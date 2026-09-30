"""Slice-33: composed grounded-follow task runner.

run_follow_task() composes goal -> open_page -> read_links ->
grounded_follow() -> TaskLoop.run_goal() for the single-follow road
only. No loop/form/dispatcher logic, no model, no new executors,
predicates, templates, policy, or audit event types.

Stub rigs prove refusal mapping + audit shape + happy-path passthrough
(real router/policy/approvals/audit/sched, stub executors). Live tests
prove the Beta follow end to end on fixtures plus tie refusal. No
browser internals are imported by src; tests may use collect_links like
Runner.observe uses page text (caller-provided seams)."""

import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import run_follow_task
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
from lakra.execution.browser.observer import BrowserObserver, collect_links
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = (FIXTURES / "loop-index.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

RECORDS = [("Alpha record", "alpha.html"), ("Beta record", "beta.html"),
           ("Gamma record", "gamma.html")]


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
    db = Database(tmp_path / "follow.db")
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


def start(ns, goal="Follow the Beta record", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def full_goal(**kw):
    goal = {"list_url": "file:///l.html",
            "goal_text": "Follow the Beta record",
            "body_expect": "detail record: Beta"}
    goal.update(kw)
    return goal


# -- stub refusals ------------------------------------------------------------

def test_compose_follow_refusals_before_plan(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)

    def run(open_page, read_links, goal):
        return run_follow_task(ns["loop"], open_page, read_links,
                               t, goal, decider)

    opened = []
    r = run(opened.append, lambda: RECORDS, {"list_url": "file:///l.html"})
    assert r.status == "STOPPED" and "malformed goal" in r.detail
    assert r.plan_id == "-" and r.steps_done == 0

    r = run(opened.append, lambda: RECORDS, "not-a-mapping")
    assert r.status == "STOPPED" and "malformed goal" in r.detail

    def boom(url):
        raise OSError("network down")

    r = run(boom, lambda: RECORDS, full_goal())
    assert r.status == "STOPPED" and "cannot open list" in r.detail

    def inv_boom():
        raise OSError("snapshot lost")

    r = run(opened.append, inv_boom, full_goal())
    assert r.status == "STOPPED" and "inventory failed" in r.detail

    # Tie: "record" overlaps every link equally -> refuse, never guess.
    r = run(opened.append, lambda: list(RECORDS),
            full_goal(goal_text="Follow record"))
    assert r.status == "STOPPED" and "no groundable link" in r.detail

    # Zero overlap: syllabus names nothing observed.
    r = run(opened.append, lambda: list(RECORDS),
            full_goal(goal_text="Show syllabus"))
    assert r.status == "STOPPED" and "no groundable link" in r.detail

    # No plan ever built: nothing executed beyond the pre-plan seams.
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" not in types
    assert "LOOP_START" not in types and "LOOP_END" not in types


def test_compose_follow_refusal_audit_shape(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    r = run_follow_task(ns["loop"], lambda url: None,
                        lambda: list(RECORDS),
                        t, full_goal(goal_text="Show syllabus"),
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


# -- stub happy path (no browser) ----------------------------------------------

def test_composed_follow_stub_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    opened = []
    res = run_follow_task(ns["loop"], opened.append,
                          lambda: list(RECORDS),
                          t, full_goal(), decider)
    assert opened == ["file:///l.html"]
    assert (res.status, res.steps_done) == ("DONE", 4)
    # Follow road has no L3 step: the human gate was never consulted.
    assert decider.seen == []
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" in types and "PLAN_OUTCOME" in types
    assert not [x for x in types if x.startswith("APPROVAL")]
    assert not [x for x in types if x.startswith("LOOP")]


# -- live composed follow -------------------------------------------------------

def _live_ns(tmp_path, goal="Follow the Beta record"):
    db = Database(tmp_path / "follow-live.db")
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


def test_live_composed_follow_beta_done(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        decider = ScriptedDecider(True)
        read_pairs = lambda: list(collect_links(ns["hands"].page))  # noqa: E731
        res = run_follow_task(
            ns["taskloop"], ns["hands"].open, read_pairs,
            ns["task"], {"list_url": LIST_URL,
                         "goal_text": "Follow the Beta record",
                         "body_expect": "detail record: Beta"},
            decider)
        # Only goal words + URL + arrival proof were hand-fed; the
        # link_text ("Beta record") came from the observed inventory.
        assert (res.status, res.steps_done) == ("DONE", 4)
        page = ns["hands"].page
        assert page.url.endswith("loop-beta.html")
        assert "detail record: Beta" in (
            page.locator("body").inner_text() or "")
        assert decider.seen == []  # no approval gate on this road
        types = [e["type"] for e in ns["audit"].replay()]
        assert not [x for x in types if x.startswith("APPROVAL")]
        assert all(e["payload"].get("result") == "PASS"
                   for e in ns["audit"].replay()
                   if e["type"] == "VERIFICATION")
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_composed_follow_tie_refuses_stays(tmp_path):
    ns = _live_ns(tmp_path, goal="Follow record")
    try:
        read_pairs = lambda: list(collect_links(ns["hands"].page))  # noqa: E731
        res = run_follow_task(
            ns["taskloop"], ns["hands"].open, read_pairs,
            ns["task"], {"list_url": LIST_URL,
                         "goal_text": "Follow record",
                         "body_expect": "detail record"},
            ScriptedDecider(True))
        assert res.status == "STOPPED"
        assert "no groundable link" in res.detail
        assert res.plan_id == "-" and res.steps_done == 0
        # Grounding refused before planning: never left the list page.
        assert ns["hands"].page.url == LIST_URL
    finally:
        ns["sessions"].close()
        ns["db"].close()
