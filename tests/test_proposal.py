"""Proposal adapter tests: strict shape, hostile content rejected."""

import json

import pytest

from lakra.control.policy import Action
from lakra.execution.browser.verification import Predicate
from lakra.models.proposal import (
    ProposalRejected,
    parse_proposal,
    to_action,
)


def good(**kw):
    obj = {"tool_kind": "browser.click", "target": "#toggle",
           "effect": "reversible", "expect_kind": "text_contains",
           "expect_target": "clicked", "rationale": "toggle it"}
    obj.update(kw)
    return json.dumps(obj)


def test_valid_proposal_parses():
    p = parse_proposal(good())
    assert (p.tool_kind, p.effect) == ("browser.click", "reversible")


def test_non_json_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal("click the button please")


def test_non_object_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal("[1, 2]")


def test_unknown_tool_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal(good(tool_kind="browser.hypnotize"))


def test_unknown_effect_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal(good(effect="mind-control"))


def test_empty_and_oversize_targets_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal(good(target=""))
    with pytest.raises(ProposalRejected):
        parse_proposal(good(target="x" * 5000))


def test_unknown_predicate_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal(good(expect_kind="vibes_good"))


def test_missing_fields_rejected():
    with pytest.raises(ProposalRejected):
        parse_proposal(json.dumps({"tool_kind": "browser.click"}))


def test_to_action_feeds_pipeline_types():
    action, pred = to_action(parse_proposal(good()), "task-1")
    assert isinstance(action, Action) and isinstance(pred, Predicate)
    assert (action.kind, action.task_id) == ("browser.click", "task-1")


def test_l4_proposal_parses_but_policy_will_block():
    # The adapter validates SHAPE; authorization stays policy's job.
    p = parse_proposal(good(tool_kind="browser.click", effect="blocked"))
    assert p.effect == "blocked"
