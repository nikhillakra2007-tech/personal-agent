"""Verification predicate tests: each check against live state, both ways."""

from pathlib import Path

import pytest

from lakra.execution.browser.actions import BrowserActions
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.browser.verification import PREDICATES, Predicate, check

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


@pytest.fixture
def page(tmp_path):
    s = BrowserSessions(tmp_path / "p")
    s.launch()
    h = BrowserActions(s)
    h.open(FIXTURE)
    yield h.page
    h.close_page()
    s.close()


def test_all_predicates_true(page):
    assert check(Predicate("element_exists", "#toggle"), page)
    assert check(Predicate("element_visible", "#toggle"), page)
    assert check(Predicate("text_contains", "Linear Algebra"), page)
    assert check(Predicate("url_is", FIXTURE), page)
    assert check(Predicate("url_contains", "courses.html"), page)


def test_all_predicates_false(page):
    assert not check(Predicate("element_exists", "#ghost"), page)
    assert not check(Predicate("element_visible", "#ghost"), page)
    assert not check(Predicate("text_contains", "No Such Course"), page)
    assert not check(Predicate("url_is", "https://x.example/other"), page)
    assert not check(Predicate("url_contains", "portal.example"), page)


def test_predicate_set_matches_contract():
    assert PREDICATES == {"element_exists", "element_visible",
                          "element_checked", "element_unchecked",
                          "text_contains", "url_is", "url_contains",
                          "file_nonempty"}


def test_checked_predicates_follow_state(page):
    assert check(Predicate("element_unchecked", "#p-news"), page)
    assert not check(Predicate("element_checked", "#p-news"), page)
    page.locator("#p-news").check()
    assert check(Predicate("element_checked", "#p-news"), page)
    assert not check(Predicate("element_unchecked", "#p-news"), page)


def test_checked_predicates_never_true_without_state(page):
    assert not check(Predicate("element_checked", "#ghost"), page)
    assert not check(Predicate("element_unchecked", "#ghost"), page)
    assert not check(Predicate("element_checked", "#echo"), page)
    assert not check(Predicate("element_unchecked", "#echo"), page)


def test_check_never_raises():
    assert check(Predicate("bogus-kind", "x"), object()) is False
    assert check(Predicate("element_exists", "#x"), None) is False


def test_execution_success_is_not_verification(page):
    # Clicking toggle succeeds but proves nothing about #delayed: the
    # predicate is evaluated on fresh state, independently.
    page.locator("#toggle").click()
    assert page.locator("#status").inner_text() == "clicked"
    assert not check(Predicate("text_contains", "late-clicked"), page)
