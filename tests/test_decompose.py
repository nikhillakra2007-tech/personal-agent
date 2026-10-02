"""V2-01: deterministic decomposition contract.

Prose -> 2-4 road-goal legs -> the unchanged run_chain(). Pure
unit tests prove split/shape/road-attach/count refusals with no
browser; stub rigs prove decomposed legs execute identically to the
hand-written equivalent; live CLI smokes prove end-to-end chains,
pre-launch refusals, and hand-written equivalence on fixtures.
No model calls anywhere; the dispatcher/policy/approvals are never
touched by the decomposer.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.chain import run_chain, validate_chain
from lakra.control.decompose import (
    DecomposeRefused,
    decompose_goal,
    road_of_shaped,
    split_goal,
)
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.planner import UnknownGoalError
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
sys.path.insert(0, str(ROOT / "scripts"))
FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = (FIXTURES / "loop-index.html").as_uri()
BETA_URL = (FIXTURES / "loop-beta.html").as_uri()
GAMMA_URL = (FIXTURES / "loop-gamma.html").as_uri()
CLICK_URL = (FIXTURES / "click.html").as_uri()
SEARCH_URL = (FIXTURES / "search.html").as_uri()

P2 = ("Show me the Records page at " + LIST_URL + " with Records,"
      " then follow the Beta record on " + LIST_URL +
      " and show me detail record")
P3 = (P2 + ", then show me the beta page at " + BETA_URL +
      " with detail record")
P4 = (P3 + ", then show me the gamma page at " + GAMMA_URL +
      " with detail record")
P_CLICK = ("Show me the Welcome page at " + CLICK_URL + " with Welcome,"
           " then open " + CLICK_URL +
           ", click the Continue button, and verify Welcome,"
           " then show me the Welcome page at " + CLICK_URL +
           " with Welcome")
P_SEARCH = ("Web search for cathedrals at " + SEARCH_URL +
            " and confirm Cathedral results,"
            " then show me the search page at " + SEARCH_URL +
            " with Cathedral results")


def hand_written_oco():
    return [
        {"road": "observe", "url": LIST_URL,
         "expect_text": "Records"},
        {"road": "follow", "list_url": LIST_URL,
         "goal_text": "Beta record", "body_expect": "detail record"},
        {"road": "observe", "url": BETA_URL, "expect_text": "detail"},
    ]


# -- pure contract ---------------------------------------------------------------

def test_two_leg_decomposition():
    legs = decompose_goal(P2)
    assert [leg["road"] for leg in legs] == ["observe", "follow"]
    assert legs[0] == {"road": "observe", "url": LIST_URL,
                       "expect_text": "Records"}
    assert legs[1] == {"road": "follow", "list_url": LIST_URL,
                       "goal_text": "Beta record",
                       "body_expect": "detail record"}


def test_three_leg_decomposition():
    legs = decompose_goal(P3)
    assert [leg["road"] for leg in legs] == ["observe", "follow",
                                            "observe"]
    assert legs[2] == {"road": "observe", "url": BETA_URL,
                       "expect_text": "detail"}


def test_four_leg_decomposition():
    legs = decompose_goal(P4)
    assert [leg["road"] for leg in legs] == ["observe", "follow",
                                            "observe", "observe"]
    assert legs[3]["url"] == GAMMA_URL


def test_click_and_search_roads():
    click_legs = decompose_goal(P_CLICK)
    assert [leg["road"] for leg in click_legs] == ["observe", "click",
                                                  "observe"]
    assert click_legs[1]["click_text"] == "Continue button"
    search_legs = decompose_goal(P_SEARCH)
    assert [leg["road"] for leg in search_legs] == ["search",
                                                   "observe"]
    # Submit grounding stays at the execution edge: phrase, never a
    # selector, in pure output.
    assert search_legs[0]["submit_phrase"] == "search"
    assert "submit_selector" not in search_legs[0]


def test_empty_decomposition_refused():
    for bad in ("", "   ", 42, None):
        with pytest.raises(DecomposeRefused):
            decompose_goal(bad)


def test_single_leg_refused():
    with pytest.raises(DecomposeRefused) as exc:
        decompose_goal("Show me the Records page at " + LIST_URL +
                       " with Records")
    assert "2..4" in str(exc.value)


def test_overlong_decomposition_refused():
    with pytest.raises(DecomposeRefused) as exc:
        decompose_goal(P4 + ", then show me the beta page at " +
                       BETA_URL + " with detail record")
    assert "2..4" in str(exc.value)


def test_unsupported_road_refused():
    with pytest.raises(DecomposeRefused) as exc:
        decompose_goal("Submit the form at file:///f.html,"
                       " then show me the Records page at " +
                       LIST_URL + " with Records")
    assert "slots-json" in str(exc.value)


def test_malformed_leg_refused():
    with pytest.raises(DecomposeRefused):
        decompose_goal("Follow the Beta record on " + LIST_URL +
                       ", then show me the beta page at " + BETA_URL +
                       " with detail record")


def test_empty_segment_refused():
    with pytest.raises(DecomposeRefused) as exc:
        decompose_goal("Show me the Records page at " + LIST_URL +
                       " with Records;; show me the beta page at " +
                       BETA_URL + " with detail record")
    assert "empty leg segment" in str(exc.value)


def test_ambiguous_segment_refused():
    with pytest.raises(DecomposeRefused) as exc:
        decompose_goal("Follow the Beta record and submit the form"
                       " on " + LIST_URL + " and show me the detail,"
                       " then show me the beta page at " + BETA_URL +
                       " with detail record")
    assert "leg 1" in str(exc.value)


def test_credential_segment_refused_credential():
    with pytest.raises(DecomposeRefused) as exc:
        decompose_goal("Fill the password at file:///v.html"
                       " with secret hunter2 and submit,"
                       " then show me the Records page at " + LIST_URL +
                       " with Records")
    assert "credential-laden" in str(exc.value)


def test_no_invented_selectors():
    for legs in (decompose_goal(P3), decompose_goal(P_CLICK),
                 decompose_goal(P_SEARCH)):
        for leg in legs:
            assert "selector" not in leg
            assert "submit_selector" not in leg
            for value in leg.values():
                assert not (isinstance(value, str)
                            and value.startswith("#"))


def test_road_of_shaped_rejects_foreign_keys():
    with pytest.raises(DecomposeRefused):
        road_of_shaped({"url": "u", "teleport": True})
    with pytest.raises(DecomposeRefused):
        road_of_shaped("not-a-mapping")


def test_first_prefix_and_periods_tolerated():
    legs = decompose_goal("First show me the Records page at " +
                          LIST_URL + " with Records. Then show me"
                          " the beta page at " + BETA_URL +
                          " with detail record.")
    assert [leg["road"] for leg in legs] == ["observe", "observe"]


def test_split_goal_semicolons():
    assert split_goal("Do A; do B") == ["Do A", "do B"]


# -- stub equivalence: decomposed == hand-written ---------------------------------

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")
PAIRS = [("Beta record", "beta.html")]


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
                return StepResult(False, False, 1, "ESCALATE",
                                  "ask-pending")
            if (res.error or "").startswith("BLOCKED"):
                return StepResult(False, False, 1, "ESCALATE",
                                  "blocked")
            return StepResult(False, False, 1, "ESCALATE",
                              "router-refused")
        return StepResult(True, True, 1, "CONTINUE", "verified")


class HealthyMonitor:
    def sample(self, force=False):
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384, ram_available_mb=8192,
            cpu_percent=5.0, lakra_rss_mb=50.0, browser_rss_mb=0.0,
            gpu_available=False)

    def under_pressure(self, **kw):
        return False, "ok"


def stub_rig(tmp_path, name="d.db", audit_name="a.jsonl"):
    db = Database(tmp_path / name)
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / audit_name)
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    for kind in KINDS:
        tools.register(kind, Stub())
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = FakeController(router)
    guards = Guards(scheduler=sched, monitor=HealthyMonitor())
    runner = Runner(ctrl, sched, audit, store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit,
            "sched": sched, "loop": loop}


def start(ns, goal="Composed legs"):
    t = ns["registry"].add(Task.create(
        goal, allowed_tools=["browser"], allowed_domains=["file:"],
        allowed_paths=[]))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def test_decomposed_matches_hand_written_chain(tmp_path):
    legs = decompose_goal(P3)
    assert legs == hand_written_oco()
    open_page = lambda url: None  # noqa: E731
    read_links = lambda: list(PAIRS)  # noqa: E731
    read_controls = lambda: []  # noqa: E731
    got = []
    for i, chain in (enumerate(({"legs": legs},
                                 {"legs": hand_written_oco()}))):
        ns = stub_rig(tmp_path, name=f"d{i}.db",
                      audit_name=f"a{i}.jsonl")
        t = start(ns)
        res = run_chain(ns["loop"], ns["db"], open_page, read_links,
                        read_controls, t, chain,
                        ScriptedDecider(True))
        got.append((res.status, res.legs_done, res.steps_done,
                    len([e for e in ns["audit"].replay()
                         if e["type"] == "PLAN_CREATED"])))
        ns["db"].close()
    assert got[0] == got[1] and got[0][0] == "DONE"


# -- live CLI ----------------------------------------------------------------------

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


def audit_types(tmp_path):
    types = {}
    try:
        lines = (tmp_path / "audit-do.jsonl").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return types
    for line in lines:
        t = json.loads(line).get("type")
        types[t] = types.get(t, 0) + 1
    return types


def task_state(tmp_path):
    import os
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / "cli.db")
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--audit", str(tmp_path / "audit-do.jsonl"),
         "--profile-dir", str(tmp_path / "prof-s"),
         "--shots-dir", str(tmp_path / "shots-s"),
         "--ram-floor-mb", "0", "--cpu-ceiling", "100",
         "--list", "--state", "all", "--format", "json"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    return json.loads(out.stdout)["tasks"]


def test_cli_decompose_observe_follow_observe(tmp_path):
    proc = run_cli(tmp_path, "--decompose", P3, "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "decomposed: 3 legs (observe, follow, observe)" in \
        proc.stdout
    assert task_state(tmp_path)[0]["status"] == "COMPLETED"
    assert audit_types(tmp_path).get("PLAN_CREATED") == 3


def test_cli_decompose_matches_hand_written_chain(tmp_path):
    import os
    hand = tmp_path / "hand.json"
    hand.write_text(json.dumps({"legs": hand_written_oco()}),
                    encoding="utf-8")
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / "cli.db")

    def status_of(args):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
             "--audit", str(tmp_path / "audit-do.jsonl"),
             "--profile-dir", str(tmp_path / "prof-q"),
             "--shots-dir", str(tmp_path / "shots-q"),
             "--ram-floor-mb", "0", "--cpu-ceiling", "100",
             *args], capture_output=True, text=True, cwd=str(ROOT),
            env=env, timeout=240)
        return out

    first = status_of(["--chain-file", str(hand), "--yes"])
    assert first.returncode == 0, first.stdout + first.stderr
    second = status_of(["--decompose", P3, "--yes"])
    assert second.returncode == 0, second.stdout + second.stderr
    # Same roads, same ordering, same per-leg outcomes on both tasks.
    for tid_out in (first.stdout, second.stdout):
        assert "status: DONE" in tid_out
    procs = [status_of(["--status", t["task_id"]])
             for t in task_state(tmp_path)]
    assert len(procs) == 2
    for p in procs:
        assert "legs: 3/3 done" in p.stdout
        assert "1. observe - DONE" in p.stdout
        assert "2. follow - DONE" in p.stdout
        assert "3. observe - DONE" in p.stdout


def test_cli_decompose_invalid_refused_before_execution(tmp_path):
    proc = run_cli(tmp_path, "--decompose",
                   "Summon the harbor ghost then befriend the fog",
                   "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "refused:" in proc.stdout
    assert "PLAN_CREATED" not in audit_types(tmp_path)
    assert task_state(tmp_path) == []


def test_cli_decompose_from_leg(tmp_path):
    proc = run_cli(tmp_path, "--decompose", P3, "--from-leg", "2",
                    "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert audit_types(tmp_path).get("PLAN_CREATED") == 2


def test_cli_decompose_flag_matrix(tmp_path):
    proc = run_cli(tmp_path, "--decompose", P3, "--road", "follow",
                    "--url", LIST_URL, "--text", "x", "--expect", "y",
                    "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout
