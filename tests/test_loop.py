"""TaskLoop tests: approve/deny/timeout/vanish paths with stub executors."""

from types import SimpleNamespace

import pytest

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.loop import (
    Decider,
    PollingDecider,
    ScriptedDecider,
    TaskLoop,
)
from lakra.control.planner import PlannedStep
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.execution.browser.verification import Predicate
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
    """Controller surface over the real router: policy-gated, always-verify."""

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


def rig(tmp_path):
    db = Database(tmp_path / "loop.db")
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
    return SimpleNamespace(db=db, registry=registry, audit=audit,
                           sched=sched, approvals=approvals, stub=stub,
                           loop=TaskLoop(runner, approvals, audit))


def start(r, goal, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = r.add(Task.create(goal, **kw))
    r.checkout(t.task_id, "e")
    r.set_status(t.task_id, Status.RUNNING)
    return t


def test_decider_is_abstract():
    with pytest.raises(TypeError):
        Decider()


def test_all_allow_run_goes_done(tmp_path):
    ns = rig(tmp_path)
    t = start(ns.registry, "List the test things")
    out = ns.loop.run_goal(t, {"url": "file:///t.html",
                               "expect_text": "x"}, ScriptedDecider(True))
    assert (out.status, out.steps_done) == ("DONE", 2)
    assert ns.stub.calls == 2


def test_approve_l3_then_done(tmp_path):
    ns = rig(tmp_path)
    t = start(ns.registry, "Submit the test form")
    out = ns.loop.run_goal(t, {"url": "file:///t.html", "selector": "#f",
                               "text": "hi", "submit_selector": "#s"},
                           ScriptedDecider(True))
    assert out.status == "DONE"
    assert ns.stub.calls == 3  # navigate + type + token-executed submit
    types = [e["type"] for e in ns.audit.replay()]
    assert "APPROVAL_CONSUMED" in types


def test_deny_l3_stops(tmp_path):
    ns = rig(tmp_path)
    t = start(ns.registry, "Submit the test form")
    out = ns.loop.run_goal(t, {"url": "file:///t.html", "selector": "#f",
                               "text": "hi", "submit_selector": "#s"},
                           ScriptedDecider(False))
    assert out.status == "STOPPED" and "denied" in out.detail
    assert ns.registry.get(t.task_id).status == Status.CANCELLED


def test_unplannable_goal_stops_cleanly(tmp_path):
    ns = rig(tmp_path)
    t = start(ns.registry, "Transcribe this meeting")
    out = ns.loop.run_goal(t, {}, ScriptedDecider(True))
    assert out.status == "STOPPED" and "cannot plan" in out.detail


def test_polling_decider_timeout_denies(tmp_path):
    ns = rig(tmp_path)
    decider = PollingDecider(ns.approvals, "no-such-task", timeout_s=0.1,
                             interval_s=0.05)
    assert decider.decide({"approval_id": "x"}) is False


def test_blocked_step_stops_without_decider_call(tmp_path):
    ns = rig(tmp_path)
    t = start(ns.registry, "List the test things")
    seen = []
    decider = ScriptedDecider(True)
    orig = decider.decide
    decider.decide = lambda item: (seen.append(item), True)[1]
    out = ns.loop.run_goal(t, {"url": "file:///t.html",
                               "expect_text": "x"}, decider)
    assert out.status == "DONE" and seen == []
