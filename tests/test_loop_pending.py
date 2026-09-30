"""Slice-42 (D-01): loop pending-body resume on a fresh stack.

Covers the validation finding: a loop paused/stranded with an
incomplete body plan must resume on a fresh process — establishing
from persisted addressing, executing the pending item exactly once,
preserving findings/cursor, and collecting fresh consent at any
re-parked L3 gate. Deterministic persisted-state setups throughout
(no timing kills): guard-starved pauses and hand-crafted rows stand
in for process death (brand-new objects, same database file).
"""

import time

import pytest

from lakra.control import plan_store
from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import LoopRunner, RepeatSpec, _save, _update
from lakra.control.planner import Planner
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.resume import ResumeRefused, resume_info, resume_task
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter
from lakra.resources.monitor import SystemSnapshot

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit")

SUBMIT_HINTS = {"url": "file:///t.html", "selector": "#f", "text": "hi",
                "submit_selector": "#s"}


class Stub:
    def __init__(self):
        self.calls = 0

    def execute(self, action, task):
        self.calls += 1
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


def rig(tmp_path, name="d01.db", monitor=None):
    db = Database(tmp_path / name)
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
    runner = Runner(ctrl, sched, audit, observe=lambda: "fresh",
                    store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit, "sched": sched,
            "approvals": approvals, "stub": stub, "runner": runner,
            "loop": loop}


def start(ns, goal="Follow every batch record", owner="cli", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = ns["registry"].add(Task.create(goal, **kw))
    ns["registry"].checkout(t.task_id, owner)
    ns["registry"].set_status(t.task_id, Status.RUNNING)
    ns["sched"].enqueue(t.task_id)
    return t


def fresh_stack(ns):
    """Simulate process death: brand-new objects, same database file."""
    return rig(ns["db"].path.parent, name=ns["db"].path.name)


def open_first_observe(opened, url):
    """CLI run_resume observe seam: establish from persisted
    addressing, then observe the live page."""
    state = {"page": False}

    def observe():
        if not state["page"]:
            opened.append(url)
            state["page"] = True
        return "fresh snapshot"

    return observe


def starve_after_first_inventory(mon, calls, inventory):
    """Deterministic mid-body pause: starve from the second cycle on,
    so the first body completes and the second parks mid-plan."""

    def fenced():
        calls["n"] += 1
        if calls["n"] >= 2:
            mon.under_pressure = lambda **kw: (True, "low RAM")
        return inventory()

    return fenced


# -- TEST 1: normal loop unaffected -------------------------------------------------

def test_normal_loop_still_done(tmp_path):
    ns = rig(tmp_path)
    t = start(ns)
    lr = LoopRunner(ns["loop"], store=ns["db"],
                    inventory_fn=lambda: ["ax record", "bx record"],
                    observe=lambda: "fresh")
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="record", max_items=2, max_iters=2,
                   body_expect="detail record")
    assert (res.status, res.findings) == ("DONE", ["ax record",
                                                   "bx record"])


# -- TEST 2/3/4: pending body resumes on a fresh stack -------------------------------

def test_pending_body_resume_completes_no_replay(tmp_path):
    mon = HealthyMonitor()
    calls = {"n": 0}
    ns_rig = rig(tmp_path, name="d01b.db", monitor=mon)
    t = start(ns_rig)
    lr = LoopRunner(ns_rig["loop"], store=ns_rig["db"],
                    inventory_fn=starve_after_first_inventory(
                        mon, calls, lambda: ["ax record", "bx record"]),
                    observe=lambda: "fresh")
    res = lr.start(t, ScriptedDecider(True), list_url="file:///l.html",
                   match_text="record", max_items=2, max_iters=2,
                   body_expect="detail record")
    assert res.status == "PAUSED" and res.findings == ["ax record"]
    pre_calls = ns_rig["stub"].calls
    assert pre_calls == 8  # establish + body1 + establish2, nothing of body2

    ns2 = fresh_stack(ns_rig)
    t2 = ns2["registry"].get(t.task_id)
    info = resume_info(ns2["db"], t.task_id, ns2["audit"].replay())
    assert info["road"] == "loop" and info["cursor"] == 1
    assert info["url"] == "file:///l.html"  # persisted addressing surfaced
    assert info["pending_plan"] is not None
    opened = []
    out = resume_task(ns2["loop"], ns2["db"], t2, info,
                      ScriptedDecider(True),
                      observe=open_first_observe(opened, info["url"]),
                      inventory_fn=lambda: ["ax record", "bx record"])
    # TEST 2: pending body executed, loop completed from the cursor.
    assert out.status == "DONE"
    # TEST 3: only the pending body re-ran (one full 4-step body).
    assert ns2["stub"].calls == 4
    # TEST 4: findings preserved in order, no duplicates.
    assert out.findings == ["ax record", "bx record"]
    assert opened == ["file:///l.html"]  # established, not assumed


# -- TEST 5: L3 inside the resumed loop needs fresh approval ----------------------------

def _parked_body_loop(ns):
    """Hand-crafted stranded state: a loop task whose body plan parked
    at its submit gate (mirrors a kill between park and decision).
    The body is planned on the loop's own task — as LoopRunner always
    does — so the parked approval belongs to the loop task and
    discovery sees it. Returns (task, spec)."""
    t = start(ns, "Submit the batch forms")
    body = Planner().plan(t, dict(SUBMIT_HINTS))
    plan_store.save_plan(ns["db"], body, dict(SUBMIT_HINTS))
    res = ns["runner"].run(body, hints=dict(SUBMIT_HINTS))
    assert res.status == "ASK_PENDING"
    spec = RepeatSpec.create(t.task_id, "file:///l.html", "record",
                             1, 1, "detail record")
    _save(ns["db"], spec, "RUNNING")
    _update(ns["db"], spec.loop_id, "RUNNING", 0, [], body.plan_id,
            "bx record")
    return t, spec


def test_gated_body_resume_collects_fresh_approval(tmp_path):
    ns = rig(tmp_path)
    t, spec = _parked_body_loop(ns)
    ns2 = fresh_stack(ns)
    # The old park is still undecided: resume must refuse, never
    # duplicate-park beside it.
    info = resume_info(ns2["db"], t.task_id, ns2["audit"].replay())
    t2 = ns2["registry"].get(t.task_id)
    out = resume_task(ns2["loop"], ns2["db"], t2, info,
                      ScriptedDecider(True),
                      observe=open_first_observe([], None),
                      inventory_fn=lambda: ["bx record"])
    assert out.status == "STOPPED" and "undecided approval" in out.detail
    # Approve.py pattern: settle the old park externally, then resume.
    # The resumed body re-parks FRESH (crash rule) and the decider
    # clears the fresh gate: exactly one consumption, of the fresh row.
    pend = ns2["approvals"].pending()
    assert len(pend) == 1
    ns2["approvals"].decide(pend[0]["approval_id"], True)
    out = resume_task(
        ns2["loop"], ns2["db"], ns2["registry"].get(t.task_id),
        resume_info(ns2["db"], t.task_id, ns2["audit"].replay()),
        ScriptedDecider(True), observe=open_first_observe([], None),
        inventory_fn=lambda: ["bx record"])
    assert out.status == "DONE"
    assert out.findings == ["bx record"]
    rows = ns2["db"].execute(
        "SELECT COUNT(*) FROM approvals").fetchone()[0]
    assert rows == 2  # settled original + one fresh park
    assert sum(1 for e in ns2["audit"].replay()
               if e["type"] == "APPROVAL_CONSUMED") == 1  # fresh only


def test_gated_body_resume_deny_holds(tmp_path):
    ns = rig(tmp_path)
    t, spec = _parked_body_loop(ns)
    ns2 = fresh_stack(ns)
    pend = ns2["approvals"].pending()
    ns2["approvals"].decide(pend[0]["approval_id"], True)
    out = resume_task(
        ns2["loop"], ns2["db"], ns2["registry"].get(t.task_id),
        resume_info(ns2["db"], t.task_id, ns2["audit"].replay()),
        ScriptedDecider(False), observe=open_first_observe([], None),
        inventory_fn=lambda: ["bx record"])
    assert out.status == "STOPPED"
    assert out.findings == []  # unexecuted item never counted
    assert ns2["registry"].get(t.task_id).status == Status.CANCELLED


# -- TEST 6: uncertain consequential stays fail-closed ------------------------------------

def test_uncertain_body_refused(tmp_path):
    ns = rig(tmp_path)
    t, spec = _parked_body_loop(ns)
    ns2 = fresh_stack(ns)
    pend = ns2["approvals"].pending()
    ns2["approvals"].decide(pend[0]["approval_id"], True)
    events = ns2["audit"].replay() + [
        {"task_id": t.task_id, "type": "EXECUTION_STARTED",
         "payload": {"kind": "browser.submit"}}]
    info = resume_info(ns2["db"], t.task_id, events)
    assert info["uncertain"] != []
    out = resume_task(ns2["loop"], ns2["db"],
                      ns2["registry"].get(t.task_id), info,
                      ScriptedDecider(True),
                      observe=open_first_observe([], None),
                      inventory_fn=lambda: ["bx record"])
    assert out.status == "STOPPED" and "uncertain" in out.detail


# -- driver hardening: observe is mandatory on the loop road -------------------------------

def test_loop_resume_without_observe_refused_shaped(tmp_path):
    ns = rig(tmp_path)
    t = start(ns)
    spec = RepeatSpec.create(t.task_id, "file:///l.html", "record",
                             1, 1, "detail record")
    _save(ns["db"], spec, "RUNNING")
    info = resume_info(ns["db"], t.task_id, [])
    out = resume_task(ns["loop"], ns["db"], t, info,
                      ScriptedDecider(True), observe=None,
                      inventory_fn=lambda: ["ax record"])
    assert out.status == "STOPPED" and out.plan_id == "-"
    assert "fresh observation" in out.detail


def test_discovery_reports_pending_url(tmp_path):
    ns = rig(tmp_path)
    t, spec = _parked_body_loop(ns)
    info = resume_info(ns["db"], t.task_id, [])
    assert info["road"] == "loop"
    assert info["url"] == "file:///t.html"  # pending body addressing
    assert info["pending_plan"] is not None
    assert info["pending_approvals"] != []
