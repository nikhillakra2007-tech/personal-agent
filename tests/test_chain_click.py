"""Slice-49: click legs in chains (existing click road via run_chain).

run_chain() accepts "click" legs with the exact click goal contract
{"click_url", "click_text", "expect_text"} and dispatches each leg
through the unchanged run_click_task() on the sibling non-completing
runner. All chain invariants hold: 2-4 legs, stop on first non-DONE
leg, intermediate legs never complete the task, per-leg approvals
unchanged, --from-leg works, no cursor, no cross-leg piping.

Stub rigs prove routing + lifecycle + approval preservation over the
real router/policy path. One CLI smoke proves --chain-file wiring
including the read_clicks seam.
"""

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
from lakra.execution.browser.controller import StepResult
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

ROOT = Path(__file__).resolve().parent.parent

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

PAIRS = [("Beta record", "beta.html")]
CLICKS = [("Continue", "#c-continue"), ("Cancel", "#c-cancel")]


class Stub:
    def execute(self, action, task):
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


class HealthyMonitor:
    def sample(self, force=False):
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384, ram_available_mb=8192,
            cpu_percent=5.0, lakra_rss_mb=50.0, browser_rss_mb=0.0,
            gpu_available=False)

    def under_pressure(self, **kw):
        return False, "ok"


def stub_rig(tmp_path):
    db = Database(tmp_path / "chain-click.db")
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
    guards = Guards(scheduler=sched, monitor=HealthyMonitor())
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


def click_leg(text="Continue", expect="Welcome",
              url="file:///c.html"):
    return {"road": "click", "click_url": url, "click_text": text,
            "expect_text": expect}


def form_leg():
    return {"road": "form", "form_url": "file:///f.html",
            "goal_slots": {"name": {"text": "Ada"}},
            "submit_selector": "#s"}


def seams(clicks=None):
    def open_page(url):
        return None

    def read_links():
        return list(PAIRS)

    def read_controls():
        return [("Name", "text", "#f")]

    def read_clicks():
        return list(clicks if clicks is not None else CLICKS)

    return open_page, read_links, read_controls, read_clicks


def run(ns, t, chain, decider, clicks=None, **kw):
    open_page, read_links, read_controls, read_clicks = seams(clicks)
    return run_chain(ns["loop"], ns["db"], open_page, read_links,
                     read_controls, t, chain, decider,
                     read_clicks=read_clicks, **kw)


# -- validation ---------------------------------------------------------------

def test_chain_accepts_click_legs(tmp_path):
    legs = validate_chain({"legs": [observe_leg(), click_leg()]})
    assert [leg["road"] for leg in legs] == ["observe", "click"]
    assert leg_goal_text(click_leg()) == "Click Continue"
    assert leg_goal_text({"road": "click"}) == "Click the control"
    try:
        validate_chain({"legs": [observe_leg(),
                                 {"road": "click",
                                  "click_url": "file:///c.html"}]})
    except ChainRefused as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("click leg missing keys must refuse")


# -- stub multi-leg behavior ----------------------------------------------------

def test_chain_observe_click_observe_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [observe_leg(), click_leg(), observe_leg()]},
              ScriptedDecider(True))
    assert res.status == "DONE"
    assert res.legs_done == 3
    assert res.steps_done == 2 + 1 + 2
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 3
    assert not [x for x in types if x.startswith("APPROVAL")]
    assert t.goal == "Chained legs"  # routing text restored afterwards


def test_chain_click_observe_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t, {"legs": [click_leg(), observe_leg()]},
              ScriptedDecider(True))
    assert res.status == "DONE" and res.legs_done == 2
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED


def test_chain_observe_click_follow_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [observe_leg(), click_leg(), follow_leg()]},
              ScriptedDecider(True))
    assert res.status == "DONE" and res.legs_done == 3
    assert res.steps_done == 2 + 1 + 4


def test_chain_click_zero_match_stops(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [observe_leg(), click_leg(text="Launch rocket"),
                         follow_leg()]},
              ScriptedDecider(True))
    assert res.status == "STOPPED" and res.legs_done == 1
    assert "leg 2/3 (click)" in res.detail
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 1  # leg 3 never planned


def test_chain_click_tie_stops(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    tied = [("Save draft", "#a"), ("Save draft copy", "#b")]
    res = run(ns, t,
              {"legs": [click_leg(text="Save draft"), observe_leg()]},
              ScriptedDecider(True), clicks=tied)
    assert res.status == "STOPPED" and res.legs_done == 0
    assert "leg 1/2 (click)" in res.detail
    assert "ambiguous" in res.detail
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]


def test_chain_click_failure_blocks_later_legs(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [click_leg(), observe_leg(expect=""),
                         follow_leg()]},
              ScriptedDecider(True))
    assert res.status == "STOPPED" and res.legs_done == 1
    types = [e["type"] for e in ns["audit"].replay()]
    # Leg 2 refuses pre-plan (empty expect) and leg 3 never runs:
    # only leg 1 ever planned.
    assert types.count("PLAN_CREATED") == 1


def test_chain_click_then_form_deny(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(False)
    res = run(ns, t,
              {"legs": [click_leg(), form_leg(), observe_leg()]},
              decider)
    assert res.status == "STOPPED" and res.legs_done == 1
    assert "denied" in res.detail and "leg 2/3 (form)" in res.detail
    assert ns["registry"].get(t.task_id).status == Status.CANCELLED
    assert len(decider.seen) == 1  # exactly one gate asked, once
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 2  # leg 3 never planned
    assert types.count("APPROVAL_CONSUMED") == 0


def test_chain_from_leg_starts_at_click(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [observe_leg(), click_leg(), observe_leg()]},
              ScriptedDecider(True), from_leg=2)
    assert res.status == "DONE" and res.legs_done == 3
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 2  # leg 1 skipped entirely


def test_chain_click_done_does_not_complete_task(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [click_leg(), observe_leg(expect="")]},
              ScriptedDecider(True))
    assert res.status == "STOPPED" and res.legs_done == 1
    # Intermediate DONE must not complete the task: still stalled here.
    assert ns["registry"].get(t.task_id).status == Status.RUNNING


def test_chain_click_submit_only_inventory_refuses(tmp_path):
    # Submit-kind controls never enter the click inventory (excluded
    # at collection), so a submit-only page yields an empty inventory
    # and the leg refuses before any click.
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run(ns, t,
              {"legs": [click_leg(text="Pay now"), observe_leg()]},
              ScriptedDecider(True), clicks=[])
    assert res.status == "STOPPED" and res.legs_done == 0
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]


# -- CLI smoke ------------------------------------------------------------------

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


def test_cli_chain_click_leg_exit_0(tmp_path):
    fixtures = Path(__file__).parent / "fixtures"
    list_url = (fixtures / "loop-index.html").as_uri()
    click_url = (fixtures / "click.html").as_uri()
    chain = write_chain(tmp_path, "chain.json", [
        {"road": "observe", "url": list_url, "expect_text": "Records"},
        {"road": "click", "click_url": click_url,
         "click_text": "Continue", "expect_text": "Welcome"}])
    proc = run_cli(tmp_path, "--chain-file", chain, "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "task: COMPLETED" in proc.stdout
