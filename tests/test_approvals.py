"""Approval tests: scoping, resume/deny paths, mandatory recheck, no bypass."""

import pytest

from lakra.control.approvals import ApprovalError, Approvals
from lakra.control.policy import ASK, Action
from lakra.control.registry import TaskRegistry
from lakra.control.tasks import Status, Task


def setup():
    r = TaskRegistry()
    t = r.add(Task.create("submit assignment", allowed_tools=["browser"],
                          allowed_domains=["portal.example.edu"]))
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    return r, Approvals(r), t


def action_for(t):
    return Action(kind="browser.submit",
                  target="https://portal.example.edu/a/1",
                  effect="consequential", task_id=t.task_id)


def test_ask_parks_task_in_waiting():
    r, ap, t = setup()
    ap.request(t.task_id, action_for(t))
    assert r.get(t.task_id).status == Status.WAITING_APPROVAL
    assert r.get(t.task_id).owner == "exec"  # ownership retained


def test_approve_resumes_and_recheck_allows():
    r, ap, t = setup()
    apv = ap.request(t.task_id, action_for(t))
    decided = ap.decide(apv.approval_id, True)
    assert decided.decided == "approved"
    assert r.get(t.task_id).status == Status.RUNNING
    # L3-kind actions stay ASK after approval: the approval token (not a
    # verdict flip) is what authorizes this one execution.
    assert ap.recheck(decided) == ASK
    ap.must_be_gated(ap.recheck(decided))  # must not raise


def test_deny_cancels_without_retry():
    r, ap, t = setup()
    apv = ap.request(t.task_id, action_for(t))
    ap.decide(apv.approval_id, False)
    assert r.get(t.task_id).status == Status.CANCELLED
    with pytest.raises(ApprovalError):
        ap.decide(apv.approval_id, True)  # settled; no second decision


def test_approval_scoped_to_its_task():
    r, ap, t = setup()
    other = r.add(Task.create("other", allowed_tools=["browser"],
                              allowed_domains=["portal.example.edu"]))
    with pytest.raises(ApprovalError):
        ap.request(other.task_id, action_for(t))  # action bound to t


def test_recheck_of_changed_world_blocks():
    r, ap, t = setup()
    apv = ap.request(t.task_id, action_for(t))
    ap.decide(apv.approval_id, True)
    # Target blocklisted between approval and execution: recheck flips to
    # BLOCK and the gate refuses.
    verdict = ap.recheck(
        apv, deny_targets=frozenset({"portal.example.edu"}))
    assert verdict == "BLOCK"
    with pytest.raises(ApprovalError):
        ap.must_be_gated(verdict)


def test_no_global_bypass_api():
    assert not hasattr(Approvals, "approve_all")
    assert not hasattr(Approvals, "approve_task")
