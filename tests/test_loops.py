"""Slice-29A: cursor-unrolled repetition over inventoried links.

Stub rigs prove spec validation, caps, precheck, first_empty,
pause/resume, terminal-refusal, and per-iteration L3 approval (real
one-shot token path through the real router). One live test proves the
follow-link loop end to end on fixtures. No browser internals are
imported by src; tests may use collect_links like Runner.observe uses
page text (caller-provided seams)."""

import time
from pathlib import Path

import pytest

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import (
    MAX_ITERS,
    LoopRefused,
    LoopRunner,
    RepeatSpec,
    _load,
    run_linked_task,
)
from lakra.control.planner import Planner
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Budget, Status, Task
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


def stub_rig(tmp_path, monitor=None, failing_after=None):
    db = Database(tmp_path / "loops.db")
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


def start(ns, goal="Follow the batch records", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


# -- spec validation (no browser) ---------------------------------------------

def test_spec_validation():
    good = {"task_id": "t", "list_url": "u", "match_text": "m",
            "max_items": 2, "max_iters": 3, "body_expect": "e"}
    assert RepeatSpec.create(**good).max_iters == 3
    for bad in ({"max_iters": MAX_ITERS + 1}, {"max_iters": 0},
                {"max_items": 4, "max_iters": 3}, {"max_items": 0},
                {"max_items": "2"}, {"list_url": ""}, {"match_text": " "},
                {"body_expect": ""}):
        kw = dict(good)
        kw.update(bad)
        with pytest.raises(LoopRefused):
            RepeatSpec.create(**kw)


# -- stub behavior --------------------------------------------------------------

def _never_plan(task, spec, item):
    raise AssertionError("no bodies on empty inventory")


def test_first_empty_done_zero(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: [],
                    plan_for=_never_plan)
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="record", max_items=3, max_iters=3,
                   body_expect="detail record")
    assert (res.status, res.iters_done, res.findings) == ("DONE", 0, [])
    assert "first_empty" in res.detail
    types = [e["type"] for e in ns["audit"].replay()]
    assert "LOOP_START" in types and "LOOP_END" in types
    assert "LOOP_ITERATION" not in types


def test_budget_precheck_refuses(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns, budget=Budget(max_steps=3, max_tokens_cents=0,
                                max_minutes=30))
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax"])
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="x", max_items=1, max_iters=2,
                   body_expect="detail record")
    assert res.status == "STOPPED" and "budget" in res.detail
    assert res.iters_done == 0


def test_item_cap_and_persistence(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns)
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax", "bx", "cx"])
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="x", max_items=2, max_iters=5,
                   body_expect="detail record")
    assert (res.status, res.findings) == ("DONE", ["ax", "bx"])
    assert "item cap" in res.detail
    assert ns["registry"].get(t.task_id).status == Status.COMPLETED
    spec, state = _load(ns["db"], res.loop_id)
    assert state["status"] == "DONE" and state["findings"] == ["ax", "bx"]
    assert state["cursor"] == 2
    again = lr.resume(res.loop_id, ScriptedDecider(True))
    assert again.status == "DONE" and "terminal" in again.detail


def test_max_iters_defensive_bound_documented(tmp_path):
    # Invariant: every completed cycle appends one finding, so with
    # max_items <= max_iters the item cap always binds first; max_iters
    # remains a defensive bound. A rich inventory still stops at the cap.
    ns = stub_rig(tmp_path)
    t = start(ns)
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax", "bx", "cx", "dx", "ex"])
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="x", max_items=3, max_iters=3,
                   body_expect="detail record")
    assert (res.status, res.findings) == ("DONE", ["ax", "bx", "cx"])


def test_body_stop_ends_loop(tmp_path):
    ns = stub_rig(tmp_path)
    calls = {"n": 0}

    class Flaky(FakeController):
        def run_step(self, task_id, step):
            calls["n"] += 1
            # Establish + first body + second establish succeed (8
            # steps); the second body then fails every step.
            if calls["n"] > 8:
                return StepResult(False, False, 1, "ESCALATE",
                                  "bounded recovery exhausted")
            return super().run_step(task_id, step)

    ns["runner"].controller = Flaky(ns["runner"].controller.router)
    t = start(ns)
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax", "bx"])
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="x", max_items=2, max_iters=2,
                   body_expect="detail record")
    assert res.status == "STOPPED" and res.findings == ["ax"]
    assert "iteration stopped" in res.detail


def test_pause_then_resume(tmp_path):
    class Starved(HealthyMonitor):
        def under_pressure(self, **kw):
            return True, "low RAM: 64 MB available"

    mon = Starved()
    ns = stub_rig(tmp_path, monitor=mon)
    t = start(ns)
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax", "bx", "cx"],
                    observe=lambda: "fresh")
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="x", max_items=3, max_iters=3,
                   body_expect="detail record")
    assert res.status == "PAUSED" and res.iters_done == 0
    assert ns["registry"].get(t.task_id).status == Status.PAUSED
    _, state = _load(ns["db"], res.loop_id)
    # Pause struck during establish: no pending body plan; resume
    # re-establishes and continues the cursor.
    assert state["status"] == "PAUSED" and state["pending_plan"] is None
    assert state["findings"] == []
    mon.under_pressure = lambda **kw: (False, "ok")
    ns["sched"].resume(t.task_id)
    out = lr.resume(res.loop_id, ScriptedDecider(True))
    assert (out.status, out.findings) == ("DONE", ["ax", "bx", "cx"])


def test_pause_mid_body_then_resume(tmp_path):
    mon = HealthyMonitor()
    ns = stub_rig(tmp_path, monitor=mon)
    calls = {"n": 0}

    def inventory():
        calls["n"] += 1
        if calls["n"] >= 2:
            # Starve from the second cycle on: establish passes, the
            # body guard trips at step 0 with a pending plan to resume.
            mon.under_pressure = lambda **kw: (True, "low RAM")
        return ["ax", "bx"]

    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=inventory, observe=lambda: "fresh")
    t = start(ns)
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="x", max_items=2, max_iters=2,
                   body_expect="detail record")
    assert res.status == "PAUSED" and res.findings == ["ax"]
    _, state = _load(ns["db"], res.loop_id)
    assert state["pending_plan"] is not None
    mon.under_pressure = lambda **kw: (False, "ok")
    ns["sched"].resume(t.task_id)
    out = lr.resume(res.loop_id, ScriptedDecider(True))
    assert (out.status, out.findings) == ("DONE", ["ax", "bx"])
    _, state = _load(ns["db"], res.loop_id)
    assert state["status"] == "DONE" and state["pending_plan"] is None


def test_per_iteration_l3_approval(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns, goal="Submit the batch forms")

    def batch_plan(task, spec, item):
        hints = {"url": "file:///t.html", "selector": "#f", "text": item,
                 "submit_selector": "#s"}
        return Planner().plan(task, hints), hints

    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax", "bx"],
                    plan_for=batch_plan)
    decider = ScriptedDecider(True)
    res = lr.start(t, decider, list_url="file:///l.html", match_text="x",
                   max_items=2, max_iters=2, body_expect="ignored")
    assert (res.status, res.findings) == ("DONE", ["ax", "bx"])
    assert len(decider.seen) == 2  # one human approval PER iteration
    consumed = [e for e in ns["audit"].replay()
                if e["type"] == "APPROVAL_CONSUMED"]
    assert len(consumed) == 2  # one-shot token burned per iteration


# -- live follow loop -------------------------------------------------------------

def test_live_follow_loop_three_details(tmp_path):
    db = Database(tmp_path / "live.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(tmp_path / "prof")
    sessions.launch()
    try:
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
            "Follow the batch records", allowed_tools=["browser"],
            allowed_domains=["file:"], allowed_paths=[]))
        registry.checkout(t.task_id, "e")
        registry.set_status(t.task_id, Status.RUNNING)
        sched.enqueue(t.task_id)
        lr = LoopRunner(
            taskloop, store=db,
            inventory_fn=lambda: [text for text, _ in
                                  collect_links(hands.page)])
        # No pre-open: the loop establishes the list page itself first.
        res = lr.start(t, ScriptedDecider(True), list_url=LIST_URL,
                       match_text="record", max_items=3, max_iters=3,
                       body_expect="detail record")
        assert (res.status, res.findings) == (
            "DONE", ["Alpha record", "Beta record", "Gamma record"])
        assert registry.get(t.task_id).status == Status.COMPLETED
        types = [e["type"] for e in audit.replay()]
        assert types.count("LOOP_ITERATION") == 3
        assert "LOOP_START" in types and "LOOP_END" in types
    finally:
        sessions.close()
        db.close()


# -- composed grounded-link road (slice-32) ------------------------------------

def test_compose_refusals_before_loop(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns, goal="Follow every batch record")
    decider = ScriptedDecider(True)
    full = {"list_url": "file:///l.html",
            "goal_text": "Follow every batch record",
            "body_expect": "detail record", "max_items": 2, "max_iters": 2}

    def run(open_page, read_links, goal):
        return run_linked_task(ns["loop"], ns["db"], open_page,
                               read_links, t, goal, decider)

    opened = []
    r = run(opened.append, lambda: ["ax record", "bx record"],
            {"list_url": "file:///l.html"})
    assert r.status == "STOPPED" and "malformed goal" in r.detail

    def boom(url):
        raise OSError("network down")

    r = run(boom, lambda: ["ax record", "bx record"], dict(full))
    assert r.status == "STOPPED" and "cannot open list" in r.detail

    r = run(opened.append, lambda: ["Only record"], dict(full))
    assert r.status == "STOPPED" and "2+ links" in r.detail

    bad_shape = dict(full, max_items=2, max_iters=1)
    r = run(opened.append, lambda: ["ax record", "bx record"], bad_shape)
    assert r.status == "STOPPED" and "invalid loop shape" in r.detail

    # Nothing runnable ever existed: no loop rows, but every refusal
    # is audit-visible as START + END.
    assert ns["db"].execute("SELECT COUNT(*) FROM loops").fetchone()[0] \
        == 0
    types = [e["type"] for e in ns["audit"].replay()]
    assert types.count("LOOP_START") == 4
    assert types.count("LOOP_END") == 4
    assert "LOOP_ITERATION" not in types


def _live_ns(tmp_path):
    db = Database(tmp_path / "compose.db")
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
        "Follow every batch record", allowed_tools=["browser"],
        allowed_domains=["file:"], allowed_paths=[]))
    registry.checkout(t.task_id, "e")
    registry.set_status(t.task_id, Status.RUNNING)
    sched.enqueue(t.task_id)
    return {"db": db, "registry": registry, "audit": audit,
            "taskloop": taskloop, "hands": hands, "sessions": sessions,
            "task": t}


def test_live_composed_grounded_loop(tmp_path):
    ns = _live_ns(tmp_path)
    try:
        read_texts = lambda: [text for text, _ in  # noqa: E731
                              collect_links(ns["hands"].page)]
        res = run_linked_task(
            ns["taskloop"], ns["db"], ns["hands"].open, read_texts,
            ns["task"], {"list_url": LIST_URL,
                         "goal_text": "Follow every batch record",
                         "body_expect": "detail record",
                         "max_items": 3, "max_iters": 3},
            ScriptedDecider(True))
        # Only goal words + URL + arrival proof were hand-fed; the
        # match ("record") and every link text came from the page.
        assert (res.status, res.findings) == (
            "DONE", ["Alpha record", "Beta record", "Gamma record"])
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.COMPLETED
        types = [e["type"] for e in ns["audit"].replay()]
        assert types.count("LOOP_ITERATION") == 3
    finally:
        ns["sessions"].close()
        ns["db"].close()
