"""Slice-47: NL search shaping (deterministic prose -> search road).

shape_goal() extends to search intent: query extraction, URL extraction,
and submit-control grounding (via ground_submit against observed submits).
The shaped dict flows through the same run_task() -> dispatcher -> search
road -> L3 gate as explicit --road search. No model calls, no policy
changes, no new roads. The shaper never invents a selector — submit
grounding is unique-winner-or-refuse.

Stub rigs prove shaping + dispatcher routing + L3 gate preservation over
the real router/policy/token path. CLI smokes prove --goal search wiring.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from lakra.control.analyzer import ground_submit
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
SEARCH_URL = (FIXTURES / "search-nl.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

PAIRS = [("Beta record", "beta.html")]

SUBMITS = [("Search", "submit", "#s-go"),
           ("Go", "submit", "#s-go2")]


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
    db = Database(tmp_path / "search-shaping.db")
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


def start(ns, goal="Shaped search", **kw):
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
        return [("Search", "text", "#q")]

    return open_page, read_links, read_controls


# -- unit: search shaping -----------------------------------------------------


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


def test_search_shapes_without_expect_text():
    shaped = shape_goal("Web search for cats at file:///s.html")
    assert shaped["expect_text"] == ""


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


def test_search_refuses_ambiguous_intent():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("Web search for cats and submit the form"
                   " at file:///s.html")
    assert "ambiguous" in str(exc.value)


def test_search_refuses_multi_step():
    import pytest
    with pytest.raises(UnknownGoalError) as exc:
        shape_goal("First web search for cats, then follow the Beta record")
    assert "multi-step" in str(exc.value)


def test_search_refuses_credential():
    import pytest
    with pytest.raises(UnknownGoalError):
        shape_goal("Web search for my password at file:///s.html")


def test_search_deterministic():
    g = "Web search for cats at file:///s.html and confirm results"
    assert shape_goal(g) == shape_goal(g)


# -- unit: ground_submit ------------------------------------------------------


def test_ground_submit_unique_winner():
    assert ground_submit("search", SUBMITS) == "#s-go"


def test_ground_submit_zero_match_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_submit("xyzzy", SUBMITS)


def test_ground_submit_tie_refused():
    import pytest
    submits = [("Search", "submit", "#a"), ("Search", "submit", "#b")]
    with pytest.raises(UnknownGoalError) as exc:
        ground_submit("search", submits)
    assert "ambiguous" in str(exc.value)


def test_ground_submit_wrong_kind_refused():
    import pytest
    submits = [("Search", "text", "#q")]
    with pytest.raises(UnknownGoalError):
        ground_submit("search", submits)


def test_ground_submit_empty_inventory_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_submit("search", [])


def test_ground_submit_malformed_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_submit("search", [("Search", "submit")])


def test_ground_submit_credential_refused():
    import pytest
    with pytest.raises(UnknownGoalError):
        ground_submit("my password", SUBMITS)


def test_ground_submit_deterministic():
    assert ground_submit("search", SUBMITS) == ground_submit("search", SUBMITS)


def test_no_model_calls_in_ground_submit():
    import lakra.control.analyzer as mod
    src = open(mod.__file__).read()
    assert "models." not in src and "complete(" not in src
    assert "ollama" not in src.lower()


# -- integration: stub rig -----------------------------------------------------


def test_shaped_search_routes_through_dispatcher(tmp_path):
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    prose = "Web search for records at file:///s.html and confirm results"
    t = start(ns, goal=prose)
    shaped = shape_goal(prose)
    shaped["submit_selector"] = "#s-go"
    open_page, read_links, read_controls = seams()
    run_task(ns["loop"], ns["db"], open_page, read_links,
             read_controls, t, shaped, ScriptedDecider(True))
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("PLAN_CREATED") >= 1


def test_shaped_search_passes_validate_hints():
    shaped = shape_goal(
        "Web search for cats at file:///s.html and confirm results")
    shaped["submit_selector"] = "#s-go"
    validate_hints({"url": shaped["search_url"],
                    "selector": "#q", "text": "cats",
                    "submit_selector": "#s-go",
                    "expect_text": shaped["expect_text"]})


def test_shaped_search_l3_gate_preserved(tmp_path):
    """The run_goal path uses run_task (same as explicit), so the L3
    gate is inherently preserved. Prove it: a search goal through the
    same run_task call parks at the gate, decider consulted once,
    deny -> CANCELLED."""
    from lakra.control.loops import run_task
    ns = stub_rig(tmp_path)
    t = start(ns, goal="Web search for cats")
    search_goal = {"search_url": "file:///s.html",
                   "query": {"text": "cats"},
                   "submit_selector": "#s-go",
                   "expect_text": "results"}
    open_page, read_links, read_controls = seams()
    decider = ScriptedDecider(False)
    res = run_task(ns["loop"], ns["db"], open_page, read_links,
                   read_controls, t, search_goal, decider)
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


def test_cli_goal_search_exit_0(tmp_path):
    proc = run_cli(
        tmp_path, "--goal",
        "Web search for Records at " + SEARCH_URL
        + " and confirm results for Records", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "shaped goal:" in proc.stdout


def test_cli_goal_search_refusal_no_url(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Web search for cats", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "refused:" in proc.stdout


def test_cli_goal_search_refusal_no_query(tmp_path):
    proc = run_cli(tmp_path, "--goal",
                   "Web search at " + SEARCH_URL, "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "refused:" in proc.stdout


def test_cli_goal_search_rejects_road_mix(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Web search for cats",
                   "--road", "search", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_cli_goal_search_rejects_addressing_flags(tmp_path):
    for flag in ("--url", "--query", "--submit", "--expect",
                 "--text", "--slots-json", "--max-items",
                 "--max-iters"):
        proc = run_cli(tmp_path, "--goal", "Web search for cats",
                       flag, "x", "--yes")
        assert proc.returncode == 1, (flag, proc.stdout + proc.stderr)
        assert "usage error" in proc.stdout


def test_cli_goal_search_rejects_chain_file(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Web search for cats",
                   "--chain-file", "c.json", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_cli_goal_search_rejects_resume(tmp_path):
    proc = run_cli(tmp_path, "--goal", "Web search for cats",
                   "--resume", "abc", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout


def test_explicit_road_search_unchanged(tmp_path):
    proc = run_cli(tmp_path, "--road", "search", "--url", SEARCH_URL,
                   "--query", "Records", "--submit", "#nl-q-go",
                   "--expect", "Records", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "road: search" in proc.stdout
