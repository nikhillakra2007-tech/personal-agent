"""Slice-04: isolated browser sessions (read-only slice).

Exactly one persistent Chromium context rooted at a Lakra-owned profile
directory — never the user's Chrome/Edge profile. Headless by default
(no typing, no clicking, no downloads (accept_downloads=False));
opt-in headed (headless=False) for hosts that bot-wall headless
renderers. The flag alone changes visibility, never policy: every
action still routes through the same guards/router/approvals.

V2-05: an explicit opt-in (accept_downloads=True, only for executions
that may run a sanctioned browser.download action) lets the context
accept downloads. The default stays False everywhere else, and the
flag alone transfers nothing: every download still parks at its own
L3 gate and lands sandbox-confined through the download executor.

V2-06: an explicit opt-in (exclusive=True, only for named session
profiles) takes a non-blocking OS lock on the profile directory for
the session's lifetime. Chromium does not serialize concurrent
persistent contexts on one directory in this configuration, so
Lakra refuses the second holder instead of silently merging state.
OS locks die with the process: a crash never leaves a stale lock,
and the next launch recovers. The default stays unlocked (V1
behavior identical).

Lifecycle: launch() -> new_page(url) -> ... -> close(). close() is
idempotent and kills all child processes. Any use after close() raises
instead of relaunching silently.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright


class SessionError(RuntimeError):
    """Launched-out-of-order use, or a browser-level failure."""


try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None


def _lock_bytes(fh) -> None:
    """Exclusive non-blocking lock on the first byte (V2-06). OS
    primitives on both families: the lock dies with the process, so
    a crash can never strand a profile behind a stale lock."""
    if _fcntl is not None:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return
    import msvcrt
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock_bytes(fh) -> None:
    if _fcntl is not None:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        return
    import msvcrt
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class BrowserSessions:
    # One Playwright driver per process: the sync API forbids a second
    # start() in the same thread, so instances share it with a refcount.
    # Isolation lives in separate contexts + profile dirs, NOT in drivers.
    _shared_pw = None
    _users = 0

    LOCK_FILENAME = ".lakra-profile.lock"

    # Hosts whose DNS flaps inside Chromium (router SERVFAILs
    # under its parallel A/AAAA bursts) while the OS resolver answers
    # fine. Resolved via the OS at launch and pinned with
    # --host-resolver-rules so the renderer never consults the broken
    # path. Best-effort: unresolvable hosts are skipped, never fatal.
    DNS_PIN_HOSTS = (
        "signon.oracle.com",
        "academy.oracle.com",
        "www.oracle.com",
        "education.oracle.com",
        "login-ext.identity.oraclecloud.com",
    )

    @staticmethod
    def _dns_pin_args() -> list[str]:
        import socket
        rules = []
        for host in BrowserSessions.DNS_PIN_HOSTS:
            try:
                ip = socket.getaddrinfo(host, 443, family=socket.AF_INET,
                                        type=socket.SOCK_STREAM)[0][4][0]
            except OSError:
                continue
            rules.append(f"MAP {host} {ip}")
        if not rules:
            return []
        return ["--host-resolver-rules=" + ",".join(rules)]

    def __init__(self, profile_dir: str | Path,
                 accept_downloads: bool = False,
                 exclusive: bool = False,
                 headless: bool = True) -> None:
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.accept_downloads = bool(accept_downloads)
        self.exclusive = bool(exclusive)
        self.headless = bool(headless)
        self._context: BrowserContext | None = None
        self._closed = False
        self._holds_driver = False
        self._lock_fh = None

    def _acquire_profile_lock(self) -> None:
        """Non-blocking OS lock on the profile (V2-06 exclusive mode).

        Held from launch() to close(); the OS releases it on process
        death, so crashes never strand the profile. A live holder
        elsewhere (any process, or this one) makes launch() refuse
        instead of merging state. No lockfile is created unless
        exclusive mode asked for it.
        """
        path = self.profile_dir / self.LOCK_FILENAME
        try:
            fh = open(path, "a+b")
        except OSError as exc:
            raise SessionError(
                f"cannot guard session profile ({exc})")
        try:
            fh.write(b"\x00")
            fh.flush()
            _lock_bytes(fh)
        except OSError:
            try:
                fh.close()
            except OSError:
                pass
            raise SessionError(
                "session profile is already in use by another process;"
                " wait for it or use a different --session-profile")
        self._lock_fh = fh

    def _release_profile_lock(self) -> None:
        fh, self._lock_fh = self._lock_fh, None
        if fh is not None:
            try:
                _unlock_bytes(fh)
            except OSError:
                pass
            try:
                fh.close()
            except OSError:
                pass

    def launch(self) -> BrowserContext:
        if self._closed:
            raise SessionError("session closed; create a new BrowserSessions")
        if self._context is not None:
            return self._context
        if self.exclusive and self._lock_fh is None:
            self._acquire_profile_lock()
        if BrowserSessions._shared_pw is None:
            BrowserSessions._shared_pw = sync_playwright().start()
        BrowserSessions._users += 1
        self._holds_driver = True
        try:
            self._context = BrowserSessions._shared_pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
                accept_downloads=self.accept_downloads,
                args=self._dns_pin_args(),
            )
        except Exception:
            self._release_driver()
            self._release_profile_lock()
            raise
        return self._context

    def _require_context(self) -> BrowserContext:
        if self._context is None:
            raise SessionError("launch() first")
        return self._context

    def new_page(self, url: str, timeout_ms: int = 15000) -> Page:
        # "load", not "domcontentloaded": CDP consumers (snapshots) need
        # the renderer's agent host ready, which domcontentloaded does not
        # guarantee after an idle gap (found via resume-flow flake, slice-11).
        page = self._require_context().new_page()
        self._goto_with_dns_retry(page, url, timeout_ms)
        self._settle_redirects(page)
        # Post-load SSO bounces (academy -> login-ext -> back) can
        # strand on a DNS error page even when the initial goto was
        # clean. A stranded error page is never a real arrival: redo
        # the whole navigation bounded, then return whatever settled.
        for _ in range(2):
            try:
                if not page.url.startswith("chrome-error://"):
                    break
            except Exception:
                break
            self._goto_with_dns_retry(page, url, timeout_ms)
            self._settle_redirects(page)
        self._await_content(page)
        return page

    @staticmethod
    def _await_content(page: Page, timeout_ms: int = 20000) -> None:
        """Wait for the settled page to actually render body text.

        SSO-fronted pages (Oracle Academy) sit on the right URL with
        an EMPTY body while the identity bounce completes; snapshotting
        then yields nothing and text_contains fails on content that
        arrives seconds later. Polls body.inner_text until non-empty,
        capped at timeout_ms, then returns regardless — verification
        still judges the result. Fail-open: unreadable pages return
        immediately for the normal error path."""
        import time
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                if (page.locator("body").inner_text(timeout=1000) or "") \
                        .strip():
                    return
            except Exception:
                return
            time.sleep(0.5)

    @staticmethod
    def _goto_with_dns_retry(page: Page, url: str, timeout_ms: int,
                             attempts: int = 5) -> None:
        """Bounded re-goto on transient Chromium DNS failures.

        Fresh Chromium renderers on this fleet intermittently report
        ERR_NAME_NOT_RESOLVED / DNS_PROBE_STARTED for hosts the OS
        resolves fine (Oracle SSO edge); a plain re-goto recovers.
        Only DNS-shaped errors retry (same error text Playwright
        surfaces); anything else raises immediately. Last attempt
        always raises so navigation failure stays fail-closed and
        the normal EXECUTION_FAILED + verification path decides."""
        last = None
        for n in range(max(1, attempts)):
            try:
                page.goto(url, wait_until="load", timeout=timeout_ms)
                return
            except Exception as exc:
                last = exc
                msg = str(exc)
                if ("ERR_NAME_NOT_RESOLVED" not in msg
                        and "DNS_PROBE" not in msg):
                    raise
                if n == attempts - 1:
                    raise
        raise last  # pragma: no cover - loop always returns/raises

    @staticmethod
    def _settle_redirects(page: Page, stable_ms: int = 2500,
                           timeout_ms: int = 20000) -> None:
        """Wait for post-load redirect chains (SSO dances, geo-redirects)
        redirect chains (SSO dances, geo-redirects) to finish, not
        merely to pause: Oracle's signon page bounces to
        login-ext.identity.oraclecloud.com ~1-3s AFTER load and returns
        ~2s later, so a sub-second window straddles the dance and the
        exact url_is predicate fails on the transient URL. Capped at
        timeout_ms — then returns regardless. Never passes/fails
        anything itself: url_is and text_contains still verify the
        settled page exactly as before. Fail-open by design: a
        closed/unreadable page just returns and lets the normal action
        error + verification path handle it."""
        import time
        try:
            last = page.url
        except Exception:
            return
        stable_since = time.monotonic()
        deadline = stable_since + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            time.sleep(0.25)
            try:
                cur = page.url
            except Exception:
                return
            now = time.monotonic()
            if cur != last:
                last, stable_since = cur, now
            elif (now - stable_since) * 1000.0 >= stable_ms:
                return

    def _release_driver(self) -> None:
        if self._holds_driver:
            self._holds_driver = False
            BrowserSessions._users -= 1
            if BrowserSessions._users <= 0:
                BrowserSessions._users = 0
                if BrowserSessions._shared_pw is not None:
                    BrowserSessions._shared_pw.stop()
                    BrowserSessions._shared_pw = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._release_driver()
            self._release_profile_lock()

    def __enter__(self) -> BrowserSessions:
        self.launch()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
