"""Controller tests: full observe-act-verify loop, recovery, policy paths."""

from pathlib import Path

import pytest

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.tasks import Status, Task
from lakra.execution.browser.actions import BrowserActions
from lakra.execution.browser.controller import (
    BrowserController,
    BrowserStep,
)
from lakra.execution.browser.observer import BrowserObserver
from lakra.execution.browser.recovery import (
    recover_execution,
    recover_verification,
)
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.browser.verification import Predicate
from lakra.execution.registry import ToolRegistry
from lakra.execution.router import ToolRouter

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


from types import SimpleNamespace  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    registry = TaskRegistry()
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry)
    approvals = Approvals(registry)
    tools = ToolRegistry()
    sessions = BrowserSessions(tmp_path / "prof")
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, tmp_path / "shots")
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched)
    task = registry.add(Task.create(
        "work the fixture", allowed_tools=["browser"],
        allowed_domains=["file:"], allowed_paths=[str(tmp_path)]))
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)
    sched.enqueue(task.task_id)
    hands.open(FIXTURE)
    ns = SimpleNamespace(registry=registry, audit=audit, sched=sched,
                         approvals=approvals, tools=tools, sessions=sessions,
                         hands=hands, obs=obs, router=router, ctrl=ctrl,
                         task=task)
    yield ns
    sessions.close()


def step(kind, target, effect, expect, task_id, retries=2):
    return BrowserStep(
        action=Action(kind=kind, target=target, effect=effect,
                      task_id=task_id),
        expect=expect, max_retries=retries)


def test_full_pass_continue(ctx):
    s = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#toggle", "reversible",
             Predicate("text_contains", "clicked"), ctx.task.task_id))
    assert (s.ok, s.verified, s.outcome) == (True, True, "CONTINUE")
    assert s.attempts == 1
    types = [e["type"] for e in ctx.audit.replay()]
    assert "VERIFICATION" in types


def test_failed_verification_escalates_after_retries(ctx):
    s = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#toggle", "reversible",
             Predicate("text_contains", "never-appears"), ctx.task.task_id,
             retries=1))
    assert (s.verified, s.outcome) == (False, "ESCALATE")
    assert s.attempts == 2  # 1 initial + 1 retry, then recovery alternate
    types = [e["type"] for e in ctx.audit.replay()]
    assert "RETRY" in types and "RECOVERY" in types


def test_execution_failure_escalates(ctx):
    s = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#ghost", "reversible",
             Predicate("text_contains", "x"), ctx.task.task_id, retries=0))
    assert (s.ok, s.verified, s.outcome) == (False, False, "ESCALATE")


def test_block_escalates_without_touching(ctx):
    before = ctx.hands.page.locator("#status").inner_text()
    s = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#toggle", "blocked",
             Predicate("text_contains", "clicked"), ctx.task.task_id))
    assert s.outcome == "ESCALATE" and "blocked" in s.detail
    assert ctx.hands.page.locator("#status").inner_text() == before


def test_ask_escalates_then_approved_reroute_continues(ctx):
    s = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#toggle", "consequential",
             Predicate("text_contains", "clicked"), ctx.task.task_id))
    assert s.outcome == "ESCALATE" and "ask-pending" in s.detail
    assert ctx.hands.page.locator("#status").inner_text() == "not clicked"
    approval_id = ctx.audit.replay()[-1]["payload"]["approval_id"]
    decided = ctx.approvals.decide(approval_id, True)
    assert ctx.approvals.recheck(decided) == "ASK"
    ctx.approvals.must_be_gated("ASK")
    s2 = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#toggle", "reversible",
             Predicate("text_contains", "clicked"), ctx.task.task_id))
    assert (s2.ok, s2.verified, s2.outcome) == (True, True, "CONTINUE")


def test_paused_task_gets_no_turn(ctx):
    ctx.sched.pause(ctx.task.task_id)
    s = ctx.ctrl.run_step(
        ctx.task.task_id,
        step("browser.click", "#toggle", "reversible",
             Predicate("text_contains", "clicked"), ctx.task.task_id))
    assert s.outcome == "ESCALATE" and s.attempts == 0


def test_recovery_helpers():
    assert recover_execution(object(), "") is False
    recover_verification(object())  # must never raise
