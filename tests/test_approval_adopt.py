"""Slice-37: adopt externally-settled approvals.

Approvals.adopt() reads shared truth without re-deciding; run_plan()
adopts after the decider so an approve.py settlement is consumed
through the unchanged mint -> redeem -> burn path (exactly-once, L3
re-checks intact) and an external denial maps to the identical clean
deny outcome. TaskRegistry.refresh() resyncs the bound task so
redeem()'s RUNNING re-check reads shared truth, not a stale pre-park
cache. No CAS/persistence/audit changes; local first-decider paths
byte-identical."""

import pytest

from lakra.control.approvals import ApprovalError, Approvals
from lakra.control.audit import AuditLog
from lakra.control.loop import Decider, ScriptedDecider, TaskLoop
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")


class Stub:
    def __init__(self):
        self.calls = 0

    def execute(self, action, task):
        self.calls += 1
        return ExecuteResult(ok=True, output="did")


class FakeController:
    def __init__(self, router):
        self.router = router

    def run_step(self, task_id, step):
        res = self.router.route(task_id, step.action)
        if not res.ok:
            if (res.error or "").startswith("PARKED"):
                return StepResult(False, False, 1, "ESCALATE", "ask-pending")
            if (res.error or "").startswith("BLOCKED"):
                return StepResult(False, False, 1, "ESCALATE", "blocked")
            return StepResult(False, False, 1, "ESCALATE", "router-refused")
        return StepResult(True, True, 1, "CONTINUE", "verified")


class VanishingController(FakeController):
    """Deletes the parked approval row: the settle that never was."""

    def __init__(self, router, db):
        super().__init__(router)
        self.db = db

    def run_step(self, task_id, step):
        res = super().run_step(task_id, step)
        if res.detail == "ask-pending":
            self.db.execute("DELETE FROM approvals")
            self.db.commit()
        return res


class ExternalDecider(Decider):
    """Deterministic cross-process stand-in: settles through a FRESH
    second-process view (new registry + Approvals on the same database,
    exactly like an approve.py invocation starting after the park),
    then reports the outcome like PollingDecider would."""

    def __init__(self, ns, answer):
        self.ns = ns
        self.answer = answer
        self.seen = []

    def decide(self, item):
        self.seen.append(item)
        registry2 = TaskRegistry(store=self.ns["db"])
        registry2.load_all()
        Approvals(registry2, store=self.ns["db"]).decide(
            item["approval_id"], self.answer)
        return self.answer


def rig(tmp_path):
    db = Database(tmp_path / "adopt.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    stub = Stub()
    for kind in KINDS:
        tools.register(kind, stub)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    runner = Runner(FakeController(router), sched, audit, store=db)
    return {"db": db, "registry": registry, "audit": audit,
            "sched": sched, "approvals": approvals, "stub": stub,
            "loop": TaskLoop(runner, approvals, audit)}


def second_pair(ns):
    """Second process view on the same database (approve.py pattern)."""
    registry2 = TaskRegistry(store=ns["db"])
    registry2.load_all()
    return registry2, Approvals(registry2, store=ns["db"])


def start(registry, goal="Submit the test form", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = registry.add(Task.create(goal, **kw))
    registry.checkout(t.task_id, "e")
    registry.set_status(t.task_id, Status.RUNNING)
    return t


def submit_hints():
    return {"url": "file:///t.html", "selector": "#f", "text": "hi",
            "submit_selector": "#s"}


# -- adopt unit -------------------------------------------------------------------

def test_adopt_unit_store(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    action = Action(kind="browser.submit", target="#s",
                    effect="consequential", task_id=t.task_id)
    apv = ns["approvals"].request(t.task_id, action)
    with pytest.raises(ApprovalError):
        ns["approvals"].adopt(apv.approval_id)  # still pending: decide instead
    # Second process view built after the park (approve.py pattern).
    _, approvals2 = second_pair(ns)
    approvals2.decide(apv.approval_id, True)  # the other process wins CAS
    adopted = ns["approvals"].adopt(apv.approval_id)
    assert adopted.decided == "approved"
    assert adopted.approval_id == apv.approval_id
    # Re-deciding stays forbidden even after adoption.
    with pytest.raises(ApprovalError):
        ns["approvals"].decide(apv.approval_id, True)

    t2 = start(ns["registry"])
    action2 = Action(kind="browser.submit", target="#s",
                     effect="consequential", task_id=t2.task_id)
    apv2 = ns["approvals"].request(t2.task_id, action2)
    _, approvals2b = second_pair(ns)
    approvals2b.decide(apv2.approval_id, False)
    assert ns["approvals"].adopt(apv2.approval_id).decided == "denied"

    with pytest.raises(ApprovalError):
        ns["approvals"].adopt("no-such-approval")


def test_adopt_unit_memory():
    registry = TaskRegistry()
    t = start(registry)
    ap = Approvals(registry)
    action = Action(kind="browser.submit", target="#s",
                    effect="consequential", task_id=t.task_id)
    apv = ap.request(t.task_id, action)
    with pytest.raises(ApprovalError):
        ap.adopt(apv.approval_id)
    ap.decide(apv.approval_id, True)
    assert ap.adopt(apv.approval_id).decided == "approved"
    with pytest.raises(ApprovalError):
        ap.adopt("no-such-approval")


# -- deterministic races (the Slice-36 crash, replayed) ------------------------------

def test_race_external_approve_completes(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    out = ns["loop"].run_goal(t, submit_hints(),
                              ExternalDecider(ns, True))
    assert out.status == "DONE"
    assert ns["stub"].calls == 3  # navigate + type + adopted approved submit
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("APPROVAL_CONSUMED") == 1  # exactly once
    assert "APPROVED_STEP_EXECUTED" in types
    assert "PLAN_OUTCOME" in types
    assert not [x for x in types if x not in
                ("PLAN_CREATED", "TOOL_SELECTED", "ACTION_ALLOWED",
                 "EXECUTION_STARTED", "EXECUTION_COMPLETED",
                 "ACTION_REQUIRES_APPROVAL", "APPROVAL_CONSUMED",
                 "APPROVED_STEP_EXECUTED", "PLAN_OUTCOME",
                 "TASK_LIFECYCLE", "GUARD_TRIP")]


def test_race_external_deny_stops_clean(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    out = ns["loop"].run_goal(t, submit_hints(),
                              ExternalDecider(ns, False))
    assert out.status == "STOPPED" and "denied" in out.detail
    assert ns["registry"].get(t.task_id).status == Status.CANCELLED
    mine = [e for e in ns["audit"].replay()
            if e["task_id"] == t.task_id]
    assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]


# -- exactly-once under double adoption -----------------------------------------------

def test_double_adopt_exactly_once(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    action = Action(kind="browser.submit", target="#s",
                    effect="consequential", task_id=t.task_id)
    apv = ns["approvals"].request(t.task_id, action)
    _, approvals2 = second_pair(ns)
    approvals2.decide(apv.approval_id, True)
    adopted = ns["approvals"].adopt(apv.approval_id)
    assert adopted.decided == "approved"
    # The waiter must see shared task truth before redeem's re-check.
    ns["registry"].refresh(t.task_id)
    token1 = ns["approvals"].mint_token(apv.approval_id)
    redeemed = ns["approvals"].redeem(token1.token_id, action)
    assert redeemed.approval_id == apv.approval_id
    ns["approvals"].burn(token1.token_id)
    # Idempotent mint returns the same burned token; second consume
    # fails closed on both redeem and burn.
    assert ns["approvals"].mint_token(apv.approval_id).token_id == \
        token1.token_id
    with pytest.raises(ApprovalError):
        ns["approvals"].redeem(token1.token_id, action)
    with pytest.raises(ApprovalError):
        ns["approvals"].burn(token1.token_id)


# -- preserved paths ------------------------------------------------------------------

def test_vanished_row_still_shaped(tmp_path):
    ns = rig(tmp_path)
    ns["loop"].runner.controller = VanishingController(
        ns["loop"].runner.controller.router, ns["db"])
    t = start(ns["registry"])
    decider = ScriptedDecider(True)
    out = ns["loop"].run_goal(t, submit_hints(), decider)
    assert out.status == "STOPPED"
    assert "approval vanished before decision" in out.detail
    assert decider.seen == []  # never reached a human


def test_local_paths_unchanged(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    out = ns["loop"].run_goal(t, submit_hints(), ScriptedDecider(True))
    assert out.status == "DONE"
    t2 = start(ns["registry"])
    out = ns["loop"].run_goal(t2, submit_hints(), ScriptedDecider(False))
    assert out.status == "STOPPED" and "denied" in out.detail
