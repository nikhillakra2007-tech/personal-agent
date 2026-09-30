"""Model-replan tests: lineage, fallback, no-repeat enforcement by gates."""

import json

from lakra.control.budgets import TokenLedger
from lakra.control.planner import Planner
from lakra.control.proposing import Attempt, ProposingPlanner
from lakra.control.tasks import Task
from lakra.models.base import ModelProvider, ModelResponse


class FakeProvider(ModelProvider):
    def __init__(self, mode="valid"):
        self.mode = mode
        self.calls = 0
        self.prompts = []

    @property
    def name(self):
        return "test/fake-replan"

    def complete(self, prompt, *, budget_tokens):
        self.calls += 1
        self.prompts.append(prompt)
        if self.mode == "valid":
            text = json.dumps({
                "tool_kind": "browser.snapshot",
                "target": "https://example.com",
                "effect": "read",
                "expect_kind": "text_contains",
                "expect_target": "Example",
                "rationale": "second look"})
        elif self.mode == "same-target":
            text = json.dumps({
                "tool_kind": "browser.click", "target": "#toggle",
                "effect": "reversible", "expect_kind": "text_contains",
                "expect_target": "x", "rationale": "repeat"})
        else:
            text = "nope, just vibes"
        return ModelResponse(text=text, prompt_tokens=4,
                             completion_tokens=len(text))


def task():
    return Task.create("List the demo courses", allowed_tools=["browser"],
                       allowed_domains=["example.com"])


def hints():
    return {"url": "file:///c.html", "expect_text": "pending"}


def test_replan_returns_new_plan_with_lineage():
    pp = ProposingPlanner(Planner(), FakeProvider("valid"))
    parent = Attempt(model_name="test/fake-replan")
    plan, attempt = pp.replan(
        task(), "page snapshot", hints(),
        {"kind": "browser.click", "effect": "reversible"}, parent=parent)
    assert plan is not None and plan.plan_id.endswith("-m")
    assert (attempt.parsed, attempt.verdict,
            attempt.fallback_used) == (True, "ALLOW", False)
    assert attempt.parent_attempt_id == parent.attempt_id
    assert attempt.attempt_id != parent.attempt_id


def test_replan_prompt_names_failed_target():
    prov = FakeProvider("valid")
    pp = ProposingPlanner(Planner(), prov)
    pp.replan(task(), "snap", hints(),
              {"kind": "browser.click", "effect": "reversible"})
    assert "DIFFERENT" in prov.prompts[0]
    assert "browser.click" in prov.prompts[0]


def test_replan_failure_returns_none_for_template_path():
    pp = ProposingPlanner(Planner(), FakeProvider("prose"))
    plan, attempt = pp.replan(task(), "snap", hints(),
                              {"kind": "x", "effect": "y"})
    assert plan is None and attempt.fallback_used


def test_replan_respects_ledger():
    pp = ProposingPlanner(Planner(), FakeProvider("valid"),
                          ledger=TokenLedger(0))
    plan, attempt = pp.replan(task(), "snap", hints(),
                              {"kind": "x", "effect": "y"})
    assert plan is None and "provider" in attempt.rejection_reason


def test_no_learning_api_on_proposer():
    for name in ("learn", "tune", "adjust", "remember", "train",
                 "update_prompts", "update_hints"):
        assert not hasattr(ProposingPlanner, name)
