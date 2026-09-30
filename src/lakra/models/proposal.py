"""Slice-06: strict model-output adapter (untrusted text -> proposal).

parse_proposal() accepts ONLY a JSON object with exactly the Proposal shape:
known tool kinds, sane effects, capped target lengths, known predicate
kinds. Anything else -> ProposalRejected. Rejected output is audited and
escalated by the caller, never executed.

to_action() converts a validated Proposal into (Action, Predicate) for the
normal pipeline: policy STILL evaluates the result. The model never gains a
path around PolicyEngine; this module only shapes text into the schema the
pipeline already trusts callers to fill honestly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..control.policy import Action
from ..execution.browser.verification import PREDICATES, Predicate

TOOL_KINDS = frozenset({
    "browser.navigate", "browser.snapshot", "browser.screenshot",
    "browser.click", "browser.type", "browser.press",
    "browser.scroll", "browser.wait",
    "fs.read", "fs.write", "fs.list", "fs.delete",
    "terminal.run",
})

EFFECTS = frozenset({"read", "reversible", "bounded-mutation",
                     "consequential", "blocked"})

MAX_TARGET_CHARS = 2000


class ProposalRejected(ValueError):
    """Model output failed schema validation. Audit + escalate."""


@dataclass
class Proposal:
    tool_kind: str
    target: str
    effect: str
    expect_kind: str
    expect_target: str
    rationale: str = ""


def parse_proposal(text: str) -> Proposal:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalRejected(f"not JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProposalRejected("proposal must be a JSON object")
    try:
        prop = Proposal(
            tool_kind=obj["tool_kind"], target=obj["target"],
            effect=obj["effect"], expect_kind=obj["expect_kind"],
            expect_target=obj["expect_target"],
            rationale=str(obj.get("rationale", "")))
    except (KeyError, TypeError) as exc:
        raise ProposalRejected(f"missing/invalid fields: {exc}") from exc
    if prop.tool_kind not in TOOL_KINDS:
        raise ProposalRejected(f"unknown tool_kind {prop.tool_kind!r}")
    if prop.effect not in EFFECTS:
        raise ProposalRejected(f"unknown effect {prop.effect!r}")
    if not prop.target or len(prop.target) > MAX_TARGET_CHARS:
        raise ProposalRejected("target empty or over cap")
    if prop.expect_kind not in PREDICATES:
        raise ProposalRejected(f"unknown expect_kind {prop.expect_kind!r}")
    return prop


def to_action(prop: Proposal, task_id: str) -> tuple[Action, Predicate]:
    return (Action(kind=prop.tool_kind, target=prop.target,
                   effect=prop.effect, task_id=task_id),
            Predicate(kind=prop.expect_kind, target=prop.expect_target))
