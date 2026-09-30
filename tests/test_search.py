"""Slice-44: composed search road (deterministic, L3-gated).

run_search_task() composes goal -> open_page -> read_controls ->
ground_fields({"search": query}) -> TaskLoop.run_goal() through the
search template: navigate, fill the grounded query box, propose the
search at the identical L3 submit gate (policy unchanged: every
search parks once for approval with the exact query and domain
visible), then verify the results marker. No model, no new
executors, predicates, policy, hint keys, or audit event types.

Stub rigs prove refusal mapping + L3 gate behavior (real
router/policy/token path, stub executors). Planner tests prove
"web search" routing precedence and byte-identical behavior for all
pre-existing goals. Live tests prove approve/deny/tie on fixtures.
One CLI smoke proves --road search end to end."""

import subprocess
import sys
import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import run_search_task, run_task
from lakra.control.planner import Planner, UnknownGoalError
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

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_URL = (FIXTURES / "search.html").as_uri()
TIE_URL = (FIXTURES / "search-tie.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit", "browser.check",
         "browser.select")

SEARCH_CONTROLS = [("Search", "text", "#q-search")]
TIE_CONTROLS = [("Site search", "text", "#t-site"),
                ("Archive search", "text", "#t-archive")]


class Stub:
    def __init__(self):
        self.calls = 0

    def execute(self, action, task):
        self.calls += 1
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
    db = Database(tmp_path / "search.db")
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


def start(ns, goal="Web search cathedrals", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def full_goal(**kw):
    goal = {"search_url": "file:///s.html",
            "query": {"text": "cathedrals"},
            "submit_selector": "#q-go",
            "expect_text": "Cathedral results"}
    goal.update(kw)
    return goal


def opened_seam(opened):
    def open_page(url):
        opened.append(url)

    return open_page


# -- stub refusals ------------------------------------------------------------

def test_search_refusals_before_plan(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    opened = []

    def run(goal, controls=None):
        return run_search_task(
            ns["loop"], opened_seam(opened),
            lambda: list(controls if controls is not None else []),
            t, goal, decider)

    for bad in ({"search_url": "file:///s.html"},
                {"query": {"text": "cathedrals"}}):
        r = run(dict(bad))
        assert r.status == "STOPPED" and "malformed goal" in r.detail
        assert r.plan_id == "-" and r.steps_done == 0

    r = run("not-a-mapping")
    assert r.status == "STOPPED" and "malformed goal" in r.detail

    def boom(url):
        raise OSError("network down")

    r = run_search_task(ns["loop"], boom, lambda: list(SEARCH_CONTROLS),
                        t, full_goal(), decider)
    assert r.status == "STOPPED" and "cannot open search page" in r.detail

    def inv_boom():
        raise OSError("snapshot lost")

    r = run_search_task(ns["loop"], opened_seam(opened), inv_boom,
                        t, full_goal(), decider)
    assert r.status == "STOPPED" and "controls failed" in r.detail

    # Tie: two search-labeled boxes, neither wins, never guessed.
    r = run(full_goal(), TIE_CONTROLS)
    assert r.status == "STOPPED" and "no groundable search box" in r.detail
    assert "ambiguous" in r.detail

    # Zero overlap: no search-worded control observed.
    r = run(full_goal(), [("Full name", "text", "#m-name")])
    assert r.status == "STOPPED" and "no groundable search box" in r.detail

    # Secret-shaped query text is refused before anything executes.
    r = run(full_goal(query={"text": "my password is x"}),
            SEARCH_CONTROLS)
    assert r.status == "STOPPED" and "no groundable search box" in r.detail

    # Non-text query shape is refused, not coerced.
    r = run(full_goal(query="cathedrals"), SEARCH_CONTROLS)
    assert r.status == "STOPPED" and "no groundable search box" in r.detail

    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" not in types
    assert "VERIFICATION" not in types
    assert not [x for x in types if x.startswith("APPROVAL")]
    assert not [x for x in types if x.startswith("LOOP")]


# -- stub L3 gate (policy unchanged: every search parks once) -------------------

def test_search_stub_approve_done(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)
    opened = []
    res = run_search_task(ns["loop"], opened_seam(opened),
                          lambda: list(SEARCH_CONTROLS),
                          t, full_goal(), decider)
    assert opened == ["file:///s.html"]
    assert (res.status, res.steps_done) == ("DONE", 4)
    # The L3 submit gate fired exactly once through the composed path.
    assert len(decider.seen) == 1
    consumed = [e for e in ns["audit"].replay()
                if e["type"] == "APPROVAL_CONSUMED"]
    assert len(consumed) == 1
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" in types and "PLAN_OUTCOME" in types
    assert not [x for x in types if x.startswith("LOOP")]


def test_search_stub_deny_holds(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    res = run_search_task(ns["loop"], lambda url: None,
                          lambda: list(SEARCH_CONTROLS),
                          t, full_goal(), ScriptedDecider(False))
    assert res.status == "STOPPED" and "denied" in res.detail
    assert ns["registry"].get(t.task_id).status == Status.CANCELLED
    mine = [e for e in ns["audit"].replay()
            if e["task_id"] == t.task_id]
    assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]


# -- template routing (precedence + byte-identical history) ------------------------

def _plan_for(goal_text, hints):
    t = Task.create(goal_text, allowed_tools=["browser"],
                    allowed_domains=["file:"], allowed_paths=[])
    return Planner().plan(t, hints)


def test_search_template_routing_and_history(tmp_path):
    _ = tmp_path
    plan = _plan_for("Web search cathedrals",
                     {"url": "file:///s.html", "selector": "#q",
                      "text": "cathedrals", "submit_selector": "#g",
                      "expect_text": "Cathedral results"})
    assert len(plan.steps) == 4
    assert [s.action.kind for s in plan.steps] == [
        "browser.navigate", "browser.type", "browser.submit",
        "browser.snapshot"]
    assert plan.steps[2].max_retries == 0  # the L3 gate never retries
    assert plan.steps[3].expect.kind == "text_contains"

    # Pre-existing goals route exactly as before (byte-identical).
    assert len(_plan_for(
        "Submit the demo form",
        {"url": "u", "selector": "#f", "text": "t",
         "submit_selector": "#s"}).steps) == 3
    assert len(_plan_for("Show the courses",
                         {"url": "u",
                          "expect_text": "e"}).steps) == 2
    assert len(_plan_for("Click the toggle",
                         {"selector": "#t"}).steps) == 1
    assert len(_plan_for("Follow the Beta record",
                         {"url": "u", "link_text": "b",
                          "expect_text": "e"}).steps) == 4
    # "Research …" does NOT route to search (substring guard): it
    # refuses instead of misrouting into a search submission.
    try:
        _plan_for("Research cathedrals",
                  {"url": "u", "expect_text": "e"})
    except UnknownGoalError:
        pass
    else:
        raise AssertionError("Research goal must not route anywhere")


# -- template/intent alignment guard ---------------------------------------------------
# Slice-44 lesson: analyzer.INTENT_NAMES is zipped positionally with
# planner.TEMPLATES, so adding a template without extending the names
# silently remaps every intent. This fails loudly instead.

def test_intent_names_match_templates():
    from lakra.control.analyzer import INTENT_NAMES
    from lakra.control.planner import TEMPLATES
    assert len(INTENT_NAMES) == len(TEMPLATES)
    assert INTENT_NAMES[0] == "search"
    assert TEMPLATES[0][0] == ("web search",)


# -- dispatcher ---------------------------------------------------------------------

def test_dispatch_search_road_and_mixes(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    decider = ScriptedDecider(True)

    def boom_links():
        raise AssertionError("search road reads no link inventory")

    opened = []
    res = run_task(ns["loop"], ns["db"], opened_seam(opened), boom_links,
                   lambda: list(SEARCH_CONTROLS),
                   t, full_goal(), decider)
    assert (res.status, res.steps_done) == ("DONE", 4)
    assert opened == ["file:///s.html"]

    for bad in (dict(full_goal(), max_items=1, max_iters=1),
                dict(full_goal(), goal_slots={"a": {"text": "b"}}),
                dict(full_goal(), list_url="file:///l.html",
                     goal_text="x", body_expect="y"),
                dict(full_goal(), url="file:///u.html")):
        r = run_task(ns["loop"], ns["db"], opened_seam(opened), boom_links,
                     lambda: list(SEARCH_CONTROLS),
                     t, bad, decider)
        assert r.status == "STOPPED" and "mixed road keys" in r.detail


# -- live search ----------------------------------------------------------------------

def _live_ns(tmp_path, goal="Web search cathedrals"):
    db = Database(tmp_path / "search-live.db")
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


def test_live_search_approve_done(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        decider = ScriptedDecider(True)
        read_controls = lambda: list(collect_controls(  # noqa: E731
            ns["hands"].page))
        res = run_search_task(
            ns["taskloop"], ns["hands"].open, read_controls,
            ns["task"], {"search_url": SEARCH_URL,
                         "query": {"text": "cathedrals"},
                         "submit_selector": "#q-go",
                         "expect_text": "Cathedral results"},
            decider)
        assert (res.status, res.steps_done) == ("DONE", 4)
        assert len(decider.seen) == 1  # one human gate per search
        page = ns["hands"].page
        assert page.locator("#q-search").input_value() == "cathedrals"
        assert "Cathedral results" in (
            page.locator("body").inner_text() or "")
        assert page.locator("#q-results").inner_text() == \
            "Cathedral results: nave, tower"
        types = [e["type"] for e in ns["audit"].replay()]
        assert types.count("APPROVAL_CONSUMED") == 1
        assert "APPROVED_STEP_EXECUTED" in types
        assert all(e["payload"].get("result") == "PASS"
                   for e in ns["audit"].replay()
                   if e["type"] == "VERIFICATION")
        # The exact query traveled untouched into the typed action.
        assert any("#q-search\n---\ncathedrals" in
                   (e.get("payload") or {}).get("target", "")
                   for e in ns["audit"].replay()
                   if e["type"] == "ACTION_ALLOWED")
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_search_deny_holds(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        read_controls = lambda: list(collect_controls(  # noqa: E731
            ns["hands"].page))
        res = run_search_task(
            ns["taskloop"], ns["hands"].open, read_controls,
            ns["task"], {"search_url": SEARCH_URL,
                         "query": {"text": "cathedrals"},
                         "submit_selector": "#q-go",
                         "expect_text": "Cathedral results"},
            ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.CANCELLED
        page = ns["hands"].page
        # Query was entered (safe prefix ran); submit never fired.
        assert page.locator("#q-search").input_value() == "cathedrals"
        assert page.locator("#q-results").inner_text() == "no results yet"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == ns["task"].task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_search_tie_refuses(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        read_controls = lambda: list(collect_controls(  # noqa: E731
            ns["hands"].page))
        res = run_search_task(
            ns["taskloop"], ns["hands"].open, read_controls,
            ns["task"], {"search_url": TIE_URL,
                         "query": {"text": "harbor"},
                         "submit_selector": "#t-go",
                         "expect_text": "whatever"},
            ScriptedDecider(True))
        assert res.status == "STOPPED"
        assert "ambiguous" in res.detail
        assert res.plan_id == "-" and res.steps_done == 0
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- live CLI smoke ---------------------------------------------------------------------

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


def test_cli_search_done_exit_0(tmp_path):
    proc = run_cli(tmp_path, "--road", "search", "--url", SEARCH_URL,
                   "--query", "cathedrals", "--submit", "#q-go",
                   "--expect", "Cathedral results", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "task: COMPLETED" in proc.stdout


def test_cli_search_bad_mix_exit_1(tmp_path):
    proc = run_cli(tmp_path, "--road", "search", "--url", SEARCH_URL,
                   "--query", "cathedrals", "--submit", "#q-go",
                   "--expect", "Cathedral results",
                   "--max-items", "2", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "usage error" in proc.stdout
