"""ProposingPlanner tests: measured proposals, fallback, no daemon needed."""

import json

import pytest

from lakra.control.budgets import TokenLedger
from lakra.control.planner import Planner
from lakra.control.proposing import (
    Attempt,
    ProposingPlanner,
    build_prompt,
)
from lakra.control.tasks import Task
from lakra.models.base import (
    BudgetExhausted,
    ModelProvider,
    ModelResponse,
    ProviderTimeout,
)


class FakeProvider(ModelProvider):
    """Recorded stand-in: canned text per mode, usage counted honestly."""

    def __init__(self, mode="valid"):
        self.mode = mode
        self.calls = 0

    @property
    def name(self):
        return "test/fake-0.5b"

    def complete(self, prompt, *, budget_tokens):
        self.calls += 1
        if self.mode == "timeout":
            raise ProviderTimeout("slow")
        if self.mode == "valid":
            text = json.dumps({
                "tool_kind": "browser.snapshot",
                "target": "https://example.com",
                "effect": "read",
                "expect_kind": "text_contains",
                "expect_target": "Example",
                "rationale": "observe first"})
        elif self.mode == "prose":
            text = "you should click around a bit"
        elif self.mode == "hostile":
            text = json.dumps({
                "tool_kind": "browser.click", "target": "#x",
                "effect": "blocked", "expect_kind": "text_contains",
                "expect_target": "x", "rationale": "evil"})
        return ModelResponse(text=text, prompt_tokens=4,
                             completion_tokens=len(text))


def task(goal="List the demo courses"):
    return Task.create(goal, allowed_tools=["browser"],
                       allowed_domains=["example.com"])


def hints():
    return {"url": "file:///c.html", "expect_text": "pending"}


def test_valid_proposal_measured_separately():
    pp = ProposingPlanner(Planner(), FakeProvider("valid"))
    plan, attempt = pp.propose(task(), "snapshot text", hints())
    assert isinstance(attempt, Attempt)
    assert (attempt.parsed, attempt.verdict,
            attempt.fallback_used) == (True, "ALLOW", False)
    assert len(plan.steps) == 1
    assert plan.steps[0].rationale.startswith("model-proposed")
    assert attempt.calls_made == 1 and attempt.tokens_used > 0


def test_prose_falls_back_with_reason():
    pp = ProposingPlanner(Planner(), FakeProvider("prose"))
    plan, attempt = pp.propose(task(), "snap", hints())
    assert (attempt.parsed, attempt.fallback_used) == (False, True)
    assert "schema" in (attempt.rejection_reason or "")
    assert len(plan.steps) == 2  # deterministic fallback intact


def test_hostile_proposal_blocked_then_fallback():
    pp = ProposingPlanner(Planner(), FakeProvider("hostile"))
    plan, attempt = pp.propose(task(), "snap", hints())
    assert (attempt.parsed, attempt.verdict) == (True, "BLOCK")
    assert "BLOCK" in (attempt.rejection_reason or "")
    assert attempt.fallback_used and len(plan.steps) == 2


def test_provider_failure_falls_back():
    pp = ProposingPlanner(Planner(), FakeProvider("timeout"))
    plan, attempt = pp.propose(task(), "snap", hints())
    assert attempt.fallback_used and "provider" in attempt.rejection_reason
    assert len(plan.steps) == 2


def test_ledger_exhaustion_before_http():
    ledger = TokenLedger(0)
    pp = ProposingPlanner(Planner(), FakeProvider("valid"), ledger=ledger)
    plan, attempt = pp.propose(task(), "snap", hints())
    assert attempt.fallback_used and isinstance(
        attempt.rejection_reason, str)
    assert ledger.used_tokens == 0  # nothing sent, nothing charged


def test_ledger_charged_on_use():
    ledger = TokenLedger(1000)
    pp = ProposingPlanner(Planner(), FakeProvider("valid"), ledger=ledger)
    _, attempt = pp.propose(task(), "snap", hints())
    assert not attempt.fallback_used
    assert ledger.used_tokens == attempt.tokens_used > 0


def test_unknown_goal_empty_plan_but_measured():
    pp = ProposingPlanner(Planner(), FakeProvider("valid"))
    # Valid proposal for an untemplatable goal: model path still measured
    # (policy gates it later like any step); fallback would have been empty.
    plan, attempt = pp.propose(task("Transcribe this meeting"), "snap", {})
    assert attempt.parsed and not attempt.fallback_used
    assert len(plan.steps) == 1


def test_prompt_frozen_shape_and_caps():
    prompt = build_prompt("g", ["browser", "fs"], "x" * 5000)
    assert "tool_kind" in prompt and "untrusted" in prompt.lower()
    assert len(prompt) < 5000  # snapshot capped, template bounded


def test_no_scoring_or_selection_api():
    assert not hasattr(ProposingPlanner, "score")
    assert not hasattr(ProposingPlanner, "select")
    assert not hasattr(ProposingPlanner, "compete")
