"""Session lifecycle tests: launch, isolation, close discipline."""

import subprocess

import pytest

from lakra.execution.browser.sessions import BrowserSessions, SessionError

FIXTURE = (__import__("pathlib").Path(__file__).parent
           / "fixtures" / "courses.html").as_uri()


def _browser_procs():
    # Count ONLY Playwright's headless shell: the user's own chrome.exe
    # processes fluctuate during a run and must not be measured.
    out = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True,
                         text=True).stdout.lower()
    return out.count('"headless_shell.exe"')


def test_launch_and_navigate(tmp_path):
    with BrowserSessions(tmp_path / "p1") as s:
        page = s.new_page(FIXTURE)
        assert "courses.html" in page.url
        assert "My Courses" in page.title()
        assert len(s._context.pages) >= 1


def test_double_launch_returns_same_context(tmp_path):
    s = BrowserSessions(tmp_path / "p")
    try:
        assert s.launch() is s.launch()
    finally:
        s.close()


def test_use_after_close_raises(tmp_path):
    s = BrowserSessions(tmp_path / "p")
    s.launch()
    s.close()
    with pytest.raises(SessionError):
        s.launch()
    s.close()  # idempotent


def test_new_page_without_launch_raises(tmp_path):
    with pytest.raises(SessionError):
        BrowserSessions(tmp_path / "p").new_page(FIXTURE)


def test_profiles_are_isolated(tmp_path):
    a = BrowserSessions(tmp_path / "pa")
    b = BrowserSessions(tmp_path / "pb")
    try:
        a.launch()
        pa = a.new_page(FIXTURE)
        b.launch()
        # B starts with only the default blank page: none of A's pages leak.
        assert pa not in b._context.pages
        assert all(p.url == "about:blank" for p in b._context.pages)
        pb = b.new_page(FIXTURE)
        assert pa is not pb
        # Separate profile roots (Chromium's internal file layout is its
        # own business and is not asserted here).
        assert a.profile_dir != b.profile_dir
        assert a.profile_dir.is_dir() and b.profile_dir.is_dir()
    finally:
        a.close()
        b.close()


def test_close_releases_processes(tmp_path):
    before = _browser_procs()
    s = BrowserSessions(tmp_path / "p")
    s.launch()
    s.new_page(FIXTURE)
    assert _browser_procs() >= before
    s.close()
    assert _browser_procs() == before


def _fake_driver(monkeypatch, seen):
    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kw):
            seen.update(kw)
            seen["profile_dir"] = profile_dir

            class Ctx:
                def close(self):
                    pass

            return Ctx()

    class FakePW:
        chromium = FakeChromium()

        def stop(self):
            pass

    monkeypatch.setattr(BrowserSessions, "_shared_pw", FakePW())
    monkeypatch.setattr(BrowserSessions, "_users", 0)


def test_headless_defaults_true(tmp_path, monkeypatch):
    seen = {}
    _fake_driver(monkeypatch, seen)
    s = BrowserSessions(tmp_path / "p")
    assert s.headless is True
    try:
        s.launch()
    finally:
        s.close()
    assert seen.get("headless") is True


def test_headed_opt_in_passes_false(tmp_path, monkeypatch):
    seen = {}
    _fake_driver(monkeypatch, seen)
    s = BrowserSessions(tmp_path / "p", headless=False)
    assert s.headless is False
    try:
        s.launch()
    finally:
        s.close()
    assert seen.get("headless") is False
