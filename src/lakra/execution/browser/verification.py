"""Slice-05: deterministic verification predicates.

Each predicate is checked against FRESH page state, after the action. A
passing execution never implies a passing predicate: check() returns a plain
bool, and the controller audits VERIFICATION PASS/FAIL separately from
EXECUTION_COMPLETED.

Predicates (target forms):
  element_exists:  CSS selector (count >= 1)
  element_visible: CSS selector (first match visible)
  element_checked:   CSS selector (first match reports checked)
  element_unchecked: CSS selector (first match reports unchecked; a
                     missing or uncheckable target reads False, never True)
  text_contains:   literal text (in body text)
  url_is:          exact URL
  url_contains:   URL substring
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Predicate:
    kind: str  # element_exists|element_visible|element_checked|
               # element_unchecked|text_contains|url_is|url_contains
    target: str


PREDICATES = frozenset({
    "element_exists", "element_visible", "element_checked",
    "element_unchecked", "text_contains",
    "url_is", "url_contains",
})

# Diff predicates compare against a previous SnapshotRecord. They REQUIRE
# a baseline: with previous=None they raise MissingBaselineError — never
# silently degrade to absolute checks (approved slice-16 clarification).
DIFF_PREDICATES = frozenset({
    "text_appeared", "text_disappeared",
    "count_increased", "count_decreased",
    "url_changed",
})

ALL_PREDICATES = PREDICATES | DIFF_PREDICATES


class MissingBaselineError(RuntimeError):
    """Diff predicate invoked without a baseline snapshot."""


def check(predicate: Predicate, page, previous=None) -> bool:
    """Evaluate against live page state (+ baseline for diff kinds).
    Never raises except MissingBaselineError; all else is False."""
    try:
        kind, target = predicate.kind, predicate.target
        if kind in DIFF_PREDICATES:
            if previous is None:
                raise MissingBaselineError(
                    f"{kind} requires a baseline snapshot")
            if kind == "text_appeared":
                return target not in (previous.text or "") \
                    and target in (page.locator("body").inner_text() or "")
            if kind == "text_disappeared":
                return target in (previous.text or "") \
                    and target not in (page.locator("body").inner_text()
                                       or "")
            if kind == "count_increased":
                return page.locator(target).count() > \
                    previous.counts.get(target, 0)
            if kind == "count_decreased":
                return page.locator(target).count() < \
                    previous.counts.get(target, 0)
            if kind == "url_changed":
                return page.url != (previous.url or "")
        if kind == "element_exists":
            return page.locator(target).count() >= 1
        if kind == "element_visible":
            return page.locator(target).first.is_visible()
        if kind == "element_checked":
            return page.locator(target).first.is_checked() is True
        if kind == "element_unchecked":
            return page.locator(target).first.is_checked() is False
        if kind == "text_contains":
            return target in (page.locator("body").inner_text() or "")
        if kind == "url_is":
            return page.url == target
        if kind == "url_contains":
            return target in page.url
    except MissingBaselineError:
        raise
    except Exception:
        return False
    return False
    return False
