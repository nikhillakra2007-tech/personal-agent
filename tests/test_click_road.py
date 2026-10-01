"""Composed click road (open -> ground -> click template -> verify).

run_click_task() drives goal {"click_url", "click_text", "expect_text"}
through the unchanged dispatcher: the target phrase is grounded against
the observed click inventory (link texts + non-submit buttons) via the
deterministic ground_click() — unique winner or refusal. Submit-kind
controls never enter the inventory, so browser.click here can never
dodge the L3 browser.submit gate. Execution is the existing _click_steps
template through TaskLoop.run_goal under the existing L1 policy.

Stub rigs prove routing + grounding + L1 execution over the real
router/policy path. Live observer tests prove the inventory contract.
CLI smokes prove --road click wiring.
"""

import subprocess
import sys
import time
from pathlib import Path

from lakra.control.analyzer import ground_click
from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.planner import UnknownGoalError
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.execution.browser.observer import collect_clicks
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
CLICK_URL = (FIXTURES / "click.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

CLICKS = [("Continue", "Continue"), ("Cancel", "#c-cancel")]


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
    db = Database(tmp_path / "click-road.db")
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


def start(ns, goal="Click the Continue button", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def click_goal(**kw):
    goal = {"click_url": "file:///c.html", "click_text": "Continue",
            "expect_text": "Welcome"}
    goal.update(kw)
    return goal


def seams(clicks=None):
    def open_page(url):
        return None

    def read_links():
        return []

    def read_controls():
        return []

    def read_clicks():
        return list(clicks if clicks is not None else CLICKS)

    return open_page, read_links, read_controls, read_clicks


# -- unit: ground_click --------------------------------------------------------


def test_ground_click_unique_link_text():
    assert ground_click("Continue", CLICKS) == "Continue"


def test_ground_click_unique_button_selector():
    assert ground_click("Cancel", CLICKS) == "#c-cancel"


def test_ground_click_zero_match_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_click("xyzzy", CLICKS)


def test_ground_click_tie_refused_named():
    import pytest
    tied = [("Save draft", "#a"), ("Save draft copy", "#b")]
    with pytest.raises(UnknownGoalError) as exc:
        ground_click("Save draft", tied)
    assert "Save draft" in str(exc.value)


def test_ground_click_malformed_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_click("Continue", [])
    with pytest.raises(UnknownGoalError):
        ground_click("Continue", [("Continue",)])
    with pytest.raises(UnknownGoalError):
        ground_click("Continue", [("", "#c")])
    with pytest.raises(UnknownGoalError):
        ground_click("Continue", [("Continue", "")])


def test_ground_click_stopword_only_phrase_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_click("the and", CLICKS)


def test_ground_click_deterministic():
    assert ground_click("Continue", CLICKS) == \
        ground_click("Continue", CLICKS)


def test_no_model_calls_in_ground_click():
    import lakra.control.analyzer as mod
    src = open(mod.__file__).read()
    assert "models." not in src and "complete(" not in src
    assert "ollama" not in src.lower()


# -- live observer: collect_clicks inventory contract --------------------------


def test_collect_clicks_links_and_buttons(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        page = s.new_page(CLICK_URL)
        from lakra.execution.browser.observer import collect_clicks as cc
        inv = list(cc(page))
        assert ("Continue", "#c-continue") in inv
        assert ("Cancel", "#c-cancel") in inv


def test_collect_clicks_excludes_submits(tmp_path):
    from lakra.execution.browser.observer import collect_submits
    with BrowserSessions(tmp_path / "p") as s:
        page = s.new_page((FIXTURES / "search.html").as_uri())
        clicks = list(collect_clicks(page))
        submits = list(collect_submits(page))
        assert submits, "search fixture must have a submit control"
        submit_refs = {sel for _, _, sel in submits}
        click_refs = {ref for _, ref in clicks}
        assert not (submit_refs & click_refs), \
            "submit selectors must never enter the click inventory"


# -- integration: dispatcher routing + road behavior ---------------------------


def test_click_goal_routes_to_click_branch(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, read_clicks = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, click_goal(), ScriptedDecider(True),
                   read_clicks=read_clicks)
    assert res.status == "DONE"
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    kinds = [e["payload"].get("kind") for e in ns["audit"].replay()
             if e["type"] == "ACTION_ALLOWED"]
    assert "browser.click" in kinds
    assert not [e for e in ns["audit"].replay()
                if e["type"].startswith("APPROVAL")]


def test_click_goal_no_longer_misroutes_to_observe(tmp_path):
    # Slice-48 finding: a click-shaped goal used to fall into the
    # observe branch with the selector silently dropped.
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, read_clicks = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, click_goal(), ScriptedDecider(True),
                   read_clicks=read_clicks)
    kinds = [e["payload"].get("kind") for e in ns["audit"].replay()
             if e["type"] == "ACTION_ALLOWED"]
    assert "browser.click" in kinds
    assert res.status == "DONE"


def test_click_tie_stops_before_any_click(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    tied = [("Save draft", "#a"), ("Save draft copy", "#b")]
    open_page, read_links, read_controls, read_clicks = seams(tied)
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t,
                   click_goal(click_text="Save draft"),
                   ScriptedDecider(True), read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0
    assert "ambiguous" in res.detail
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_OUTCOME") == 1


def test_click_zero_match_stops_before_any_click(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, read_clicks = seams([])
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, click_goal(),
                   ScriptedDecider(True), read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]


def test_click_missing_keys_refused(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, read_clicks = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t,
                   {"click_url": "file:///c.html"},
                   ScriptedDecider(True), read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0


def test_click_without_seam_refused(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, _ = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, click_goal(),
                   ScriptedDecider(True))
    assert res.status == "STOPPED" and res.steps_done == 0
    assert "inventory unavailable" in res.detail


def test_click_mixed_keys_refused(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, read_clicks = seams()
    mixed = click_goal()
    mixed["goal_slots"] = {"name": {"text": "x"}}
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, mixed,
                   ScriptedDecider(True), read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0
    assert "mixed road keys" in res.detail


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


def test_cli_click_done_exit_0(tmp_path):
    proc = run_cli(tmp_path, "--road", "click", "--url", CLICK_URL,
                   "--text", "Continue", "--expect", "Welcome", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "road: click" in proc.stdout
    assert "task: COMPLETED" in proc.stdout


def test_cli_click_missing_flags_exit_1(tmp_path):
    for argv in (["--road", "click", "--url", CLICK_URL, "--yes"],
                 ["--road", "click", "--url", CLICK_URL,
                  "--text", "Continue", "--yes"],
                 ["--road", "click", "--text", "Continue",
                  "--expect", "Welcome", "--yes"]):
        proc = run_cli(tmp_path, *argv)
        assert proc.returncode == 1, (argv, proc.stdout + proc.stderr)
        assert "usage error" in proc.stdout


def test_cli_click_ambiguous_target_exit_1(tmp_path):
    proc = run_cli(tmp_path, "--road", "click", "--url", CLICK_URL,
                   "--text", "Continue Cancel", "--expect", "Welcome",
                   "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout


def test_cli_click_bad_flag_mix_exit_1(tmp_path):
    proc = run_cli(tmp_path, "--road", "click", "--url", CLICK_URL,
                   "--text", "Continue", "--expect", "Welcome",
                   "--max-items", "3", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout
