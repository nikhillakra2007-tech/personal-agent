"""Slice-35: composed road dispatcher.

run_task() routes one union goal to exactly one composed road by key
presence only (goal_slots -> form; max_items/max_iters -> loop;
list_url/goal_text/body_expect -> follow), then delegates unchanged to
run_form_task / run_linked_task / run_follow_task. No content
inference, no model, no fall-through, no new executors, predicates,
templates, policy, hint keys, or audit event types.

Stub rigs prove routing (each road observes exactly once, results pass
through), malformed/mixed-key refusals, and no-fall-through. Live
tests prove one dispatcher drives all three roads on fixtures. No
browser internals are imported by src; tests may use collect_links /
collect_controls like Runner.observe uses page text
(caller-provided seams)."""

import time
from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import run_task
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
    collect_links,
)
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

FIXTURES = Path(__file__).parent / "fixtures"
LOOP_INDEX = (FIXTURES / "loop-index.html").as_uri()
FORM_PAGE = (FIXTURES / "courses.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit", "browser.check",
         "browser.select")

LINK_PAIRS = [("Alpha record", "alpha.html"),
              ("Beta record", "beta.html"),
              ("Gamma record", "gamma.html")]

CONTROLS = [("Full name", "text", "#m-name"),
            ("City", "text", "#m-city"),
            ("ZIP", "text", "#m-zip"),
            ("News", "check", "#p-news"),
            ("Country", "select", "#p-country")]

SLOTS = {"name": {"text": "Ada"},
         "city": {"text": "Lagos"},
         "zip": {"text": "10001"},
         "news": {"checked": True},
         "country": {"select": "ng"}}


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
    db = Database(tmp_path / "dispatch.db")
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


def start(ns, goal, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, "e")
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def dispatch(ns, task, goal, decider, calls):
    opened = calls.setdefault("opened", [])

    def open_page(url):
        opened.append(url)

    def read_links():
        calls["links"] = calls.get("links", 0) + 1
        return list(calls["link_data"])

    def read_controls():
        calls["controls"] = calls.get("controls", 0) + 1
        return list(calls["control_data"])

    return run_task(ns["loop"], ns["db"], open_page, read_links,
                    read_controls, task, goal, decider)


# -- stub routing ---------------------------------------------------------------

def test_dispatch_routes_three_roads(tmp_path):
    ns = stub_rig(tmp_path)

    # Follow road: links observed once, controls never touched.
    calls = {"link_data": list(LINK_PAIRS), "control_data": list(CONTROLS)}
    t = start(ns, "Follow the Beta record")

    def no_controls():
        raise AssertionError("follow must not read controls")

    res = run_task(ns["loop"], ns["db"], calls.setdefault("opened", []).append,
                   lambda: (calls.update(links=calls.get("links", 0) + 1),
                            list(LINK_PAIRS))[1],
                   no_controls, t,
                   {"list_url": "file:///l.html",
                    "goal_text": "Follow the Beta record",
                    "body_expect": "detail record: Beta"},
                   ScriptedDecider(True))
    assert (res.status, res.steps_done) == ("DONE", 4)
    assert calls["links"] == 1

    # Form road: controls observed once, links never touched.
    calls = {"link_data": list(LINK_PAIRS), "control_data": list(CONTROLS)}
    t = start(ns, "Submit the grounded application form")
    decider = ScriptedDecider(True)

    def no_links():
        raise AssertionError("form must not read links")

    res = run_task(ns["loop"], ns["db"], calls.setdefault("opened", []).append,
                   no_links,
                   lambda: (calls.update(
                       controls=calls.get("controls", 0) + 1),
                       list(CONTROLS))[1], t,
                   {"form_url": "file:///f.html",
                    "goal_slots": dict(SLOTS),
                    "submit_selector": "#m-submit"},
                   decider)
    assert (res.status, res.steps_done) == ("DONE", 7)
    assert calls["controls"] == 1
    assert len(decider.seen) == 1  # the submit gate asked once

    # Loop road: LoopResult passes through with findings.
    calls = {"link_data": ["ax record", "bx record"],
             "control_data": list(CONTROLS)}
    t = start(ns, "Follow every batch record")
    res = dispatch(ns, t,
                   {"list_url": "file:///l.html",
                    "goal_text": "Follow every batch record",
                    "body_expect": "detail record",
                    "max_items": 2, "max_iters": 2},
                   ScriptedDecider(True), calls)
    assert (res.status, res.findings) == ("DONE", ["ax record", "bx record"])
    assert calls.get("controls", 0) == 0  # loop never reads controls


def test_dispatch_malformed_and_mixed_keys_refuse(tmp_path):
    ns = stub_rig(tmp_path)
    calls = {"link_data": list(LINK_PAIRS), "control_data": list(CONTROLS)}

    def run(goal):
        t = start(ns, "Do the thing")
        return t, dispatch(ns, t, goal, ScriptedDecider(True), calls)

    t, r = run("not-a-mapping")
    assert (r.plan_id, r.status) == ("-", "STOPPED")
    assert "malformed goal" in r.detail

    t, r = run({})
    assert r.status == "STOPPED" and "unroutable" in r.detail
    assert r.plan_id == "-"

    t, r = run({"form_url": "file:///f.html"})
    assert r.status == "STOPPED" and "unroutable" in r.detail

    # Follow shape minus body_expect: delegated; the follow road names
    # the missing key (same trail as calling it directly).
    t, r = run({"list_url": "file:///l.html",
                "goal_text": "Follow the Beta record"})
    assert r.status == "STOPPED" and "missing" in r.detail

    # Mixed road keys: dispatcher refuses, never picks a winner.
    t, r = run({"form_url": "file:///f.html",
                "goal_slots": dict(SLOTS),
                "submit_selector": "#m-submit",
                "max_items": 1, "max_iters": 1})
    assert r.status == "STOPPED" and "mixed road keys" in r.detail
    assert r.plan_id == "-"

    t, r = run({"list_url": "file:///l.html",
                "goal_text": "Follow every batch record",
                "body_expect": "detail record",
                "max_items": 1, "max_iters": 1,
                "form_url": "file:///f.html"})
    assert r.status == "STOPPED" and "mixed road keys" in r.detail

    # Dispatcher-level refusals: exactly one PLAN_OUTCOME each, no road
    # activity (no PLAN_CREATED bodies, no LOOP_* rows).
    types = [e["type"] for e in ns["audit"].replay()]
    assert "PLAN_CREATED" not in types
    assert "LOOP_START" not in types and "LOOP_END" not in types
    assert ns["db"].execute(
        "SELECT COUNT(*) FROM loops").fetchone()[0] == 0


def test_dispatch_no_fall_through(tmp_path):
    ns = stub_rig(tmp_path)
    t = start(ns, "Submit the grounded application form")
    calls = {"link_data": list(LINK_PAIRS), "control_data": list(CONTROLS)}
    addrs = [("Home address", "text", "#p-home"),
             ("Work address", "text", "#p-work")]
    calls["control_data"] = addrs
    r = dispatch(ns, t,
                 {"form_url": "file:///f.html",
                  "goal_slots": {"address": {"text": "x"}},
                  "submit_selector": "#m-submit"},
                 ScriptedDecider(True), calls)
    assert r.status == "STOPPED" and "no groundable fields" in r.detail
    # The refused form was NOT retried on another road.
    events = [e for e in ns["audit"].replay()
              if e["task_id"] == t.task_id]
    assert [e["type"] for e in events] == ["PLAN_OUTCOME"]
    assert calls.get("links", 0) == 0


# -- live dispatch (one dispatcher, all three roads) ------------------------------

def _live_ns(tmp_path, goal):
    db = Database(tmp_path / "dispatch-live.db")
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


def _seams(ns):
    return (ns["hands"].open,
            lambda: list(collect_links(ns["hands"].page)),  # noqa: E731
            lambda: [text for text, _ in  # noqa: E731
                     collect_links(ns["hands"].page)],
            lambda: list(collect_controls(ns["hands"].page)))  # noqa: E731


def test_live_dispatch_follow_beta(tmp_path):
    ns = _live_ns(tmp_path, "Follow the Beta record")
    try:
        open_page, read_links, _, read_controls = _seams(ns)
        decider = ScriptedDecider(True)
        res = run_task(ns["taskloop"], ns["db"], open_page, read_links,
                       read_controls, ns["task"],
                       {"list_url": LOOP_INDEX,
                        "goal_text": "Follow the Beta record",
                        "body_expect": "detail record: Beta"},
                       decider)
        assert (res.status, res.steps_done) == ("DONE", 4)
        assert ns["hands"].page.url.endswith("loop-beta.html")
        assert decider.seen == []
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_dispatch_loop_three(tmp_path):
    ns = _live_ns(tmp_path, "Follow every batch record")
    try:
        open_page, _, read_texts, read_controls = _seams(ns)
        res = run_task(ns["taskloop"], ns["db"], open_page, read_texts,
                       read_controls, ns["task"],
                       {"list_url": LOOP_INDEX,
                        "goal_text": "Follow every batch record",
                        "body_expect": "detail record",
                        "max_items": 3, "max_iters": 3},
                       ScriptedDecider(True))
        assert (res.status, res.findings) == (
            "DONE", ["Alpha record", "Beta record", "Gamma record"])
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.COMPLETED
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_dispatch_form_approve(tmp_path):
    ns = _live_ns(tmp_path, "Submit the grounded application form")
    try:
        open_page, read_links, _, read_controls = _seams(ns)
        decider = ScriptedDecider(True)
        res = run_task(ns["taskloop"], ns["db"], open_page, read_links,
                       read_controls, ns["task"],
                       {"form_url": FORM_PAGE,
                        "goal_slots": dict(SLOTS),
                        "submit_selector": "#m-submit"},
                       decider)
        assert (res.status, res.steps_done) == ("DONE", 7)
        assert len(decider.seen) == 1
        page = ns["hands"].page
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#m-status").inner_text() == "submitted"
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_dispatch_form_deny(tmp_path):
    ns = _live_ns(tmp_path, "Submit the grounded application form")
    try:
        open_page, read_links, _, read_controls = _seams(ns)
        res = run_task(ns["taskloop"], ns["db"], open_page, read_links,
                       read_controls, ns["task"],
                       {"form_url": FORM_PAGE,
                        "goal_slots": dict(SLOTS),
                        "submit_selector": "#m-submit"},
                       ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.CANCELLED
        assert ns["hands"].page.locator("#m-status").inner_text() == \
            "not submitted"
    finally:
        ns["sessions"].close()
        ns["db"].close()
