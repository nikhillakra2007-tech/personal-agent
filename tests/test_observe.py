"""Slice-43: composed observe road (read-only page Q&A).

run_observe_task() composes goal -> TaskLoop.run_goal() through the
observe template for the L0 read-only road only: navigate + snapshot,
no inventory to ground, no browser seams, no approvals, no side
effects. No loop/follow/form/dispatcher logic, no model, no new
executors, predicates, templates, policy, or audit event types.

Stub rigs prove refusal mapping + audit shape + happy-path passthrough
(real router/policy/approvals/audit/sched, stub executors). Live tests
prove the index observation end to end on fixtures. Dispatcher tests
prove the url/expect_text road routes and mixes refuse. One CLI smoke
proves --road observe end to end."""

import subprocess
import sys
import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import run_observe_task, run_task
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
from lakra.execution.browser.observer import BrowserObserver
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = (FIXTURES / "loop-index.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")


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
    db = Database(tmp_path / "observe.db")
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


def start(ns, goal="Show the records index", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def full_goal(**kw):
    goal = {"url": "file:///l.html", "expect_text": "Records"}
    goal.update(kw)
    return goal


# -- stub refusals ------------------------------------------------------------

def test_observe_refusals_before_plan(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)

    def run(goal):
        return run_observe_task(ns["loop"], t, goal, decider)

    r = run({"url": "file:///l.html"})
    assert r.status == "STOPPED" and "malformed goal" in r.detail
    assert r.plan_id == "-" and r.steps_done == 0

    r = run("not-a-mapping")
    assert r.status == "STOPPED" and "malformed goal" in r.detail

    r = run({"expect_text": "Records"})
    assert r.status == "STOPPED" and "malformed goal" in r.detail

    # Nothing plannable ever existed: planned nothing, verified nothing.
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_OUTCOME") == 3
    assert "PLAN_CREATED" not in types
    assert "VERIFICATION" not in types


def test_observe_bad_hints_still_refused(tmp_path):
    # The planner's own gate stays authoritative: unplannable values
    # refuse through run_goal's shape, never executed, never guessed.
    ns = stub_rig(tmp_path)
    t = start(ns)
    r = run_observe_task(ns["loop"], t, full_goal(expect_text=""),
                         ScriptedDecider(True))
    assert r.status == "STOPPED" and "cannot plan" in r.detail
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" not in types


# -- stub happy path ------------------------------------------------------------

def test_composed_observe_stub_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    res = run_observe_task(ns["loop"], t, full_goal(), decider)
    assert (res.status, res.steps_done) == ("DONE", 2)
    # L0 road: no approval gate exists, the decider was never consulted.
    assert decider.seen == []
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" in types and "PLAN_OUTCOME" in types
    assert not [x for x in types if x.startswith("APPROVAL")]
    assert not [x for x in types if x.startswith("LOOP")]


# -- dispatcher ------------------------------------------------------------------

def _stub_ns_loop(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    return ns, t


def test_dispatch_observe_road_and_mixes(tmp_path):
    ns, t = _stub_ns_loop(tmp_path)
    decider = ScriptedDecider(True)

    def boom_open(url):
        raise AssertionError("observe road takes no open_page seam")

    def boom_links():
        raise AssertionError("observe road reads no inventory")

    def boom_controls():
        raise AssertionError("observe road reads no controls")

    res = run_task(ns["loop"], ns["db"], boom_open, boom_links,
                   boom_controls, t, full_goal(), decider)
    assert (res.status, res.steps_done) == ("DONE", 2)

    for bad in (dict(full_goal(), max_items=1, max_iters=1),
                dict(full_goal(), goal_slots={"a": {"text": "b"}}),
                dict(full_goal(), list_url="file:///l.html",
                     goal_text="x", body_expect="y")):
        r = run_task(ns["loop"], ns["db"], boom_open, boom_links,
                     boom_controls, t, bad, decider)
        assert r.status == "STOPPED" and "mixed road keys" in r.detail


# -- live composed observe ----------------------------------------------------------

def _live_ns(tmp_path, goal="Show the records index"):
    db = Database(tmp_path / "observe-live.db")
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


def test_live_composed_observe_index_done(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        decider = ScriptedDecider(True)
        res = run_observe_task(
            ns["taskloop"], ns["task"],
            {"url": LIST_URL, "expect_text": "Records"}, decider)
        assert (res.status, res.steps_done) == ("DONE", 2)
        page = ns["hands"].page
        assert page.url == LIST_URL  # never left the observed page
        assert "Records" in (page.locator("body").inner_text() or "")
        assert decider.seen == []  # no approval gate on this road
        types = [e["type"] for e in ns["audit"].replay()]
        assert not [x for x in types if x.startswith("APPROVAL")]
        assert all(e["payload"].get("result") == "PASS"
                   for e in ns["audit"].replay()
                   if e["type"] == "VERIFICATION")
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- live CLI smoke -------------------------------------------------------------------

def run_cli(tmp_path, *argv):
    import os
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / "cli.db")
    cmd = [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
           "--audit", str(tmp_path / "audit-do.jsonl"),
           "--profile-dir", str(tmp_path / "prof"),
           "--shots-dir", str(tmp_path / "shots"),
           "--ram-floor-mb", "0", "--cpu-ceiling", "100", *argv]
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(ROOT), env=env, timeout=240)


def test_cli_observe_done_exit_0(tmp_path):
    proc = run_cli(tmp_path, "--road", "observe", "--url", LIST_URL,
                   "--text", "Records index", "--expect", "Records",
                   "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "task: COMPLETED" in proc.stdout


def test_cli_observe_bad_road_mix_exit_1(tmp_path):
    proc = run_cli(tmp_path, "--road", "observe", "--url", LIST_URL,
                   "--text", "Records index", "--expect", "Records",
                   "--submit", "#s", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout
