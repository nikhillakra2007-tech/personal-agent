"""Slice-04: isolated browser sessions (read-only slice).

Exactly one persistent Chromium context rooted at a Lakra-owned profile
directory — never the user's Chrome/Edge profile. Headless only. No typing,
no clicking, no downloads (accept_downloads=False).

Lifecycle: launch() -> new_page(url) -> ... -> close(). close() is
idempotent and kills all child processes. Any use after close() raises
instead of relaunching silently.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright


class SessionError(RuntimeError):
    """Launched-out-of-order use, or a browser-level failure."""


class BrowserSessions:
    # One Playwright driver per process: the sync API forbids a second
    # start() in the same thread, so instances share it with a refcount.
    # Isolation lives in separate contexts + profile dirs, NOT in drivers.
    _shared_pw = None
    _users = 0

    def __init__(self, profile_dir: str | Path) -> None:
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._context: BrowserContext | None = None
        self._closed = False
        self._holds_driver = False

    def launch(self) -> BrowserContext:
        if self._closed:
            raise SessionError("session closed; create a new BrowserSessions")
        if self._context is not None:
            return self._context
        if BrowserSessions._shared_pw is None:
            BrowserSessions._shared_pw = sync_playwright().start()
        BrowserSessions._users += 1
        self._holds_driver = True
        try:
            self._context = BrowserSessions._shared_pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=True,
                accept_downloads=False,
            )
        except Exception:
            self._release_driver()
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

    def __enter__(self) -> BrowserSessions:
        self.launch()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
