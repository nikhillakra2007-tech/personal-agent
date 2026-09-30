"""Slice-21: V1 acceptance harness (no new capability, harness only).

A/B/D run the fixture-only live browser through the real TaskLoop/Runner/
approval-token/guard paths. Since slice-22 the approved browser.submit step
runs through the LIVE submit executor (hands) — proven by real page-state
change, no stub twin remains. C runs stub executors (guard+runner paths are
identical; live pressure cycling is proven in scripts/replay_slice20.py and
replay_slice21.py). D re-derives the verdict table from a FRESH audit read
and demands cell-for-cell equality with the live-observed table.
"""

import time
from pathlib import Path

from lakra.control.analyzer import grounded_follow, ground_fields
from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.budgets import TokenLedger
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.planner import Planner
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
from lakra.execution.registry import ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()
LOOP_INDEX = (Path(__file__).parent / "fixtures" / "loop-index.html").as_uri()
A_HINTS = {"url": FIXTURE, "expect_text": "pending"}
B_HINTS = {"url": FIXTURE, "selector": "#echo", "text": "hi",
           "submit_selector": "#toggle"}


class ScriptedMonitor:
    def __init__(self, mode="healthy"):
        self.mode = mode

    def sample(self, force=False):
        starved = self.mode == "starved"
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384,
            ram_available_mb=64 if starved else 8192,
            cpu_percent=99.0 if starved else 5.0,
            lakra_rss_mb=50.0, browser_rss_mb=0.0, gpu_available=False)

    def under_pressure(self, **kw):
        if self.mode == "starved":
            return True, "low RAM: 64 MB available"
        return False, "ok"


def live_rig(tmp_path, monitor=None, ledger_for=None):
    """Full live stack. Harness calls TaskLoop/Runner only; it never
    transitions tasks or mints tokens itself."""
    db = Database(tmp_path / "acc.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(tmp_path / "prof")
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, tmp_path / "shots")
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait", "browser.submit",
                 "browser.check", "browser.select"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    guards = Guards(scheduler=sched, ledger_for=ledger_for,
                    monitor=monitor or ScriptedMonitor("healthy"))
    runner = Runner(ctrl, sched, audit,
                    observe=lambda: hands.page.locator("body").inner_text(),
                    store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit, "sched": sched,
            "approvals": approvals, "ctrl": ctrl, "guards": guards,
            "runner": runner, "loop": loop, "sessions": sessions,
            "hands": hands}


def start(ns, goal):
    task = ns["registry"].add(Task.create(
        goal, allowed_tools=["browser"], allowed_domains=["file:"],
        allowed_paths=[]))
    ns["registry"].checkout(task.task_id, "e")
    ns["registry"].set_status(task.task_id, Status.RUNNING)
    ns["sched"].enqueue(task.task_id)
    return task


def derive_rows(events):
    """Verdict table from audit events alone (mirrors replay_slice21.py)."""
    rows = {}

    def row(tid):
        return rows.setdefault(
            tid, {"plan_status": "-", "steps_done": 0, "guard_trips": 0,
                  "approval": "none", "task_status": "?"})

    for e in events:
        tid, ty, p = e["task_id"], e["type"], e.get("payload", {})
        if tid == "-":
            continue
        r = row(tid)
        if ty == "PLAN_OUTCOME":
            r["plan_status"] = p["status"]
            r["steps_done"] = p["steps_done"]
            if p["status"] == "PAUSED":
                r["task_status"] = "PAUSED"
        elif ty == "GUARD_TRIP":
            r["guard_trips"] += 1
        elif ty == "ACTION_REQUIRES_APPROVAL":
            r["approval"] = "pending"
        elif ty == "APPROVAL_CONSUMED":
            r["approval"] = "approved"
        elif ty == "TASK_LIFECYCLE":
            r["task_status"] = p["task_status"]
        elif ty == "TASK_STALLED":
            r["task_status"] = "RUNNING"
        elif ty == "TASK_CANCELLED":
            r["task_status"] = "CANCELLED"
    for r in rows.values():
        if r["approval"] == "pending" and r["plan_status"] == "STOPPED":
            r["approval"] = "denied"
            r["task_status"] = "CANCELLED"
    return rows


def live_row(ns, task_id, res):
    events = ns["audit"].replay()
    return {
        "plan_status": res.status, "steps_done": res.steps_done,
        "guard_trips": sum(1 for e in events
                           if e["type"] == "GUARD_TRIP"
                           and e["task_id"] == task_id),
        "approval": ("approved"
                     if any(e["type"] == "APPROVAL_CONSUMED"
                             and e["task_id"] == task_id for e in events)
                     else ("denied" if "denied" in (res.detail or "")
                           else "none")),
        "task_status": ns["registry"].get(task_id).status.value}


# -- A. docs-search (live) ----------------------------------------------------

def test_A_docs_search_done_without_direction(tmp_path):
    ns = live_rig(tmp_path)
    try:
        seen = []
        decider = ScriptedDecider(True)
        decider.decide = lambda item: (seen.append(item), True)[1]
        t = start(ns, "List my pending courses")
        res = ns["loop"].run_goal(t, dict(A_HINTS), decider)
        assert (res.status, res.steps_done) == ("DONE", 2)
        assert seen == []  # no per-click direction
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine if e["type"] == "GUARD_TRIP"]
        assert all(e["payload"].get("result") == "PASS"
                   for e in mine if e["type"] == "VERIFICATION")
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B. portal-submit approve/deny (live approval-token path) -----------------

def test_B_approve_submit_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the demo form")
        res = ns["loop"].run_goal(t, dict(B_HINTS), ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 3)
        types = [e["type"] for e in ns["audit"].replay()]
        assert "APPROVAL_CONSUMED" in types
        assert "APPROVED_STEP_EXECUTED" in types
        # Real execution proof: the LIVE control changed page state
        # (a stub twin could never do this).
        assert ns["hands"].page.locator("#status").inner_text() == "clicked"
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_B_deny_submit_stops_cancelled(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the demo form")
        res = ns["loop"].run_goal(t, dict(B_HINTS), ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(t.task_id).status == Status.CANCELLED
        # Denied submit never touched the page.
        assert ns["hands"].page.locator("#status").inner_text() == \
            "not clicked"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B3. multi-field form (slice-23, live) ------------------------------------

def _multi_hints():
    return {"url": FIXTURE, "submit_selector": "#m-submit", "fields": [
        {"selector": "#m-name", "text": "Ada"},
        {"selector": "#m-city", "text": "Lagos"},
        {"selector": "#m-zip", "text": "10001"},
    ]}


def test_B3_approve_multi_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the multi-field address form")
        res = ns["loop"].run_goal(t, _multi_hints(), ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 5)
        types = [e["type"] for e in ns["audit"].replay()]
        assert "APPROVAL_CONSUMED" in types
        page = ns["hands"].page
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#m-city").input_value() == "Lagos"
        assert page.locator("#m-zip").input_value() == "10001"
        assert page.locator("#m-status").inner_text() == "submitted"
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_B3_deny_multi_stops_cancelled(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the multi-field address form")
        res = ns["loop"].run_goal(t, _multi_hints(), ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(t.task_id).status == Status.CANCELLED
        page = ns["hands"].page
        # Safe prefix ran (fields filled); the gate held the submit.
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#m-status").inner_text() == "not submitted"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B4. mixed text+check form (slice-25, live) -------------------------------

def _mixed_hints():
    return {"url": FIXTURE, "submit_selector": "#m-submit", "fields": [
        {"selector": "#m-name", "text": "Ada"},
        {"selector": "#p-news", "checked": True},
        {"selector": "#p-plan-pro", "checked": True},
    ]}


def test_B4_approve_mixed_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the mixed preferences form")
        res = ns["loop"].run_goal(t, _mixed_hints(), ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 5)
        types = [e["type"] for e in ns["audit"].replay()]
        assert "APPROVAL_CONSUMED" in types
        page = ns["hands"].page
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#p-news").is_checked()
        assert page.locator("#p-plan-pro").is_checked()
        assert page.locator("#m-status").inner_text() == "submitted"
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_B4_deny_mixed_stops_cancelled(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the mixed preferences form")
        res = ns["loop"].run_goal(t, _mixed_hints(), ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(t.task_id).status == Status.CANCELLED
        page = ns["hands"].page
        # Safe prefix ran (text filled, box checked); the gate held submit.
        assert page.locator("#p-news").is_checked()
        assert page.locator("#m-status").inner_text() == "not submitted"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B5. full mixed form incl. select (slice-26, live) ------------------------

def _full_hints():
    return {"url": FIXTURE, "submit_selector": "#m-submit", "fields": [
        {"selector": "#m-name", "text": "Ada"},
        {"selector": "#p-news", "checked": True},
        {"selector": "#p-country", "select": "ng"},
    ]}


def test_B5_approve_full_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the full application form")
        res = ns["loop"].run_goal(t, _full_hints(), ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 5)
        types = [e["type"] for e in ns["audit"].replay()]
        assert "APPROVAL_CONSUMED" in types
        page = ns["hands"].page
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#p-news").is_checked()
        assert page.locator("#p-country").input_value() == "ng"
        assert page.locator("#m-status").inner_text() == "submitted"
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_B5_deny_full_stops_cancelled(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Submit the full application form")
        res = ns["loop"].run_goal(t, _full_hints(), ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(t.task_id).status == Status.CANCELLED
        page = ns["hands"].page
        # Safe prefix ran (selection made); the gate held submit.
        assert page.locator("#p-country").input_value() == "ng"
        assert page.locator("#m-status").inner_text() == "not submitted"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B6. follow-link detail flow (slice-27, live) -----------------------------

def _follow_hints(link_text="details"):
    return {"url": FIXTURE, "link_text": link_text,
            "expect_text": "eigenvalues"}


def test_B6_follow_detail_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Show details of Linear Algebra")
        res = ns["loop"].run_goal(t, _follow_hints(), ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 4)
        page = ns["hands"].page
        assert page.url.endswith("course-detail.html")
        assert "eigenvalues" in (
            page.locator("body").inner_text() or "")
        # The decider was never needed: no approval gate on this road.
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine
                    if e["type"] == "ACTION_REQUIRES_APPROVAL"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_B6_bad_link_stops(tmp_path):
    ns = live_rig(tmp_path)
    try:
        t = start(ns, "Show details of Linear Algebra")
        res = ns["loop"].run_goal(t, _follow_hints("no-such-link"),
                                  ScriptedDecider(True))
        assert res.status == "STOPPED"
        assert ns["hands"].page.url == FIXTURE  # never left the list
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B7. grounded follow (slice-30, live): the link_text is DERIVED
# from the observed inventory, never hand-fed -----------------------------

def test_B7_grounded_follow_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        ns["hands"].open(LOOP_INDEX)
        inventory = collect_links(ns["hands"].page)
        assert len(inventory) == 3
        hints = grounded_follow("Follow the Beta record", LOOP_INDEX,
                                inventory, "detail record: Beta")
        assert hints["link_text"] == "Beta record"  # derived, not fed
        t = start(ns, "Follow the Beta record")
        res = ns["loop"].run_goal(t, hints, ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 4)
        page = ns["hands"].page
        assert page.url.endswith("loop-beta.html")
        assert "detail record: Beta" in (
            page.locator("body").inner_text() or "")
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- B8. grounded form (slice-31, live): every selector DERIVED from
# the observed control inventory; only slot names+values hand-fed -----

def _grounded_form_hints(ns):
    ns["hands"].open(FIXTURE)
    controls = collect_controls(ns["hands"].page)
    fields = ground_fields({"name": {"text": "Ada"},
                            "city": {"text": "Lagos"},
                            "zip": {"text": "10001"},
                            "news": {"checked": True},
                            "country": {"select": "ng"}}, controls)
    assert [f["selector"] for f in fields] == \
        ["#m-name", "#m-city", "#m-zip", "#p-news", "#p-country"]
    return {"url": FIXTURE, "submit_selector": "#m-submit",
            "fields": fields}


def test_B8_approve_grounded_done(tmp_path):
    ns = live_rig(tmp_path)
    try:
        hints = _grounded_form_hints(ns)
        t = start(ns, "Submit the grounded application form")
        res = ns["loop"].run_goal(t, hints, ScriptedDecider(True))
        assert (res.status, res.steps_done) == ("DONE", 7)
        page = ns["hands"].page
        assert page.locator("#m-name").input_value() == "Ada"
        assert page.locator("#m-city").input_value() == "Lagos"
        assert page.locator("#m-zip").input_value() == "10001"
        assert page.locator("#p-news").is_checked()
        assert page.locator("#p-country").input_value() == "ng"
        assert page.locator("#m-status").inner_text() == "submitted"
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_B8_deny_grounded_stops_cancelled(tmp_path):
    ns = live_rig(tmp_path)
    try:
        hints = _grounded_form_hints(ns)
        t = start(ns, "Submit the grounded application form")
        res = ns["loop"].run_goal(t, hints, ScriptedDecider(False))
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(t.task_id).status == Status.CANCELLED
        page = ns["hands"].page
        # Safe prefix ran (all grounded bindings executed); the gate
        # held the submit.
        assert page.locator("#p-country").input_value() == "ng"
        assert page.locator("#m-status").inner_text() == "not submitted"
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == t.task_id]
        assert not [e for e in mine if e["type"] == "APPROVAL_CONSUMED"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- C. guard paths (stub executors; live proven in the replays) --------------

class FakeController:
    def run_step(self, task_id, step):
        return StepResult(True, True, 1, "CONTINUE", "verified")


def stub_rig(tmp_path, monitor=None, ledger_for=None):
    db = Database(tmp_path / "c.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    guards = Guards(scheduler=sched, ledger_for=ledger_for,
                    monitor=monitor or ScriptedMonitor("healthy"))
    runner = Runner(FakeController(), sched, audit, store=db, guards=guards)
    return registry, sched, audit, runner, db


def test_C_pressure_pause_relief_resume_done(tmp_path):
    mon = ScriptedMonitor("starved")
    r, s, a, runner, db = stub_rig(tmp_path, monitor=mon)
    t = r.add(Task.create("List the test things",
                          allowed_tools=["browser"]))
    r.checkout(t.task_id, "e")
    r.set_status(t.task_id, Status.RUNNING)
    s.enqueue(t.task_id)
    plan = Planner().plan(t, {"url": "file:///t.html", "expect_text": "x"})
    res = runner.run(plan)
    assert res.status == "PAUSED" and res.steps_done == 0
    assert r.get(t.task_id).status == Status.PAUSED
    assert any(e["type"] == "GUARD_TRIP"
               and e["payload"]["action"] == "pause" for e in a.replay())
    mon.mode = "healthy"
    s.resume(t.task_id)
    out = runner.resume(plan.plan_id, observe=lambda: "fresh")
    assert (out.status, out.steps_done) == ("DONE", 2)
    db.close()


def test_C_token_exhaustion_stops(tmp_path):
    spent = TokenLedger(4)
    spent.charge(4)
    r, s, a, runner, db = stub_rig(
        tmp_path, ledger_for=lambda tid: spent)
    t = r.add(Task.create("List the test things",
                          allowed_tools=["browser"]))
    r.checkout(t.task_id, "e")
    r.set_status(t.task_id, Status.RUNNING)
    s.enqueue(t.task_id)
    res = runner.run(Planner().plan(
        t, {"url": "file:///t.html", "expect_text": "x"}))
    assert res.status == "STOPPED" and "tokens" in res.detail
    assert any(e["type"] == "GUARD_TRIP"
               and e["payload"]["action"] == "stop" for e in a.replay())
    db.close()


# -- D. replay equality (live A + B-approve + C-cycle, one audit log) ---------

def test_D_replay_table_equals_live(tmp_path):
    ns = live_rig(tmp_path)
    try:
        live = {}
        tA = start(ns, "List my pending courses")
        resA = ns["loop"].run_goal(tA, dict(A_HINTS), ScriptedDecider(True))
        live[tA.task_id] = live_row(ns, tA.task_id, resA)

        tB = start(ns, "Submit the demo form")
        resB = ns["loop"].run_goal(tB, dict(B_HINTS), ScriptedDecider(True))
        live[tB.task_id] = live_row(ns, tB.task_id, resB)
        assert (resB.status, resB.steps_done) == ("DONE", 3)

        ns["guards"].monitor = ScriptedMonitor("starved")
        tC = start(ns, "List my pending courses")
        planC = Planner().plan(tC, dict(A_HINTS))
        resC = ns["runner"].run(planC, hints=dict(A_HINTS))
        assert resC.status == "PAUSED"
        ns["guards"].monitor = ScriptedMonitor("healthy")
        ns["sched"].resume(tC.task_id)
        outC = ns["runner"].resume(planC.plan_id,
                                   observe=lambda: "fresh snapshot")
        live[tC.task_id] = live_row(ns, tC.task_id, outC)
        assert (outC.status, outC.steps_done) == ("DONE", 2)

        fresh = AuditLog(ns["audit"].path).replay()
        replayed = derive_rows(fresh)
        for tid, row in live.items():
            assert replayed[tid] == row, (tid, row, replayed.get(tid))
    finally:
        ns["sessions"].close()
        ns["db"].close()
