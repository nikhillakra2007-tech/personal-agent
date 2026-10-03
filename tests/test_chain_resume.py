"""Slice-51: chain resume by leg index (same task id).

--resume TASK_ID --chain-file FILE --from-leg N re-executes legs
N..total on the EXISTING task through the unchanged run_chain():
fresh plans per leg, existing policy/approval/lifecycle machinery,
stale live plans abandoned via set_plan_status(..., SUPERSEDED) +
the existing PLAN_SUPERSEDED audit event. A bare
--chain-file --from-leg run still creates a NEW task, and bare
--resume still resumes a single stranded unit.

Stub rigs (no browser) prove every precondition refuses read-only
before anything launches; the fake-browser seam proves the success
path end to end through run_chain_resume() itself. Live CLI proof
is a manual bounded validation, not a unit test.

V1-D1: the chain marker ("Chain of N legs") is write-once —
task_store.update_task() never persists goal, so a mid-chain death
cannot leak leg routing text over chain identity; bare --resume
refuses chain tasks with chain-resume guidance (read-only).
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lakra_do
from lakra_do import chain_summary, render_status, run_chain_resume
from lakra.control import plan_store, task_store
from lakra.control.approvals import ApprovalError, Approvals
from lakra.control.audit import AuditLog
from lakra.control.chain import run_chain
from lakra.control.guards import Guards
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.resume import pending_approval_ids
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
PAIRS = [("Beta record", "beta.html")]
CONTROLS = [("Name", "text", "#f")]


class StubExec:
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


class StarvedMonitor(HealthyMonitor):
    def under_pressure(self, **kw):
        return True, "low RAM: 64 MB available"


def stub_stack(db, audit, monitor=None):
    registry = TaskRegistry(store=db)
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    stub = StubExec()
    for kind in KINDS:
        tools.register(kind, stub)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = FakeController(router)
    guards = Guards(scheduler=sched, monitor=monitor or HealthyMonitor())
    runner = Runner(ctrl, sched, audit, store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit, "sched": sched,
            "approvals": approvals, "stub": stub, "runner": runner,
            "loop": loop}


def observe_leg(url="file:///l.html", expect="Records"):
    return {"road": "observe", "url": url, "expect_text": expect}


def follow_leg():
    return {"road": "follow", "list_url": "file:///l.html",
            "goal_text": "Beta record", "body_expect": "detail record"}


def form_leg():
    return {"road": "form", "form_url": "file:///f.html",
            "goal_slots": {"name": {"text": "Ada"}},
            "submit_selector": "#s"}


def seams():
    def open_page(url):
        return None

    def read_links():
        return list(PAIRS)

    def read_controls():
        return list(CONTROLS)

    return open_page, read_links, read_controls


def write_chain(tmp_path, name, legs):
    path = tmp_path / name
    path.write_text(json.dumps({"legs": legs}), encoding="utf-8")
    return str(path)


def make_chain_task(db, goal="Chain of 2 legs", status=Status.RUNNING,
                    owner="cli"):
    reg = TaskRegistry(store=db)
    t = reg.add(Task.create(goal, allowed_tools=["browser"],
                            allowed_domains=["file:"],
                            allowed_paths=[]))
    reg.checkout(t.task_id, owner)
    if status != Status.QUEUED:
        reg.set_status(t.task_id, Status.RUNNING)
        if status != Status.RUNNING:
            reg.set_status(t.task_id, status)
    return t.task_id


def resume_opts(tid, chain_path, from_leg=None, extra=("--yes",)):
    argv = ["--resume", tid, "--chain-file", chain_path]
    if from_leg is not None:
        argv += ["--from-leg", str(from_leg)]
    argv += list(extra)
    return argv, lakra_do.parse_args(argv)


def run_resume(tmp_path, db_path, audit_path, tid, chain_path,
               from_leg=None, extra=("--yes",), monitor=None):
    argv, opts = resume_opts(tid, chain_path, from_leg, extra)
    return run_chain_resume(
        argv, opts, audit_path, tmp_path / "prof", tmp_path / "shots",
        monitor or HealthyMonitor(), db_path=db_path)


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


# -- fake browser seam (no real browser; run_chain_resume builds its own
# stack, so the session/action/observer classes plus the inventory
# functions are swapped for recording fakes) ------------------------------

@pytest.fixture
def fake_browser(monkeypatch):
    instances = []

    class FakeLocator:
        def __init__(self,
                     text="Records Beta record detail record: Beta Welcome"):
            self._text = text

        def inner_text(self):
            return self._text

        def count(self):
            return 1

        @property
        def first(self):
            return self

        def nth(self, i):
            return self

        def is_visible(self):
            return True

        def is_checked(self):
            return True

        def wait_for(self, **kw):
            return None

    class FakePage:
        url = "file:///l.html"

        def locator(self, sel):
            return FakeLocator()

        def get_by_text(self, sel, exact=False):
            return FakeLocator()

        def get_by_role(self, sel):
            return FakeLocator()

    class FakeSessions:
        def __init__(self, profile_dir, accept_downloads=False,
                     exclusive=False):
            self.profile_dir = profile_dir
            self.accept_downloads = accept_downloads
            self.exclusive = exclusive
            self.page = FakePage()
            self.launches = 0
            instances.append(self)

        def launch(self):
            self.launches += 1

        def close(self):
            pass

    class FakeHands:
        def __init__(self, sessions, transfer_root=None):
            self.sessions = sessions
            self.transfer_root = transfer_root
            self.page = sessions.page
            self.opened = []

        def open(self, url, timeout_ms=15000):
            self.opened.append(url)
            self.page.url = url  # navigating moves the live page
            return None

        def attach(self, page):
            self.page = page

        def execute(self, action, task):
            return ExecuteResult(ok=True, output="did")

    class FakeObs:
        def __init__(self, sessions, shot_dir):
            self.sessions = sessions
            self.page = sessions.page

        @property
        def current_page(self):
            return self.page

        def execute(self, action, task):
            # Emulate the one real side effect the legs depend on:
            # the observer's navigate tool moves the live page, so
            # the plan's own navigate step (observe/follow/form all
            # verify url_is) sees the arrival URL.
            if action.kind == "browser.navigate":
                self.page.url = action.target
            return ExecuteResult(ok=True, output="did")

    monkeypatch.setattr(lakra_do, "BrowserSessions", FakeSessions)
    monkeypatch.setattr(lakra_do, "BrowserActions", FakeHands)
    monkeypatch.setattr(lakra_do, "BrowserObserver", FakeObs)
    monkeypatch.setattr(lakra_do, "collect_links",
                        lambda page: list(PAIRS))
    monkeypatch.setattr(lakra_do, "collect_controls",
                        lambda page: list(CONTROLS))
    monkeypatch.setattr(lakra_do, "collect_clicks", lambda page: [])
    return instances


def launches(instances):
    return sum(s.launches for s in instances)


# -- preconditions: invalid from_leg (tests 1-2) ----------------------------

def test_resume_from_leg_zero_refused(tmp_path, fake_browser):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=0)
    assert rc == 1
    assert launches(fake_browser) == 0


def test_resume_from_leg_beyond_total_refused(tmp_path, fake_browser,
                                              capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=9)
    assert rc == 1
    out = capsys.readouterr().out
    assert "invalid from_leg" in out
    assert launches(fake_browser) == 0


# -- preconditions: unknown / non-chain / terminal (tests 3-7) --------------

def test_resume_unknown_task_refused(tmp_path, fake_browser, capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    Database(db_path).close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, "no-such-task", chain,
                     from_leg=1)
    assert rc == 1
    assert "unknown task" in capsys.readouterr().out
    assert launches(fake_browser) == 0


def test_resume_non_chain_task_refused(tmp_path, fake_browser, capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db, goal="Do the thing")
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    assert "not a chain task" in capsys.readouterr().out
    assert launches(fake_browser) == 0


@pytest.mark.parametrize("terminal", [Status.COMPLETED, Status.FAILED,
                                      Status.CANCELLED])
def test_resume_terminal_refused(tmp_path, fake_browser, capsys,
                                 terminal):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db, status=terminal)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    out = capsys.readouterr().out
    assert "history immutable" in out and terminal.value in out
    assert launches(fake_browser) == 0


# -- preconditions: malformed chain file (test 8) ----------------------------

def test_resume_malformed_chain_file_refused(tmp_path, fake_browser,
                                             capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    rc = run_resume(tmp_path, db_path, audit_path, tid, str(bad),
                     from_leg=1)
    assert rc == 1
    assert "cannot read chain file" in capsys.readouterr().out
    assert launches(fake_browser) == 0


def test_resume_invalid_chain_shape_refused(tmp_path, fake_browser,
                                            capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg()])  # 1 leg
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    assert "invalid chain" in capsys.readouterr().out
    assert launches(fake_browser) == 0


# -- preconditions: pending approval names the id (test 9) -------------------

def test_resume_pending_approval_refused_with_id(tmp_path, fake_browser,
                                                 capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        ns = stub_stack(db, AuditLog(audit_path))
        tid = make_chain_task(db)
        apv = ns["approvals"].request(
            tid, Action(kind="browser.submit", target="#s",
                        effect="consequential", task_id=tid))
        assert pending_approval_ids(db, tid) == [apv.approval_id]
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    assert apv.approval_id in capsys.readouterr().out
    assert launches(fake_browser) == 0


# -- preconditions: uncertain execution (test 10) ------------------------------

def test_resume_uncertain_execution_refused(tmp_path, fake_browser,
                                            capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    AuditLog(audit_path).log("EXECUTION_STARTED", tid,
                             {"kind": "browser.submit"})
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    assert "uncertain" in capsys.readouterr().out
    assert launches(fake_browser) == 0


# -- preconditions: ownership conflict (test 11) ---------------------------------

def test_resume_owned_elsewhere_refused(tmp_path, fake_browser, capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db, owner="other-live-process")
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    assert "ownership" in capsys.readouterr().out
    assert launches(fake_browser) == 0


# -- success: paused chain resumes on the same task (tests 12-16, 20-21, 23) ---

def paused_chain(tmp_path):
    """Genuine stopped chain: starved first run pauses leg 1, leaving a
    live PAUSED plan on a PAUSED chain task."""
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    ns = stub_stack(db, AuditLog(audit_path), monitor=StarvedMonitor())
    tid = make_chain_task(db)
    task = ns["registry"].get(tid)
    ns["sched"].enqueue(tid)
    open_page, read_links, read_controls = seams()
    res = run_chain(ns["loop"], db, open_page, read_links,
                    read_controls, task,
                    {"legs": [observe_leg(), follow_leg()]},
                    ScriptedDecider(True))
    assert res.status == "PAUSED"
    assert ns["registry"].get(tid).status == Status.PAUSED
    stale = [pid for pid in plan_store.plans_for_task(db, tid)
             if plan_store.load_plan(db, pid)[0].status in
             ("EXECUTING", "DRAFT", "PAUSED")]
    assert stale, "paused run must leave a live plan"
    db.close()
    return db_path, audit_path, tid, stale


def test_paused_chain_resumes_on_same_task(tmp_path, fake_browser,
                                           capsys):
    db_path, audit_path, tid, stale = paused_chain(tmp_path)
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    before = len([e for e in AuditLog(audit_path).replay()
                  if e.get("type") == "PLAN_CREATED"])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 0
    out = capsys.readouterr().out
    assert f"chain legs 1..2" in out
    assert "status: DONE" in out
    # Same task id preserved: exactly one task row, now COMPLETED.
    db = Database(db_path)
    try:
        tasks = task_store.load_all_tasks(db)
        assert [t.task_id for t in tasks] == [tid]
        assert task_store.load_task(db, tid).status == Status.COMPLETED
        # Stale live plan abandoned through the existing mechanism.
        for pid in stale:
            assert plan_store.load_plan(db, pid)[0].status == \
                "SUPERSEDED"
    finally:
        db.close()
    events = AuditLog(audit_path).replay()
    superseded = [e for e in events if e.get("type") == "PLAN_SUPERSEDED"
                  and e.get("task_id") == tid]
    assert {e["payload"]["old_plan"] for e in superseded} >= set(stale)
    # from_leg honored exactly: only the 2 rerun legs planned fresh.
    after = len([e for e in events
                 if e.get("type") == "PLAN_CREATED"])
    assert after - before == 2
    # Slice-50 status derives the latest valid outcome per leg and
    # ignores the superseded plan.
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        summary = chain_summary(db, task, events)
    finally:
        db.close()
    assert summary["total_legs"] == 2
    assert summary["legs_done"] == 2
    assert summary["legs"] == [
        {"index": 1, "road": "observe", "status": "DONE"},
        {"index": 2, "road": "follow", "status": "DONE"}]
    text = render_status(task, "not resumable: test", [], events,
                         summary)
    assert "legs: 2/2 done" in text


def test_step_usage_restored_not_reset(tmp_path, fake_browser):
    db_path, audit_path, tid, _ = paused_chain(tmp_path)
    db = Database(db_path)
    try:
        task_store.record_steps(db, tid, 7)
        assert task_store.get_usage(db, tid)[0] == 7
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    assert run_resume(tmp_path, db_path, audit_path, tid, chain,
                       from_leg=1) == 0
    db = Database(db_path)
    try:
        # Budgets continue from restored usage, never reset to zero.
        assert task_store.get_usage(db, tid)[0] >= 7
    finally:
        db.close()


def test_stale_plan_scoped_to_this_task(tmp_path, fake_browser):
    db_path, audit_path, tid, stale = paused_chain(tmp_path)
    db = Database(db_path)
    try:
        # A live plan belonging to ANOTHER task (goal text only needs
        # a matching planner template; chain membership is irrelevant).
        reg = TaskRegistry(store=db)
        other_task = reg.add(Task.create(
            "Show Records", allowed_tools=["browser"],
            allowed_domains=["file:"], allowed_paths=[]))
        reg.checkout(other_task.task_id, "cli")
        reg.set_status(other_task.task_id, Status.RUNNING)
        from lakra.control.planner import Planner
        plan = Planner().plan(other_task,
                              {"url": "file:///l.html",
                               "expect_text": "Records"})
        plan_store.save_plan(db, plan, {"url": "file:///l.html",
                                        "expect_text": "Records"})
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    assert run_resume(tmp_path, db_path, audit_path, tid, chain,
                       from_leg=1) == 0
    db = Database(db_path)
    try:
        # This task's stale plan superseded; the other task's plan
        # untouched (no cross-task superseding).
        for pid in stale:
            assert plan_store.load_plan(db, pid)[0].status == \
                "SUPERSEDED"
        assert plan_store.load_plan(db, plan.plan_id)[0].status == \
            "DRAFT"
    finally:
        db.close()
    mine = [e for e in AuditLog(audit_path).replay()
            if e.get("type") == "PLAN_SUPERSEDED"]
    assert mine and all(e.get("task_id") == tid for e in mine)


# -- L3 legs: fresh approval, terminal denial, inert stale tokens (17-19) -----

def test_l3_leg_creates_fresh_approval(tmp_path, fake_browser):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db, goal="Chain of 3 legs")
        assert db.execute(
            "SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json",
                        [observe_leg(), form_leg(), observe_leg()])
    # Fresh chain task, resume from the form leg: the L3 gate parks
    # FRESH and --yes clears exactly that one gate.
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=2)
    assert rc == 0
    db = Database(db_path)
    try:
        rows = db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        assert rows == 1
        consumed = sum(1 for e in AuditLog(audit_path).replay()
                       if e.get("type") == "APPROVAL_CONSUMED")
        assert consumed == 1  # the fresh token only
        assert task_store.load_task(db, tid).status == Status.COMPLETED
    finally:
        db.close()


def test_l3_denial_remains_terminal(tmp_path, fake_browser, capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json", [observe_leg(), form_leg()])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=2, extra=("--no",))
    assert rc == 2  # existing denial exit code
    db = Database(db_path)
    try:
        assert task_store.load_task(db, tid).status == Status.CANCELLED
    finally:
        db.close()
    # A terminal CANCELLED task cannot be resurrected.
    n_events = len(AuditLog(audit_path).replay())
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=2)
    assert rc == 1
    assert "history immutable" in capsys.readouterr().out
    assert len(AuditLog(audit_path).replay()) == n_events  # read-only


def test_consumed_token_cannot_authorize_new_plan(tmp_path,
                                                 fake_browser):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json",
                        [form_leg(), observe_leg(expect="Nope")])
    # First resume: form approved (token A consumed), then the bad
    # observe leg stops the chain; task stays RUNNING.
    assert run_resume(tmp_path, db_path, audit_path, tid, chain,
                       from_leg=1) == 1
    db = Database(db_path)
    try:
        first = [r[0] for r in db.execute(
            "SELECT approval_id FROM approvals").fetchall()]
        assert len(first) == 1
        used = db.execute("SELECT used FROM tokens WHERE approval_id=?",
                          (first[0],)).fetchone()[0]
        assert used == 1
        assert task_store.load_task(db, tid).status == Status.RUNNING
    finally:
        db.close()
    # Second resume re-runs the form leg: a FRESH approval appears, the
    # settled one stays inert.
    chain2 = write_chain(tmp_path, "c2.json",
                         [form_leg(), observe_leg()])
    assert run_resume(tmp_path, db_path, audit_path, tid, chain2,
                       from_leg=1) == 0
    db = Database(db_path)
    try:
        ids = [r[0] for r in db.execute(
            "SELECT approval_id FROM approvals ORDER BY requested_at"
        ).fetchall()]
        assert len(ids) == 2 and ids[0] == first[0] and ids[1] != ids[0]
        ns = stub_stack(db, AuditLog(audit_path))
        with pytest.raises(ApprovalError):
            ns["approvals"].decide(first[0], True)  # stale: settled
    finally:
        db.close()


# -- lifecycle: failure during resume stays stopped (test 22) ------------------

def test_failure_during_resume_stays_running(tmp_path, fake_browser,
                                             capsys):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    chain = write_chain(tmp_path, "c.json",
                        [observe_leg(), observe_leg(expect="Nope")])
    rc = run_resume(tmp_path, db_path, audit_path, tid, chain,
                     from_leg=1)
    assert rc == 1
    assert "stopped" in capsys.readouterr().out
    db = Database(db_path)
    try:
        # Existing lifecycle mapping: stopped leg stalls, task RUNNING.
        assert task_store.load_task(db, tid).status == Status.RUNNING
    finally:
        db.close()


# -- refusal path is read-only (test 24) ----------------------------------------

def test_refused_discovery_path_is_read_only(tmp_path, fake_browser):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        ns = stub_stack(db, AuditLog(audit_path))
        tid = make_chain_task(db)
        ns["approvals"].request(
            tid, Action(kind="browser.submit", target="#s",
                        effect="consequential", task_id=tid))
    finally:
        db.close()
    AuditLog(audit_path).log("PLAN_CREATED", tid,
                             {"plan_id": "p", "steps": 1})
    before_db, before_audit = file_hash(db_path), file_hash(audit_path)
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])
    assert run_resume(tmp_path, db_path, audit_path, tid, chain,
                       from_leg=1) == 1
    assert file_hash(db_path) == before_db
    assert file_hash(audit_path) == before_audit
    assert launches(fake_browser) == 0


# -- CLI matrix: existing behavior unchanged (tests 25-26) ------------------------

def test_single_resume_rejects_chain_flags(tmp_path):
    from lakra_do import run_resume
    opts = {"allow_domains": [], "resume": "tid",
            "chain_file": "c.json"}
    assert run_resume([], opts, tmp_path / "a.jsonl", tmp_path / "p",
                       tmp_path / "s", HealthyMonitor()) == 1
    opts = {"allow_domains": [], "resume": "tid", "from_leg": "2"}
    assert run_resume([], opts, tmp_path / "a.jsonl", tmp_path / "p",
                       tmp_path / "s", HealthyMonitor()) == 1


def test_fresh_chain_rejects_resume_flag(tmp_path):
    from lakra_do import run_chain_file
    opts = {"allow_domains": [], "resume": "tid",
            "chain_file": "c.json"}
    assert run_chain_file([], opts, tmp_path / "a.jsonl",
                           tmp_path / "p", tmp_path / "s",
                           HealthyMonitor()) == 1


def test_main_dispatch_matrix(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake(kind):
        def _fn(*a, **k):
            seen[kind] = True
            return 0
        return _fn

    monkeypatch.setattr(lakra_do, "run_chain_resume", fake("resume-chain"))
    monkeypatch.setattr(lakra_do, "run_chain_file", fake("fresh-chain"))
    monkeypatch.setattr(lakra_do, "run_resume", fake("single-resume"))
    chain = write_chain(tmp_path, "c.json", [observe_leg(), follow_leg()])

    def main(*argv):
        return lakra_do.main(["--audit", str(tmp_path / "a.jsonl"),
                              "--profile-dir", str(tmp_path / "p"),
                              "--shots-dir", str(tmp_path / "s"),
                              *argv])

    assert main("--resume", "tid", "--chain-file", chain,
                "--from-leg", "2", "--yes") == 0
    assert seen == {"resume-chain": True}
    seen.clear()
    assert main("--chain-file", chain, "--from-leg", "2", "--yes") == 0
    assert seen == {"fresh-chain": True}
    seen.clear()
    assert main("--resume", "tid", "--yes") == 0
    assert seen == {"single-resume": True}


# -- V1-D1: chain marker is write-once -------------------------------------------
# update_task() never persists goal, so leg routing text mutated in
# memory by run_chain() can never overwrite the "Chain of N legs"
# marker — including across mid-chain process death. Bare --resume
# refuses chain tasks (read-only) with chain-resume guidance.

def test_update_task_never_persists_goal(tmp_path):
    db = Database(tmp_path / "d1.db")
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create("Chain of 2 legs",
                                allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        reg.checkout(t.task_id, "cli")
        reg.set_status(t.task_id, Status.RUNNING)
        # Simulate the run_chain() routing mutation, then persist.
        t.goal = "Submit the form"
        task_store.update_task(db, t)
        reloaded = task_store.load_task(db, t.task_id)
        assert reloaded.goal == "Chain of 2 legs"
        # All other columns still persist through the same call.
        assert reloaded.status == Status.RUNNING
        assert reloaded.owner == "cli"
    finally:
        db.close()


@pytest.mark.parametrize("routing", ["Show Records", "Click the control",
                                      "Submit the form"])
def test_routing_goals_cannot_overwrite_chain_identity(tmp_path,
                                                        routing):
    """Each representative leg routing text is unpersistable."""
    db = Database(tmp_path / "d1.db")
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create("Chain of 3 legs",
                                allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        reg.checkout(t.task_id, "cli")
        reg.set_status(t.task_id, Status.RUNNING)
        t.goal = routing
        reg.set_status(t.task_id, Status.WAITING_APPROVAL)
        reloaded = task_store.load_task(db, t.task_id)
        assert reloaded.goal == "Chain of 3 legs"
        assert reloaded.status == Status.WAITING_APPROVAL
    finally:
        db.close()


def test_non_chain_task_goal_still_stable(tmp_path):
    """Non-chain tasks: insert-time goal reads back unchanged across
    normal status persistence (existing behavior preserved)."""
    db = Database(tmp_path / "d1.db")
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create("Do the thing",
                                allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        reg.checkout(t.task_id, "cli")
        reg.set_status(t.task_id, Status.RUNNING)
        reloaded = task_store.load_task(db, t.task_id)
        assert reloaded.goal == "Do the thing"
        assert reloaded.status == Status.RUNNING
    finally:
        db.close()


def test_bare_resume_refuses_chain_task_read_only(
        tmp_path, monkeypatch, capsys):
    """Bare --resume on a chain task refuses with chain-resume
    guidance: no browser, no execution, no approvals, no audit or DB
    writes."""
    from lakra_do import run_resume
    db_path = tmp_path / "d1.db"
    audit_path = tmp_path / "a.jsonl"
    db = Database(db_path)
    try:
        tid = make_chain_task(db)
    finally:
        db.close()
    launched = []

    class NoBrowser:
        def __init__(self, profile_dir, accept_downloads=False,
                     exclusive=False):
            pass

        def launch(self):
            launched.append(True)
            raise AssertionError("browser must not launch")

        def close(self):
            pass

    monkeypatch.setattr(lakra_do, "BrowserSessions", NoBrowser)
    monkeypatch.setenv("LAKRA_DB", str(db_path))
    AuditLog(audit_path).log("SEED", tid, {"note": "pre-existing"})
    before_db = file_hash(db_path)
    before_audit = file_hash(audit_path)
    db = Database(db_path)
    try:
        n_approvals = db.execute(
            "SELECT COUNT(*) FROM approvals").fetchone()[0]
    finally:
        db.close()
    opts = {"allow_domains": [], "resume": tid}
    rc = run_resume(["--resume", tid, "--yes"], opts, audit_path,
                    tmp_path / "p", tmp_path / "s", HealthyMonitor())
    assert rc == 1
    out = capsys.readouterr().out
    assert "is a chain task" in out and "--chain-file" in out
    assert launched == []
    assert file_hash(db_path) == before_db
    # Audit file: byte-identical (no event created).
    assert file_hash(audit_path) == before_audit
    db = Database(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM approvals").fetchone()[0] == \
            n_approvals
    finally:
        db.close()


def test_bare_resume_non_chain_task_passes_guard(
        tmp_path, monkeypatch, capsys):
    """Non-chain tasks flow past the V1-D1 guard to existing
    discovery (unknown task refuses there, unchanged)."""
    from lakra_do import run_resume
    db_path = tmp_path / "d1.db"
    Database(db_path).close()

    class NoBrowser:
        def __init__(self, profile_dir, accept_downloads=False,
                     exclusive=False):
            pass

        def launch(self):
            raise RuntimeError("no browser in unit test")

        def close(self):
            pass

    monkeypatch.setattr(lakra_do, "BrowserSessions", NoBrowser)
    monkeypatch.setenv("LAKRA_DB", str(db_path))
    opts = {"allow_domains": [], "resume": "no-such-task"}
    rc = run_resume(["--resume", "no-such-task", "--yes"], opts,
                    tmp_path / "a.jsonl", tmp_path / "p",
                    tmp_path / "s", HealthyMonitor())
    # Past the chain guard (no chain guidance) into the existing
    # launch/discovery path, which refuses without a browser here.
    out = capsys.readouterr().out
    assert "is a chain task" not in out
    assert rc == 1


# -- V1-D1: live kill-at-park regression ------------------------------------------
# Killed mid-chain at an L3 park, the persisted goal must remain the
# chain marker; --status keeps the chain section; chain resume on the
# same id completes; bare --resume refuses with guidance.

D1_FIXTURES = Path(__file__).parent / "fixtures"
D1_LIST = (D1_FIXTURES / "loop-index.html").as_uri()
D1_FORM = (D1_FIXTURES / "courses.html").as_uri()
D1_SLOTS = {"name": {"text": "Ada"}}


def _d1_cli(tmp_path, db_name="cli.db"):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / db_name)
    base = [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(tmp_path / "audit-do.jsonl"),
            "--ram-floor-mb", "0", "--cpu-ceiling", "100"]
    return base, env


def _d1_wait_park(audit_path, timeout_s=150):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            lines = Path(audit_path).read_text(
                encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in reversed(lines):
            if '"ACTION_REQUIRES_APPROVAL"' in line:
                return True
        time.sleep(2)
    return False


def _d1_task_id(tmp_path, db_name="cli.db"):
    import sqlite3
    con = sqlite3.connect(str(tmp_path / db_name))
    try:
        row = con.execute("SELECT task_id, goal, status FROM tasks"
                          " ORDER BY created_at DESC LIMIT 1").fetchone()
    finally:
        con.close()
    assert row is not None
    return row


def _d1_killed_chain(tmp_path):
    """Run observe->form->observe with --poll, kill at the L3 park.
    Returns (task_id, chain_path)."""
    import subprocess
    base, env = _d1_cli(tmp_path)
    chain_path = tmp_path / "d1chain.json"
    chain_path.write_text(json.dumps({"legs": [
        {"road": "observe", "url": D1_LIST,
         "expect_text": "Records"},
        {"road": "form", "form_url": D1_FORM,
         "goal_slots": D1_SLOTS, "submit_selector": "#m-submit"},
        {"road": "observe", "url": D1_LIST,
         "expect_text": "Records"}]}), encoding="utf-8")
    proc = subprocess.Popen(
        base + ["--profile-dir", str(tmp_path / "prof-die"),
                "--shots-dir", str(tmp_path / "shots-die"),
                "--chain-file", str(chain_path), "--poll", "180"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env)
    try:
        assert _d1_wait_park(tmp_path / "audit-do.jsonl"), \
            "chain never parked at the form L3 gate"
        proc.terminate()  # death mid-chain at the L3 park
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    tid, _, _ = _d1_task_id(tmp_path)
    return tid, str(chain_path), env


def test_kill_at_park_preserves_chain_marker(tmp_path):
    tid, _, _ = _d1_killed_chain(tmp_path)
    _, goal, _ = _d1_task_id(tmp_path)
    assert goal == "Chain of 3 legs"


def test_status_after_kill_shows_chain(tmp_path):
    import subprocess
    tid, _, env = _d1_killed_chain(tmp_path)
    base, _ = _d1_cli(tmp_path)
    out = subprocess.run(
        base + ["--profile-dir", str(tmp_path / "prof-s"),
                "--shots-dir", str(tmp_path / "shots-s"),
                "--status", tid],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=240)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "legs:" in out.stdout and "chain:" in out.stdout


def test_bare_resume_after_kill_refuses_with_guidance(tmp_path):
    import subprocess
    tid, _, env = _d1_killed_chain(tmp_path)
    base, _ = _d1_cli(tmp_path)
    before = (tmp_path / "audit-do.jsonl").read_bytes()
    out = subprocess.run(
        base + ["--profile-dir", str(tmp_path / "prof-r"),
                "--shots-dir", str(tmp_path / "shots-r"),
                "--resume", tid, "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=240)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "is a chain task" in out.stdout
    assert "--chain-file" in out.stdout
    assert (tmp_path / "audit-do.jsonl").read_bytes() == before


def test_chain_resume_after_kill_completes_same_id(tmp_path):
    import subprocess
    tid, chain_path, env = _d1_killed_chain(tmp_path)
    base, _ = _d1_cli(tmp_path)
    approved = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "approve.py"), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert "approved" in approved.stdout, approved.stdout
    out = subprocess.run(
        base + ["--profile-dir", str(tmp_path / "prof-re"),
                "--shots-dir", str(tmp_path / "shots-re"),
                "--resume", tid, "--chain-file", chain_path,
                "--from-leg", "2", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "status: DONE" in out.stdout
    assert "chain legs 2..3" in out.stdout
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "cli.db"))
    try:
        rows = con.execute(
            "SELECT task_id, goal, status FROM tasks").fetchall()
        consumed = con.execute("SELECT COUNT(*) FROM tokens WHERE"
                               " used=1").fetchone()[0]
    finally:
        con.close()
    assert len(rows) == 1 and rows[0][0] == tid  # same task id
    assert rows[0][1] == "Chain of 3 legs"  # marker intact
    assert rows[0][2] == "COMPLETED"
    assert consumed == 1  # exactly-once: the fresh approval only


def test_no_new_persistence_surface(tmp_path):
    import sqlite3
    db_path = tmp_path / "d1.db"
    db = Database(db_path)
    try:
        make_chain_task(db)
    finally:
        db.close()
    con = sqlite3.connect(str(db_path))
    try:
        tables = sorted(
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'").fetchall())
        version = con.execute(
            "SELECT value FROM meta WHERE key='v'").fetchone()
    finally:
        con.close()
    assert tables == ["approvals", "attempts", "failure_records",
                      "loops", "meta", "plan_steps", "plans", "runs",
                      "tasks", "tokens", "usage", "work_items"]
    assert version is not None and int(version[0]) == 6  # V2-02 v6

