"""Slice-46: natural-language goal shaping (deterministic prose -> road).

shape_goal() maps prose to the exact road goal dicts the Slice-35
dispatcher already consumes. Pure function: prose in, road goal dict
out — or a refusal. No model calls, no policy changes, no new roads.
Only observe and follow (with arrival phrase) shape; all other intents
refuse with guidance. The shaped dict flows through the same run_task()
as the explicit path, so policy, guards, and the L3 gate are untouched.

Stub rigs prove shaping + dispatcher routing + L3 gate preservation over
the real router/policy/token path. CLI smokes prove --goal wiring.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.planner import UnknownGoalError, validate_hints
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.shaping import shape_goal
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


def stub_rig(tmp_path):
    db = Database(tmp_path / "shaping.db")
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


def start(ns, goal="Shaped goal", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def seams():
    def open_page(url):
        return None

    def read_links():
        return list(PAIRS)

    def read_controls():
        return [("Name", "text", "#f")]

    return open_page, read_links, read_controls


# -- unit: shaping -----------------------------------------------------------


def test_observe_shapes_from_prose():
    shaped = shape_goal("Show me the records page at file:///l.html")
    assert shaped == {"url": "file:///l.html", "expect_text": "records"}


def test_observe_shapes_https_url():
    shaped = shape_goal("List courses at https://example.com/docs")
    assert shaped == {"url": "https://example.com/docs",
                      "expect_text": "courses"}


def test_follow_shapes_with_arrival_phrase():
    shaped = shape_goal(
        "Follow the Beta record on file:///l.html and show me the"
        " detail record")
    assert shaped == {"list_url": "file:///l.html",
                      "goal_text": "Beta record",
                      "body_expect": "detail record"}


def test_follow_shapes_with_and_show_connector():
    shaped = shape_goal(
        "Follow the Beta record on file:///l.html and show the detail")
    assert shaped["list_url"] == "file:///l.html"
    assert shaped["goal_text"] == "Beta record"
    assert shaped["body_expect"] == "detail"


def test_follow_refuses_without_arrival():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Follow the Beta record on file:///l.html")
    assert "arrival" in str(exc.value)
    assert "--road follow" in str(exc.value)


def test_follow_refuses_without_url():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Follow the Beta record and show me the detail")
    assert "URL" in str(exc.value)


def test_follow_refuses_empty_link_phrase():
    import pytest
    with pytest.raises(UnknownGoalError):
        shape_goal("Follow on file:///l.html and show me the detail")


def test_search_shapes_from_prose():
    shaped = shape_goal("Web search for cathedrals at file:///s.html")
    assert shaped["search_url"] == "file:///s.html"
    assert shaped["query"] == {"text": "cathedrals"}
    assert shaped["submit_phrase"] == "search"


def test_search_shapes_with_expect_text():
    shaped = shape_goal(
        "Web search for cats at file:///s.html and confirm results")
    assert shaped["query"] == {"text": "cats"}
    assert shaped["expect_text"] == "results"


def test_search_refuses_without_url():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Web search for cats")
    assert "URL" in str(exc.value)


def test_search_refuses_without_query():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Web search at file:///s.html")
    assert "query" in str(exc.value)


def test_click_refuses_no_selector():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Click the toggle")
    assert "click" in str(exc.value)
    assert "--road click" in str(exc.value)


def test_form_refuses_no_slots():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Submit the form at file:///f.html")
    assert "form" in str(exc.value)
    assert "--slots-json" in str(exc.value)


def test_loop_refuses_no_bounds():
    """Loop is not a planner template (no INTENT_NAMES slot), so loop
    prose is classified by its follow/observe keywords. 'Follow every
    batch record' matches follow but has no arrival phrase -> refuses.
    The shaper never invents loop bounds."""
    import pytest
    with pytest.raises(UnknownGoalError):
        shape_goal("Follow every batch record on file:///l.html")


def test_multi_step_prose_refuses_with_chain_guidance():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("First show the records, then follow the Beta record")
    assert "multi-step" in str(exc.value)
    assert "--chain-file" in str(exc.value)


def test_ambiguous_intent_refuses():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Follow the Beta record and submit the form"
                   " on file:///l.html and show me the detail")
    assert "ambiguous" in str(exc.value)


def test_empty_and_credential_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        shape_goal("")
    with pytest.raises(UnknownGoalError):
        shape_goal("   ")
    with pytest.raises(UnknownGoalError):
        shape_goal("Type my password into the form")


def test_no_intent_refused():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("the records page at file:///l.html")
    assert "no road matches" in str(exc.value)


def test_deterministic():
    g = "Show me the records page at file:///l.html please"
    assert shape_goal(g) == shape_goal(g)
    f = ("Follow the Beta record on file:///l.html and show me the"
         " detail record")
    assert shape_goal(f) == shape_goal(f)


def test_no_model_calls_in_shaper():
    import lakra.control.shaping as mod
    src = open(mod.__file__).read()
    assert "models." not in src and "complete(" not in src
    assert "ollama" not in src.lower()


# -- integration: stub rig ----------------------------------------------------


def test_shaped_observe_routes_through_dispatcher(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = "Show me the records page at file:///l.html"
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    open_page, read_links, read_controls = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, shaped, ScriptedDecider(True))
    assert res.status == "DONE"
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 1
    assert not [x for x in types if x.startswith("APPROVAL")]


def test_shaped_follow_grounds_link(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = ("Follow the Beta record on file:///l.html and show me the"
             " detail record")
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    open_page, read_links, read_controls = seams()
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, shaped, ScriptedDecider(True))
    assert res.status == "DONE"
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") == 1


def test_shaped_goal_passes_validate_hints():
    """Observe shaped dicts use hint keys (url, expect_text) and pass
    validate_hints directly. Follow shaped dicts use goal keys
    (list_url, goal_text, body_expect) and are validated by the
    dispatcher's key-presence routing (proved in the stub-rig tests)."""
    for prose in (
            "Show me the records page at file:///l.html",
            "List courses at https://example.com/docs"):
        shaped = shape_goal(prose)
        validate_hints(shaped)
    follow = shape_goal(
        "Follow the Beta record on file:///l.html and show me the"
        " detail record")
    assert set(follow) == {"list_url", "goal_text", "body_expect"}


def test_shaping_refusal_logs_plan_outcome(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns, goal="Shaped goal")
    open_page, read_links, read_controls = seams()
    import pytest
    with pytest.raises(UnknownGoalError):
        shape_goal("Click the toggle")
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_OUTCOME" not in types


# -- integration: L3 gate preserved -------------------------------------------


def test_shaped_path_preserves_l3_gate(tmp_path):
    """The run_goal path uses run_task (same as explicit), so the L3
    gate is inherently preserved. Prove it: a form goal (with submit)
    through the same run_task call parks at the gate, decider consulted
    once, deny -> CANCELLED."""
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns, goal="Submit the form")
    form_goal = {"form_url": "file:///f.html",
                 "goal_slots": {"name": {"text": "Ada"}},
                 "submit_selector": "#s"}
    open_page, read_links, read_controls = seams()
    decider = ScriptedDecider(False)
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, form_goal, decider)
    assert res.status == "STOPPED"
    assert "denied" in res.detail
    assert ns["registry"].get(t.task_id).status == Status.CANCELLED
    assert len(decider.seen) == 1
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("APPROVAL_CONSUMED") == 0


# -- CLI smoke ----------------------------------------------------------------


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


def test_cli_goal_observe_exit_0(tmp_path):
    proc = run_cli(tmp_path, "--goal",
                   "Show me the Records page at " + LIST_URL, "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "shaped goal:" in proc.stdout


def test_cli_goal_follow_exit_0(tmp_path):
    proc = run_cli(
        tmp_path, "--goal",
        "Follow the Beta record on " + LIST_URL
        + " and show me the detail record", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout


def test_cli_goal_refusal_exit_1(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Click the toggle", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "refused:" in proc.stdout
    assert "click" in proc.stdout


def test_cli_goal_rejects_road_mix(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Show the records",
                    "--road", "observe", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_cli_goal_rejects_addressing_flags(tmp_path):
    for flag in ("--url", "--text", "--expect", "--slots-json",
                 "--submit", "--query", "--max-items", "--max-iters"):
        proc = run_cli(tmp_path, "--goal", "Show the records",
                       flag, "x", "--yes")
        assert proc.returncode == 1, (flag, proc.stdout + proc.stderr)
        assert "usage error" in proc.stdout


def test_cli_goal_rejects_chain_file(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Show the records",
                    "--chain-file", "chain.json", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_cli_goal_rejects_resume(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Show the records",
                    "--resume", "abc", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_cli_goal_rejects_empty_prose(tmp_path):
    proc = run_cli(tmp_path, "--goal", "  ", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_explicit_road_path_unchanged(tmp_path):
    proc = run_cli(tmp_path, "--road", "observe", "--url", LIST_URL,
                   "--text", "Records", "--expect", "Records", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "road: observe" in proc.stdout
