"""Slice-04: isolated browser sessions (read-only slice).

Exactly one persistent Chromium context rooted at a Lakra-owned profile
directory — never the user's Chrome/Edge profile. Headless only. No typing,
no clicking, no downloads (accept_downloads=False).

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

    def __init__(self, profile_dir: str | Path,
                 accept_downloads: bool = False,
                 exclusive: bool = False) -> None:
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.accept_downloads = bool(accept_downloads)
        self.exclusive = bool(exclusive)
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
                headless=True,
                accept_downloads=self.accept_downloads,
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
        page.goto(url, wait_until="load", timeout=timeout_ms)
        return page

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
