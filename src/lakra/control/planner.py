"""Slice-09: deterministic planner v0 (no model calls — see below).

Turns a Task goal + hints into a Plan: ordered PlannedSteps, each an
(Action, Predicate, retries) plus a human-readable rationale. Dispatch is
template matching on goal keywords; anything unrecognized raises
UnknownGoalError instead of hallucinating steps. Rationales are fixed
template strings filled with concrete values — deterministic including
rationale generation, per the approved amendment. No ModelProvider use.

Templates (first keyword match wins):
  follow-link — goal has follow|open link|details of|read more — needs
                   hints url + link_text + expect_text. Steps: navigate
                   (url) → snapshot (link present) → click link (arrival
                   proved by detail text) → snapshot (detail recorded).
                   Listed FIRST: its multi-word keys are the most
                   specific, so plain list/click/submit goals never
                   re-route (proven by the unchanged-behavior tests).
  observe/list  — goal has list|observ|snapshot|show — needs hints url +
                   expect_text. Steps: navigate(url) expect url_is(url);
                   snapshot expect text_contains(expect_text).
  click/toggle  — goal has click|toggle|press — needs selector (+ optional
                  expect_text, default: selector itself). Steps: single
                  click step.
  fill-submit   — goal has submit|fill|complete|send — needs url,
                   submit_selector, plus EITHER selector+text (single field)
                   OR fields (multi, slice-23). Steps end AT the L3 gate
                   (type(s), then submit which will ASK); never past it.
                   Presence of "fields" selects the multi template
                   (documented tie-break; single-field plans are
                   byte-identical to before).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from ..execution.browser.controller import BrowserStep
from ..execution.browser.verification import Predicate
from .policy import Action
from .tasks import Task

# Mirrors analyzer.URL_RE (kept local: analyzer imports this module, so
# importing it here would be circular). URL text is addressing, not
# intent: template routing classifies the goal prose with URLs removed
# so a path segment can never vote for a template.
_URL_RE = re.compile(r"(https?://\S+|file://\S+)")


class UnknownGoalError(ValueError):
    """No template matches, or required hints are missing. Escalate to ASK."""


# Hint schema (slice-11): closed key set, capped values, never credentials.
# Hints travel with planning requests and persist with plans so a replan or
# a restart has context. Anything shaped like a secret is rejected outright
# (word-boundary match, so "secretary" passes and "my password" does not).
HINT_KEYS = frozenset({"url", "expect_text", "selector", "text",
                       "submit_selector", "achieved", "fields",
                       "link_text", "table_text", "dest_path",
                       "src_path"})
# "table_text" (V2-04) rides table-road plans so persisted hints name
# the grounded description (chain status labels the road honestly);
# "dest_path"/"src_path" (V2-05) do the same for transfer roads.
# The generic non-empty/capped/secret-shaped gate below covers all.
HINT_VALUE_CAP = 2000
SECRET_WORDS = frozenset({"password", "passwd", "secret", "api_key",
                          "apikey"})

# Slice-23: multi-field forms carry a capped list of text entries
# ({selector, text}), slice-25 check entries ({selector, checked: strict
# bool}), and — since slice-26 — select entries ({selector, select:
# strict non-empty string}). File uploads, multi-selects, custom-widget
# dropdowns, and tri-state/indeterminate controls are NOT represented:
# anything beyond these three entry shapes is refused, not approximated.
MAX_FIELDS = 8


def _secret_shaped(value: str) -> bool:
    words = set(value.lower().replace("=", " ").replace(":", " ")
                .replace("/", " ").split())
    return bool(words & SECRET_WORDS)


def validate_hints(hints: dict) -> dict:
    """Closed-world validation. Returns the hints unchanged if clean."""
    if not isinstance(hints, dict):
        raise UnknownGoalError("hints must be an object")
    for key, value in hints.items():
        if key not in HINT_KEYS:
            raise UnknownGoalError(f"unknown hint {key!r}")
        if key == "achieved":
            if (not isinstance(value, list)
                    or not all(isinstance(v, str) for v in value)):
                raise UnknownGoalError("achieved must be a string list")
            continue
        if key == "fields":
            if (not isinstance(value, list) or not value
                    or len(value) > MAX_FIELDS):
                raise UnknownGoalError(
                    f"fields must be 1..{MAX_FIELDS} entries")
            for entry in value:
                if not isinstance(entry, dict):
                    raise UnknownGoalError(
                        "each field must be {selector, text},"
                        " {selector, checked}, or {selector, select}")
                keys = set(entry)
                if keys == {"selector", "checked"}:
                    sel = entry["selector"]
                    if (not isinstance(sel, str) or not sel
                            or len(sel) > HINT_VALUE_CAP):
                        raise UnknownGoalError(
                            "field 'selector' must be a non-empty string")
                    if _secret_shaped(sel):
                        raise UnknownGoalError(
                            "field 'selector' looks secret-shaped: rejected")
                    if type(entry["checked"]) is not bool:
                        raise UnknownGoalError(
                            "field 'checked' must be true or false"
                            " (strict boolean)")
                    continue
                if keys == {"selector", "select"}:
                    sel, want = entry["selector"], entry["select"]
                    if (not isinstance(sel, str) or not sel
                            or len(sel) > HINT_VALUE_CAP):
                        raise UnknownGoalError(
                            "field 'selector' must be a non-empty string")
                    if _secret_shaped(sel):
                        raise UnknownGoalError(
                            "field 'selector' looks secret-shaped: rejected")
                    if (not isinstance(want, str) or not want.strip()
                            or len(want) > HINT_VALUE_CAP):
                        raise UnknownGoalError(
                            "field 'select' must be a non-empty string")
                    if _secret_shaped(want):
                        raise UnknownGoalError(
                            "field 'select' looks secret-shaped: rejected")
                    continue
                if keys != {"selector", "text"}:
                    raise UnknownGoalError(
                        "each field must be {selector, text},"
                        " {selector, checked}, or {selector, select}")
                for part in ("selector", "text"):
                    text = entry[part]
                    if (not isinstance(text, str) or not text
                            or len(text) > HINT_VALUE_CAP):
                        raise UnknownGoalError(
                            f"field {part!r} must be a non-empty string")
                    if _secret_shaped(text):
                        raise UnknownGoalError(
                            f"field {part!r} looks secret-shaped: rejected")
            continue
        if not isinstance(value, str) or not value:
            raise UnknownGoalError(f"hint {key!r} must be a non-empty string")
        if len(value) > HINT_VALUE_CAP:
            raise UnknownGoalError(f"hint {key!r} over cap")
        words = set(value.lower().replace("=", " ").replace(":", " ")
                    .replace("/", " ").split())
        if words & SECRET_WORDS:
            raise UnknownGoalError(
                f"hint {key!r} looks secret-shaped: rejected")
    return hints


@dataclass
class PlannedStep:
    action: Action
    expect: Predicate
    max_retries: int
    rationale: str

    def to_browser_step(self) -> BrowserStep:
        return BrowserStep(action=self.action, expect=self.expect,
                           max_retries=self.max_retries)


@dataclass
class Plan:
    plan_id: str
    task_id: str
    goal: str
    steps: list[PlannedStep] = field(default_factory=list)
    created_at: str = ""
    status: str = "DRAFT"  # DRAFT|EXECUTING|DONE|STOPPED


def _need(hints: dict, *keys: str) -> None:
    missing = [k for k in keys if not hints.get(k)]
    if missing:
        raise UnknownGoalError(f"missing hints: {', '.join(missing)}")


def _follow_link_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    """Slice-27: cross one link to a detail page. The click resolves
    through the normal fallback chain (link text hits the text rung);
    arrival is proved by the detail text on fresh state, and the closing
    snapshot records the detail page. No new predicates, no new policy."""
    _need(hints, "url", "link_text", "expect_text")
    url, link, text = hints["url"], hints["link_text"], hints["expect_text"]
    return [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so observation starts from a known page"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task_id),
            expect=Predicate(kind="text_contains", target=link),
            max_retries=1,
            rationale=f"confirm the page links {link!r} before following"),
        PlannedStep(
            action=Action(kind="browser.click", target=link,
                          effect="reversible", task_id=task_id),
            expect=Predicate(kind="text_contains", target=text),
            max_retries=2,
            rationale=f"follow {link!r} and confirm {text!r} appears"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task_id),
            expect=Predicate(kind="text_contains", target=text),
            max_retries=1,
            rationale=f"record the detail page mentioning {text!r}"),
    ]


def _observe_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    _need(hints, "url", "expect_text")
    url, text = hints["url"], hints["expect_text"]
    return [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so observation starts from a known page"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task_id),
            expect=Predicate(kind="text_contains", target=text),
            max_retries=1,
            rationale=f"read the page and confirm it mentions {text!r}"),
    ]


def _click_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    _need(hints, "selector")
    sel = hints["selector"]
    expect_text = hints.get("expect_text", sel)
    return [PlannedStep(
        action=Action(kind="browser.click", target=sel, effect="reversible",
                      task_id=task_id),
        expect=Predicate(kind="text_contains", target=expect_text),
        max_retries=2,
        rationale=f"click {sel} and confirm {expect_text!r} appears")]


def _submit_gate_step(task_id: str, hints: dict) -> PlannedStep:
    return PlannedStep(
        action=Action(kind="browser.submit",
                      target=hints["submit_selector"],
                      effect="consequential", task_id=task_id),
        expect=Predicate(kind="element_exists",
                         target=hints["submit_selector"]),
        max_retries=0,
        rationale="propose submission and STOP at the approval gate;"
                  " this step parks for a human, never auto-executes")


def _fill_submit_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    _need(hints, "url", "selector", "text", "submit_selector")
    url = hints["url"]
    return [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so the form is in front of us"),
        PlannedStep(
            action=Action(
                kind="browser.type",
                target=f"{hints['selector']}\n---\n{hints['text']}",
                effect="bounded-mutation", task_id=task_id),
            expect=Predicate(kind="element_visible",
                             target=hints["selector"]),
            max_retries=2,
            rationale=f"fill {hints['selector']} (field stays visible)"),
        _submit_gate_step(task_id, hints),
    ]


def _fill_multi_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    """Slice-23 multi, slice-25 mixed, slice-26 with selects: one type step
    per text field (each visibility-verified), one idempotent check step
    per check entry (each state-verified), one exact-resolution select step
    per select entry (select stays visible — no new predicate), then the
    identical L3 submit gate. Shape validation already happened in
    validate_hints; _need re-asserts presence for a direct caller."""
    _need(hints, "url", "fields", "submit_selector")
    url = hints["url"]
    steps = [PlannedStep(
        action=Action(kind="browser.navigate", target=url,
                      effect="reversible", task_id=task_id),
        expect=Predicate(kind="url_is", target=url),
        max_retries=2,
        rationale=f"open {url} so the form is in front of us")]
    for field in hints["fields"]:
        if "checked" in field:
            want = "checked" if field["checked"] else "unchecked"
            pred = ("element_checked" if field["checked"]
                    else "element_unchecked")
            steps.append(PlannedStep(
                action=Action(
                    kind="browser.check",
                    target=f"{field['selector']}\n---\n{want}",
                    effect="bounded-mutation", task_id=task_id),
                expect=Predicate(kind=pred, target=field["selector"]),
                max_retries=2,
                rationale=f"ensure {field['selector']} {want}"
                          " (no-op if already so)"))
            continue
        if "select" in field:
            steps.append(PlannedStep(
                action=Action(
                    kind="browser.select",
                    target=f"{field['selector']}\n---\n{field['select']}",
                    effect="bounded-mutation", task_id=task_id),
                expect=Predicate(kind="element_visible",
                                 target=field["selector"]),
                max_retries=2,
                rationale=f"select {field['select']!r} in"
                          f" {field['selector']} (exact option match)"))
            continue
        steps.append(PlannedStep(
            action=Action(
                kind="browser.type",
                target=f"{field['selector']}\n---\n{field['text']}",
                effect="bounded-mutation", task_id=task_id),
            expect=Predicate(kind="element_visible",
                             target=field["selector"]),
            max_retries=2,
            rationale=f"fill {field['selector']} (field stays visible)"))
    steps.append(_submit_gate_step(task_id, hints))
    return steps


def _search_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    """Slice-44 search: navigate, fill the grounded query box, propose
    the search at the identical L3 submit gate (policy unchanged: the
    submit step parks for a human, never auto-executes), then record
    the results page by its expected text. Shape validation already
    happened in validate_hints; _need re-asserts presence for a direct
    caller."""
    _need(hints, "url", "selector", "text", "submit_selector",
          "expect_text")
    url = hints["url"]
    return [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so the search page is in front of us"),
        PlannedStep(
            action=Action(
                kind="browser.type",
                target=f"{hints['selector']}\n---\n{hints['text']}",
                effect="bounded-mutation", task_id=task_id),
            expect=Predicate(kind="element_visible",
                             target=hints["selector"]),
            max_retries=2,
            rationale=f"enter the query in {hints['selector']}"
                      " (field stays visible)"),
        _submit_gate_step(task_id, hints),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task_id),
            expect=Predicate(kind="text_contains",
                             target=hints["expect_text"]),
            max_retries=1,
            rationale=f"record the results page mentioning"
                      f" {hints['expect_text']!r}"),
    ]


def _fill_steps(task_id: str, hints: dict) -> list[PlannedStep]:
    """Fill dispatcher: fields present -> multi template, else the
    original single-field template (byte-identical to slice-09)."""
    if hints.get("fields"):
        return _fill_multi_steps(task_id, hints)
    return _fill_submit_steps(task_id, hints)


TEMPLATES = (
    (("web search",), _search_steps),
    (("follow", "open link", "details of", "read more"), _follow_link_steps),
    (("submit", "fill", "complete", "send"), _fill_steps),
    (("click", "toggle", "press"), _click_steps),
    (("list", "observ", "snapshot", "show"), _observe_steps),
)


class Planner:
    def plan(self, task: Task, hints: dict | None = None) -> Plan:
        hints = validate_hints(dict(hints or {}))
        goal = _URL_RE.sub(" ", task.goal.lower())
        for keywords, factory in TEMPLATES:
            if any(k in goal for k in keywords):
                steps = factory(task.task_id, hints or {})
                return Plan(
                    plan_id=uuid4().hex,
                    task_id=task.task_id,
                    goal=task.goal,
                    steps=steps,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    status="DRAFT",
                )
        raise UnknownGoalError(f"no template matches goal {task.goal!r}")
