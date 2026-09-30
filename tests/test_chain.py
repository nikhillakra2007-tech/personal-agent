"""Slice-45: chained multi-template runs (sequential supervised legs).

run_chain() drives 2-4 single-road legs through the unchanged
dispatcher on a sibling non-completing runner: intermediate DONE never
completes the task, the first non-DONE leg ends the chain, budgets and
guards apply per step under one task, and each L3 gate parks for the
threaded decider. No cross-leg data piping (each leg re-grounds
fresh); loop legs refused; no new audit event types.

Stub rigs prove validation + stop/pause/deny/ownership semantics over
the real router/policy/token path. Live tests prove a 3-leg fixture
chain end to end. CLI smokes prove --chain-file/--from-leg wiring."""

import json
import subprocess
import sys
import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.chain import (
    ChainRefused,
    leg_goal_text,
    run_chain,
    validate_chain,
)
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
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
    collect_links,
)
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = (FIXTURES / "loop-index.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

PAIRS = [("Beta record", "beta.html")]


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


class StarvedMonitor(HealthyMonitor):
    def under_pressure(self, **kw):
        return True, "low RAM: 64 MB available"


def stub_rig(tmp_path, monitor=None):
    db = Database(tmp_path / "chain.db")
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


def start(ns, goal="Chained legs", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def observe_leg(url="file:///l.html", expect="Records"):
    return {"road": "observe", "url": url, "expect_text": expect}


def follow_leg():
    return {"road": "follow", "list_url": "file:///l.html",
            "goal_text": "Beta record", "body_expect": "detail record"}


def form_leg():
    return {"road": "form", "form_url": "file:///f.html",
            "goal_slots": {"name": {"text": "Ada"}},
            "submit_selector": "#s"}


def seams():
    def open_page(url):
        return None

    def read_links():
        return list(PAIRS)

    def read_controls():
        return [("Name", "text", "#f")]

    return open_page, read_links, read_controls


# -- validation -------------------------------------------------------------------

def test_chain_validates_shapes(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    open_page, read_links, read_controls = seams()

    def run(chain):
        return run_chain(ns["loop"], ns["db"], open_page, read_links,
                         read_controls, t, chain, decider)

    bad_chains = [
        "not-a-mapping",
        {},
        {"legs": []},
        {"legs": [observe_leg()]},
        {"legs": [observe_leg()] * 5},
        {"legs": [observe_leg(), {"road": "teleport"}]},
        {"legs": [observe_leg(),
                   {"road": "loop", "list_url": "u", "goal_text": "g",
                    "body_expect": "e", "max_items": 1, "max_iters": 1}]},
        {"legs": [observe_leg(), {"road": "follow",
                                  "list_url": "file:///l.html"}]},
    ]
    for bad in bad_chains:
        r = run(bad)
        assert r.status == "STOPPED" and r.legs_done == 0, bad
        assert r.steps_done == 0

    # One shaped refusal audit entry per invalid chain, nothing planned.
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_OUTCOME") == len(bad_chains)
    assert "PLAN_CREATED" not in types

    try:
        validate_chain({"legs": [observe_leg()]})
    except ChainRefused:
        pass
    else:
        raise AssertionError("single-leg chain must refuse")


def test_leg_goal_text_defaults():
    assert leg_goal_text({"road": "follow",
                          "goal_text": "Beta record"}) == "Follow Beta record"
    assert leg_goal_text({"road": "observe",
                          "expect_text": "Records"}) == "Show Records"
    assert leg_goal_text({"road": "form"}) == "Submit the form"
    assert leg_goal_text(
        {"road": "search",
         "query": {"text": "cathedrals"}}) == "Web search cathedrals"
    assert leg_goal_text({"road": "follow", "goal": "Custom words",
                          "goal_text": "x",
                          "body_expect": "y"}) == "Custom words"


# -- stub multi-leg behavior --------------------------------------------------------

def test_chain_two_legs_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(), follow_leg()]}, decider)
    assert res.status == "DONE"
    assert res.legs_done == 2
    assert res.steps_done == 2 + 4
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    assert decider.seen == []  # no L3 gate on either leg
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 2
    assert not [x for x in types if x.startswith("APPROVAL")]
    assert t.goal == "Chained legs"  # routing text restored afterwards


def test_chain_stops_first_failure(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(expect=""),
                              follow_leg()]},
                    ScriptedDecider(True))
    assert res.status == "STOPPED" and res.legs_done == 0
    assert "leg 1/2 (observe)" in res.detail
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" not in types  # leg 2 never planned
    assert t.goal == "Chained legs"


def test_chain_task_not_completed_midway(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(), observe_leg(expect="")]},
                    ScriptedDecider(True))
    assert res.status == "STOPPED" and res.legs_done == 1
    # Intermediate DONE must not complete the task: still stalled here.
    assert ns["registry"].get(t.task_id).status == Status.RUNNING


def test_chain_form_deny_midchain(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(False)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(), form_leg(),
                              observe_leg()]},
                    decider)
    assert res.status == "STOPPED" and res.legs_done == 1
    assert "denied" in res.detail and "leg 2/3 (form)" in res.detail
    assert ns["registry"].get(t.task_id).status == Status.CANCELLED
    assert len(decider.seen) == 1  # exactly one gate asked, once
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 2  # leg 3 never planned
    assert types.count("APPROVAL_CONSUMED") == 0


def test_chain_pause_propagates(tmp_path):
    ns = stub_rig(tmp_path, monitor=StarvedMonitor())
    t = start(ns)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(), follow_leg()]},
                    ScriptedDecider(True))
    assert res.status == "PAUSED" and res.legs_done == 0
    assert ns["registry"].get(t.task_id).status == Status.PAUSED


def test_chain_from_leg(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(), observe_leg(),
                              observe_leg()]},
                    ScriptedDecider(True), from_leg=2)
    assert res.status == "DONE" and res.legs_done == 3
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 2  # leg 1 skipped entirely

    bad = run_chain(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, t,
                    {"legs": [observe_leg(), observe_leg()]},
                    ScriptedDecider(True), from_leg=9)
    assert bad.status == "STOPPED" and bad.legs_done == 0


# -- live multi-leg chain --------------------------------------------------------------

def _live_ns(tmp_path, goal="Chained legs"):
    db = Database(tmp_path / "chain-live.db")
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


def test_live_chain_observe_follow_observe(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        from lakra.control.chain import run_chain as run_c
        decider = ScriptedDecider(True)
        res = run_c(
            ns["taskloop"], ns["db"], ns["hands"].open,
            lambda: [(text, href) for text, href in collect_links(  # noqa: E731
                ns["hands"].page)],
            lambda: [],  # noqa: E731 (no form leg; never called)
            ns["task"],
            {"legs": [
                {"road": "observe", "url": LIST_URL,
                 "expect_text": "Records"},
                {"road": "follow", "list_url": LIST_URL,
                 "goal_text": "Follow the Beta record",
                 "body_expect": "detail record: Beta"},
                {"road": "observe",
                 "url": (FIXTURES / "loop-beta.html").as_uri(),
                 "expect_text": "detail record: Beta"}]},
            decider)
        assert res.status == "DONE" and res.legs_done == 3
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.COMPLETED
        assert decider.seen == []  # no L3 gate on any leg
        types = [e["type"] for e in ns["audit"].replay()]
        assert types.count("PLAN_CREATED") == 3
        assert not [x for x in types if x.startswith("APPROVAL")]
        assert all(e["payload"].get("result") == "PASS"
                   for e in ns["audit"].replay()
                   if e["type"] == "VERIFICATION")
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- live CLI smoke ----------------------------------------------------------------------

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


def write_chain(tmp_path, name, legs):
    path = tmp_path / name
    path.write_text(json.dumps({"legs": legs}), encoding="utf-8")
    return str(path)


def test_cli_chain_done_exit_0(tmp_path):
    chain = write_chain(tmp_path, "chain.json", [
        {"road": "observe", "url": LIST_URL, "expect_text": "Records"},
        {"road": "follow", "list_url": LIST_URL,
         "goal_text": "Follow the Beta record",
         "body_expect": "detail record: Beta"}])
    proc = run_cli(tmp_path, "--chain-file", chain, "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "task: COMPLETED" in proc.stdout


def test_cli_chain_bad_file_and_mix_exit_1(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    proc = run_cli(tmp_path, "--chain-file", str(bad), "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout

    chain = write_chain(tmp_path, "chain.json", [
        {"road": "observe", "url": LIST_URL, "expect_text": "Records"}])
    proc = run_cli(tmp_path, "--chain-file", chain, "--road", "follow",
                   "--url", LIST_URL, "--text", "x", "--expect", "y",
                   "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_cli_chain_from_leg(tmp_path):
    chain = write_chain(tmp_path, "chain.json", [
        {"road": "observe", "url": LIST_URL, "expect_text": "Records"},
        {"road": "follow", "list_url": LIST_URL,
         "goal_text": "Follow the Beta record",
         "body_expect": "detail record: Beta"}])
    proc = run_cli(tmp_path, "--chain-file", chain, "--from-leg", "2",
                   "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
