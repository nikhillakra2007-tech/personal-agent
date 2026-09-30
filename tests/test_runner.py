"""Runner tests: loop outcomes on the live fixture (no network, no model)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.planner import Planner
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.tasks import Status, Task
from lakra.execution.browser.actions import BrowserActions
from lakra.execution.browser.controller import BrowserController
from lakra.execution.browser.observer import BrowserObserver
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ToolRegistry
from lakra.execution.router import ToolRouter

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


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
                 "browser.scroll", "browser.wait", "browser.submit"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    runner = Runner(ctrl, sched, audit,
                    observe=lambda: hands.page.locator("body").inner_text())
    ns = SimpleNamespace(registry=registry, audit=audit, sched=sched,
                         approvals=approvals, tools=tools, sessions=sessions,
                         hands=hands, obs=obs, router=router, ctrl=ctrl,
                         runner=runner)
    yield ns
    sessions.close()


def start(ns, goal, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    task = ns.registry.add(Task.create(goal, **kw))
    ns.registry.checkout(task.task_id, "exec")
    ns.registry.set_status(task.task_id, Status.RUNNING)
    ns.sched.enqueue(task.task_id)
    return task


def test_full_pass_done(ctx):
    task = start(ctx, "List the demo courses")
    plan = Planner().plan(task, {"url": FIXTURE, "expect_text": "pending"})
    for line in [s.rationale for s in plan.steps]:
        assert line  # rationales printed before execution (see replay)
    res = ctx.runner.run(plan)
    assert (res.status, res.steps_done) == ("DONE", 2)


def test_verify_fail_stops_after_replan(ctx):
    task = start(ctx, "Click the toggle button")
    ctx.hands.open(FIXTURE)  # click template has no navigate step
    hints = {"selector": "#toggle", "expect_text": "never-appears"}
    plan = Planner().plan(task, dict(hints))
    res = ctx.runner.run(plan, hints=hints)
    assert res.status == "STOPPED" and res.steps_done == 0
    types = [e["type"] for e in ctx.audit.replay()]
    assert "PLAN_SUPERSEDED" in types and "PLAN_OUTCOME" in types


def test_ask_approve_token_resume(ctx):
    task = start(ctx, "Submit the demo form")
    plan = Planner().plan(task, {"url": FIXTURE, "selector": "#echo",
                                 "text": "hi",
                                 "submit_selector": "#toggle"})
    # Navigate first WITHOUT completing anything (a DONE plan now closes
    # its task via lifecycle ownership, so the page setup must not run).
    from lakra.control.policy import Action
    ctx.router.route(task.task_id, Action(
        kind="browser.navigate", target=FIXTURE, effect="reversible",
        task_id=task.task_id))
    ctx.hands.attach(ctx.obs.current_page)
    parked = ctx.ctrl.run_step(
        task.task_id, plan.steps[2].to_browser_step())
    assert parked.outcome == "ESCALATE" and "ask-pending" in parked.detail
    approval_id = ctx.audit.replay()[-1]["payload"]["approval_id"]
    # NOTE: the submit twin here is illustrative; approval-token execution
    # of consequential browser actions is covered in slice-07 tests.
    decided = ctx.approvals.decide(approval_id, False)  # deny: safe default
    assert decided.decided == "denied"
    assert ctx.registry.get(task.task_id).status == Status.CANCELLED


def test_budget_cutoff_stops(ctx):
    from lakra.control.tasks import Budget
    task = start(ctx, "List the demo courses",
                 budget=Budget(max_steps=1, max_tokens_cents=0,
                               max_minutes=30))
    plan = Planner().plan(task, {"url": FIXTURE, "expect_text": "pending"})
    res = ctx.runner.run(plan)
    assert res.status == "STOPPED" and "budget" in res.detail


def test_pause_mid_plan_stops(ctx):
    task = start(ctx, "List the demo courses")
    plan = Planner().plan(task, {"url": FIXTURE, "expect_text": "pending"})
    ctx.sched.pause(task.task_id)
    res = ctx.runner.run(plan)
    assert res.status == "STOPPED"
