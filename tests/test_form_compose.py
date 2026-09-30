"""Slice-34: composed grounded-form task runner.

run_form_task() composes goal -> open_page -> read_controls ->
ground_fields() -> TaskLoop.run_goal() for the single-form road only.
Caller states WHAT (typed slots) + addressing (form_url,
submit_selector); the page states WHERE (every field selector derived,
never fed). No loop/follow/dispatcher logic, no model, no new
executors, predicates, templates, policy, hint keys, or audit event
types.

Stub rigs prove refusal mapping + audit shape + approve/deny
passthrough through the real router/policy/token path. Live tests
prove approve DONE and deny-held-submit end to end on the B8 fixture.
No browser internals are imported by src; tests may use
collect_controls like Runner.observe uses page text
(caller-provided seams)."""

import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import run_form_task
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.actions import BrowserActions
from lakra.execution.browser.controller import (
    BrowserController,
    StepResult,
)
from lakra.execution.browser.observer import (
    BrowserObserver,
    collect_controls,
)
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

FIXTURES = Path(__file__).parent / "fixtures"
FORM_URL = (FIXTURES / "courses.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit", "browser.check",
         "browser.select")

CONTROLS = [("Full name", "text", "#m-name"),
            ("City", "text", "#m-city"),
            ("ZIP", "text", "#m-zip"),
            ("News", "check", "#p-news"),
            ("Country", "select", "#p-country")]

SLOTS = {"name": {"text": "Ada"},
         "city": {"text": "Lagos"},
         "zip": {"text": "10001"},
         "news": {"checked": True},
         "country": {"select": "ng"}}


class Stub:
    def execute(self, action, task):
        return ExecuteResult(ok=True, output="did")


class FakeController:
    """Policy-gated steps over the real router (mirrors test_loop)."""

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


class HealthyMonitor:
    def sample(self, force=False):
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384, ram_available_mb=8192,
            cpu_percent=5.0, lakra_rss_mb=50.0, browser_rss_mb=0.0,
            gpu_available=False)

    def under_pressure(self, **kw):
        return False, "ok"


def stub_rig(tmp_path, monitor=None):
    db = Database(tmp_path / "form.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    stub = Stub()
    for kind in KINDS:
        tools.register(kind, stub)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = FakeController(router)
    guards = Guards(scheduler=sched, monitor=monitor or HealthyMonitor())
    runner = Runner(ctrl, sched, audit, store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit, "sched": sched,
            "approvals": approvals, "runner": runner, "loop": loop}


def start(ns, goal="Submit the grounded application form", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def full_goal(**kw):
    goal = {"form_url": "file:///f.html",
            "goal_slots": dict(SLOTS),
            "submit_selector": "#m-submit"}
    goal.update(kw)
    return goal


# -- stub refusals ------------------------------------------------------------

def test_compose_form_refusals_before_plan(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)

    def run(open_page, read_controls, goal):
        return run_form_task(ns["loop"], open_page, read_controls,
                             t, goal, decider)

    opened = []
    r = run(opened.append, lambda: list(CONTROLS),
            {"form_url": "file:///f.html"})
    assert r.status == "STOPPED" and "malformed goal" in r.detail
    assert r.plan_id == "-" and r.steps_done == 0

    r = run(opened.append, lambda: list(CONTROLS), "not-a-mapping")
    assert r.status == "STOPPED" and "malformed goal" in r.detail

    def boom(url):
        raise OSError("network down")

    r = run(boom, lambda: list(CONTROLS), full_goal())
    assert r.status == "STOPPED" and "cannot open form" in r.detail

    def controls_boom():
        raise OSError("snapshot lost")

    r = run(opened.append, controls_boom, full_goal())
    assert r.status == "STOPPED" and "controls failed" in r.detail

    # Unknown slot shape: refused, never guessed.
    r = run(opened.append, lambda: list(CONTROLS),
            full_goal(goal_slots={"name": {"bogus": 1}}))
    assert r.status == "STOPPED" and "no groundable fields" in r.detail

    # Secret-shaped value travels nowhere.
    r = run(opened.append, lambda: list(CONTROLS),
            full_goal(goal_slots={"name": {"text": "my password is x"}}))
    assert r.status == "STOPPED" and "no groundable fields" in r.detail

    # Kind mismatch: checked slot, no check control observed.
    text_only = [("Full name", "text", "#m-name")]
    r = run(opened.append, lambda: list(text_only),
            full_goal(goal_slots={"news": {"checked": True}}))
    assert r.status == "STOPPED" and "no groundable fields" in r.detail

    # Zero overlap: slot names nothing observed.
    r = run(opened.append, lambda: list(CONTROLS),
            full_goal(goal_slots={"zzz": {"text": "x"}}))
    assert r.status == "STOPPED" and "no groundable fields" in r.detail

    # Tie: one slot word matches two same-kind controls equally.
    addrs = [("Home address", "text", "#p-home"),
             ("Work address", "text", "#p-work")]
    r = run(opened.append, lambda: list(addrs),
            full_goal(goal_slots={"address": {"text": "x"}}))
    assert r.status == "STOPPED" and "no groundable fields" in r.detail
    assert "ambiguous" in r.detail

    # Nothing runnable ever existed: no body plan built.
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" not in types
    assert "LOOP_START" not in types and "LOOP_END" not in types


def test_compose_form_refusal_audit_shape(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    r = run_form_task(ns["loop"], lambda url: None,
                      lambda: list(CONTROLS),
                      t, full_goal(goal_slots={"zzz": {"text": "x"}}),
                      ScriptedDecider(True))
    assert (r.plan_id, r.status, r.steps_done) == ("-", "STOPPED", 0)
    events = [e for e in ns["audit"].replay()
              if e["task_id"] == t.task_id]
    outcomes = [e for e in events if e["type"] == "PLAN_OUTCOME"]
    assert len(outcomes) == 1
    assert outcomes[0]["payload"]["status"] == "STOPPED"
    assert outcomes[0]["payload"]["plan_id"] == "-"
    assert outcomes[0]["payload"]["steps_done"] == 0
    types = [e["type"] for e in events]
    assert "PLAN_CREATED" not in types
    assert "VERIFICATION" not in types
    assert not [x for x in types if x.startswith("APPROVAL")]
    assert not [x for x in types if x.startswith("LOOP")]


# -- stub approve/deny (no browser) --------------------------------------------

def test_composed_form_stub_approve_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    opened = []
    res = run_form_task(ns["loop"], opened.append,
                        lambda: list(CONTROLS),
                        t, full_goal(), decider)
    assert opened == ["file:///f.html"]
    assert (res.status, res.steps_done) == ("DONE", 7)
    # The L3 submit gate fired exactly once through the composed path.
    assert len(decider.seen) == 1
    consumed = [e for e in ns["audit"].replay()
                if e["type"] == "APPROVAL_CONSUMED"]
    assert len(consumed) == 1
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" in types and "PLAN_OUTCOME" in types
    assert not [x for x in types if x.startswith("LOOP")]


def test_composed_form_stub_deny_stops(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run_form_task(ns["loop"], lambda url: None,
                        lambda: list(CONTROLS),
                        t, full_goal(), ScriptedDecider(False))
    assert res.status == "STOPPED" and "denied" in res.detail
    assert ns["registry"].get(t.task_id).status == Status.CANCELLED
    mine = [e for e in ns["audit"].replay()
            if e["task_id"] == t.task_id]
    assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]


# -- live composed form (B8 fixture) --------------------------------------------

def _live_ns(tmp_path,
              goal="Submit the grounded application form"):
    db = Database(tmp_path / "form-live.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(tmp_path / "prof")
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, tmp_path / "shots")
    for kind in ("browser.navigate", "browser.snapshot",
                 "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait", "browser.submit",
                 "browser.check", "browser.select"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    guards = Guards(scheduler=sched, monitor=HealthyMonitor())
    runner = Runner(
        ctrl, sched, audit,
        observe=lambda: hands.page.locator("body").inner_text(),
        store=db, guards=guards)
    taskloop = TaskLoop(runner, approvals, audit)
    t = registry.add(Task.create(
        goal, allowed_tools=["browser"],
        allowed_domains=["file:"], allowed_paths=[]))
    registry.checkout(t.task_id, "e")
    registry.set_status(t.task_id, Status.RUNNING)
    sched.enqueue(t.task_id)
    return {"db": db, "registry": registry, "audit": audit,
            "taskloop": taskloop, "hands": hands, "sessions": sessions,
            "task": t}


def test_live_composed_form_approve_done(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        decider = ScriptedDecider(True)
        read_controls = lambda: list(collect_controls(  # noqa: E731
            ns["hands"].page))
        res = run_form_task(
            ns["taskloop"], ns["hands"].open, read_controls,
            ns["task"], {"form_url": FORM_URL,
                         "goal_slots": dict(SLOTS),
                         "submit_selector": "#m-submit"},
            decider)
        # Only slot names+values and addressing were hand-fed; every
        # field selector came from the observed control inventory.
        assert (res.status, res.steps_done) == ("DONE", 7)
        assert len(decider.seen) == 1  # the submit gate asked once
        page = ns["hands"].page
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#m-city").input_value() == "Lagos"
        assert page.locator("#m-zip").input_value() == "10001"
        assert page.locator("#p-news").is_checked()
        assert page.locator("#p-country").input_value() == "ng"
        assert page.locator("#m-status").inner_text() == "submitted"
        types = [e["type"] for e in ns["audit"].replay()]
        assert "APPROVAL_CONSUMED" in types
        assert "APPROVED_STEP_EXECUTED" in types
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_composed_form_deny_holds_submit(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        read_controls = lambda: list(collect_controls(  # noqa: E731
            ns["hands"].page))
        res = run_form_task(
            ns["taskloop"], ns["hands"].open, read_controls,
            ns["task"], {"form_url": FORM_URL,
                         "goal_slots": dict(SLOTS),
                         "submit_selector": "#m-submit"},
            ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.CANCELLED
        page = ns["hands"].page
        # Safe prefix ran (all grounded bindings executed); the gate
        # held the submit.
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#p-country").input_value() == "ng"
        assert page.locator("#m-status").inner_text() == "not submitted"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == ns["task"].task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()
