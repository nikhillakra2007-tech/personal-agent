"""Diff predicate tests: past-tense checks, explicit baselines, store caps."""

from pathlib import Path

import pytest

from lakra.execution.browser.snapshots import (
    SnapshotRecord,
    SnapshotStore,
)
from lakra.execution.browser.verification import (
    ALL_PREDICATES,
    DIFF_PREDICATES,
    MissingBaselineError,
    Predicate,
    check,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


@pytest.fixture
def page(tmp_path):
    from lakra.execution.browser.actions import BrowserActions
    from lakra.execution.browser.sessions import BrowserSessions
    s = BrowserSessions(tmp_path / "p")
    s.launch()
    h = BrowserActions(s)
    h.open(FIXTURE)
    yield h.page
    h.close_page()
    s.close()


def before(text="nothing here", url="file:///before.html", counts=None):
    return SnapshotRecord(url=url, text=text, counts=counts or {})


def test_appeared_disappeared(page):
    assert check(Predicate("text_appeared", "Linear Algebra"),
                 page, before("nothing here"))
    assert not check(Predicate("text_appeared", "nothing here"),
                     page, before("nothing here"))
    assert check(Predicate("text_disappeared", "gone text"),
                 page, before("gone text and more"))
    assert not check(Predicate("text_disappeared", "Linear Algebra"),
                     page, before("Linear Algebra"))


def test_count_changed(page):
    assert check(Predicate("count_increased", ".course-card"),
                 page, before("", counts={".course-card": 1}))
    assert not check(Predicate("count_increased", ".course-card"),
                     page, before("", counts={".course-card": 99}))
    assert check(Predicate("count_decreased", ".course-card"),
                 page, before("", counts={".course-card": 99}))
    assert not check(Predicate("count_decreased", ".course-card"),
                     page, before("", counts={".course-card": 0}))


def test_url_changed(page):
    assert check(Predicate("url_changed", ""), page,
                 before("", url="file:///other.html"))
    assert not check(Predicate("url_changed", ""), page,
                     before("", url=page.url))


def test_missing_baseline_raises_never_silent(page):
    for kind in DIFF_PREDICATES:
        with pytest.raises(MissingBaselineError):
            check(Predicate(kind, "x"), page, None)
        with pytest.raises(MissingBaselineError):
            check(Predicate(kind, "x"), page)


def test_absolute_predicates_ignore_baseline(page):
    assert check(Predicate("text_contains", "Linear Algebra"),
                 page, before("unrelated"))
    assert check(Predicate("url_is", page.url), page, before())


def test_predicate_sets():
    assert DIFF_PREDICATES == {"text_appeared", "text_disappeared",
                               "count_increased", "count_decreased",
                               "url_changed"}
    assert len(ALL_PREDICATES) == 13  # 8 absolute (incl. slice-25
                                # checked/unchecked and V2-05
                                # file_nonempty) + 5 diff


def test_store_cap_and_latest():
    store = SnapshotStore(cap=3)
    for i in range(5):
        store.record("t", SnapshotRecord(url="u", text=f"t{i}"))
    assert store.depth("t") == 3
    assert store.latest("t").text == "t4"
    assert store.latest("nobody") is None


def test_stored_text_holds_no_secrets(page):
    from lakra.execution.browser.snapshots import SnapshotRecord as SR
    text = page.locator("body").inner_text() or ""
    assert "s3cr3t-nope" not in text
    rec = SR(url=page.url, text=text)
    assert "s3cr3t-nope" not in rec.text


def test_controller_audits_no_baseline_explicitly(page, tmp_path):
    from lakra.control.audit import AuditLog
    from lakra.execution.browser.controller import BrowserController
    from lakra.execution.browser.controller import BrowserStep
    from lakra.control.policy import Action

    class FakeActions:
        def __init__(self, page):
            self._page = page

        @property
        def page(self):
            return self._page

    audit = AuditLog(tmp_path / "a.jsonl")
    ctrl = BrowserController(router=None, actions=FakeActions(page),
                             audit=audit)
    step = BrowserStep(
        action=Action(kind="browser.click", target="#toggle",
                      effect="reversible", task_id="t"),
        expect=Predicate(kind="text_appeared", target="clicked"))
    assert ctrl._verify("t", step, None) is False
    events = audit.replay()
    assert len(events) == 1 and events[0]["payload"]["result"] == \
        "NO_BASELINE"
    assert ctrl._verify("t", step, before("nothing here")) in (True, False)
    assert audit.replay()[-1]["payload"]["result"] in ("PASS", "FAIL")
