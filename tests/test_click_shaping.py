"""NL click shaping (deterministic prose -> click road).

shape_goal() extends to click intent: target-phrase extraction, URL
extraction, and required verification-text extraction. The shaped dict
{"click_url", "click_text", "expect_text"} flows through the same
run_task() -> dispatcher -> click road -> L1 policy -> verification
path as explicit --road click. The click road grounds the target
phrase via ground_click() against the observed click inventory
(submit-kind controls excluded there); the shaper never invents a
selector. No model calls, no policy changes, no new roads.

Stub rigs prove shaping + dispatcher routing + L1 execution over the
real router/policy path. CLI smokes prove --goal click wiring.
"""

import subprocess
import sys
import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.planner import UnknownGoalError
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.shaping import shape_goal
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
CLICK_URL = (FIXTURES / "click.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

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
    db = Database(tmp_path / "click-shaping.db")
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


# -- unit: click shaping -------------------------------------------------------


def test_click_shapes_valid():
    shaped = shape_goal(
        "Open file:///c.html, click the Continue button,"
        " and verify Welcome")
    assert shaped == {"click_url": "file:///c.html",
                      "click_text": "Continue button",
                      "expect_text": "Welcome"}


def test_click_shapes_with_confirm_marker():
    shaped = shape_goal(
        "Open file:///c.html, click Continue and confirm Welcome")
    assert shaped["click_url"] == "file:///c.html"
    assert shaped["click_text"] == "Continue"
    assert shaped["expect_text"] == "Welcome"


def test_click_missing_url_refuses():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Click the Continue button and verify Welcome")
    assert "--road click --url" in str(exc.value)


def test_click_missing_target_refuses():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Click at file:///c.html and verify Welcome")
    assert "--road click --text" in str(exc.value)


def test_click_missing_expect_refuses():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Open file:///c.html, click the Continue button")
    assert "--road click --expect" in str(exc.value)


def test_click_ambiguous_intent_refuses():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Click the Continue button to show details at"
                   " file:///c.html and verify Welcome")
    assert "ambiguous" in str(exc.value)
    assert "click" in str(exc.value) and "observe" in str(exc.value)


def test_click_credential_refuses():
    import pytest
    with pytest.raises(UnknownGoalError):
        shape_goal("Click the password button at file:///c.html"
                   " and verify Welcome")


def test_click_multistep_refuses():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("First click Continue, then verify Welcome"
                   " at file:///c.html")
    assert "--chain-file" in str(exc.value)


def test_click_url_keywords_no_false_intent():
    shaped = shape_goal(
        "Click the Continue button at"
        " file:///nosubmit-search-follow-click.html and verify Welcome")
    assert shaped["click_url"] == \
        "file:///nosubmit-search-follow-click.html"
    assert shaped["click_text"] == "Continue button"
    assert shaped["expect_text"] == "Welcome"


def test_click_deterministic():
    g = ("Open file:///c.html, click the Continue button,"
         " and verify Welcome")
    assert shape_goal(g) == shape_goal(g)


def test_no_model_calls_in_click_shaper():
    import lakra.control.shaping as mod
    src = open(mod.__file__).read()
    assert "models." not in src and "complete(" not in src
    assert "ollama" not in src.lower()


# -- integration: stub rig -----------------------------------------------------


def test_shaped_click_routes_through_dispatcher(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = ("Open file:///c.html, click the Continue button,"
             " and verify Welcome")
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    open_page, read_links, read_controls, read_clicks = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, shaped, ScriptedDecider(True),
                   read_clicks=read_clicks)
    assert res.status == "DONE"
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    kinds = [e["payload"].get("kind") for e in ns["audit"].replay()
             if e["type"] == "ACTION_ALLOWED"]
    assert "browser.click" in kinds
    assert not [e for e in ns["audit"].replay()
                if e["type"].startswith("APPROVAL")]


def test_shaped_click_zero_match_refuses(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = ("Open file:///c.html, click the Launch rocket button,"
             " and verify Space")
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    open_page, read_links, read_controls, read_clicks = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, shaped, ScriptedDecider(True),
                   read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_OUTCOME") == 1


def test_shaped_click_tie_refuses(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = ("Open file:///c.html, click Save draft,"
             " and verify Saved")
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    tied = [("Save draft", "#a"), ("Save draft copy", "#b")]
    open_page, read_links, read_controls, read_clicks = seams(tied)
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, shaped, ScriptedDecider(True),
                   read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0
    assert "ambiguous" in res.detail
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]


def test_shaped_click_submit_phrase_cannot_reach_submit(tmp_path):
    # Submit-kind controls live outside the click inventory (excluded
    # at collection). A phrase naming only such a control finds zero
    # overlap here, so the road refuses before any click. (Prose
    # avoids fill-submit keywords so the test exercises grounding,
    # not intent routing.)
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = ("Open file:///c.html, click the Go button,"
             " and verify Gone")
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    open_page, read_links, read_controls, read_clicks = seams(
        [("Next step", "#m-next")])
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, shaped, ScriptedDecider(True),
                   read_clicks=read_clicks)
    assert res.status == "STOPPED" and res.steps_done == 0
    assert not [e for e in ns["audit"].replay()
                if e["type"] == "ACTION_ALLOWED"]


def test_shaped_click_mixed_keys_refused(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns)
    open_page, read_links, read_controls, read_clicks = seams()
    mixed = {"click_url": "file:///c.html", "click_text": "Continue",
             "expect_text": "Welcome",
             "goal_slots": {"name": {"text": "x"}}}
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


def test_cli_goal_click_done_exit_0(tmp_path):
    proc = run_cli(
        tmp_path, "--goal",
        "Open " + CLICK_URL + ", click the Continue button,"
        " and verify Welcome", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "shaped goal:" in proc.stdout
    assert "task: COMPLETED" in proc.stdout


def test_cli_goal_click_refusals_exit_1(tmp_path):
    cases = [
        "Click the Continue button and verify Welcome",
        "Open " + CLICK_URL + ", click the Continue button",
        "Click at " + CLICK_URL + " and verify Welcome",
        "Click the password button at " + CLICK_URL
        + " and verify Welcome",
        "First click Continue, then verify Welcome at " + CLICK_URL,
    ]
    for goal in cases:
        proc = run_cli(tmp_path, "--goal", goal, "--yes")
        assert proc.returncode == 1, (goal, proc.stdout + proc.stderr)
        assert "refused:" in proc.stdout


def test_cli_goal_click_rejects_road_mix(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Click the Continue button",
                   "--road", "click", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_explicit_road_click_unchanged(tmp_path):
    proc = run_cli(tmp_path, "--road", "click", "--url", CLICK_URL,
                   "--text", "Continue", "--expect", "Welcome", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "road: click" in proc.stdout
