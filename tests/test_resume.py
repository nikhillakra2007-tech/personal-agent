"""Slice-38: resume a stranded task by id.

resume_info() is a pure read over persisted rows (road, plan/loop id,
first-open step, pending approvals, UNCERTAIN flag). resume_task()
enforces the fail-closed preconditions (no duplicate parks, no blind
continues past uncertain steps, ownership, shared-truth status) then
resumes through Runner.resume / LoopRunner.resume with supervised ASK
handling — resumed L3 gates always collect fresh consent. Terminal
history stays immutable; no new audit event types."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lakra.control import plan_store
from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.loop import ScriptedDecider, TaskLoop
from lakra.control.loops import LoopRunner, RepeatSpec
from lakra.control.planner import Planner
from lakra.control.registry import TaskRegistry
from lakra.control.resume import ResumeRefused, resume_info, resume_task
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.execution.registry import ExecuteResult, ToolRegistry
from lakra.execution.router import ToolRouter

ROOT = Path(__file__).resolve().parent.parent
LOOP_INDEX = (Path(__file__).parent / "fixtures" / "loop-index.html").as_uri()
FORM_PAGE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()

KINDS = ("browser.navigate", "browser.snapshot", "browser.click",
         "browser.type", "browser.submit", "browser.check",
         "browser.select")

SLOTS = {"name": {"text": "Ada"},
         "city": {"text": "Lagos"},
         "zip": {"text": "10001"},
         "news": {"checked": True},
         "country": {"select": "ng"}}

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
    def under_pressure(self, **kw):
        return False, "ok"


def rig(tmp_path, name="resume.db", monitor=None):
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
    from lakra.control.guards import Guards
    guards = Guards(scheduler=sched, monitor=monitor or HealthyMonitor())
    runner = Runner(ctrl, sched, audit, observe=lambda: "fresh",
                    store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit, "sched": sched,
            "approvals": approvals, "stub": stub, "runner": runner,
            "loop": loop}


def start(registry, goal="Submit the test form", owner="cli", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    t = registry.add(Task.create(goal, **kw))
    registry.checkout(t.task_id, owner)
    registry.set_status(t.task_id, Status.RUNNING)
    return t


def fresh_stack(ns):
    """Simulate process death: brand-new objects, same database file."""
    return rig(ns["db"].path.parent, name=ns["db"].path.name)


# -- discovery unit ---------------------------------------------------------------

def test_discover_single_follow(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"], "Follow the Beta record")
    plan = Planner().plan(t, {"url": "file:///l.html",
                              "link_text": "Beta record",
                              "expect_text": "detail record: Beta"})
    plan_store.save_plan(ns["db"], plan, {"url": "file:///l.html",
                                          "link_text": "Beta record",
                                          "expect_text":
                                          "detail record: Beta"})
    plan_store.set_step_outcome(ns["db"], plan.plan_id, 0, "done")
    info = resume_info(ns["db"], t.task_id, [])
    assert info["road"] == "single" and info["subkind"] == "follow"
    assert info["plan_id"] == plan.plan_id
    assert (info["first_open"], info["steps"]) == (1, 4)
    assert info["url"] == "file:///l.html"
    assert info["pending_approvals"] == [] and info["uncertain"] == []


def test_discover_single_form_and_loop(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    plan = Planner().plan(t, {"url": "file:///f.html", "fields": [
        {"selector": "#m-name", "text": "Ada"}],
        "submit_selector": "#m-submit"})
    plan_store.save_plan(ns["db"], plan, {"url": "file:///f.html",
                                          "fields": [{"selector": "#m-name",
                                                      "text": "Ada"}],
                                          "submit_selector": "#m-submit"})
    info = resume_info(ns["db"], t.task_id, [])
    assert (info["road"], info["subkind"]) == ("single", "form")
    assert info["first_open"] == 0

    t2 = start(ns["registry"], "Follow every batch record")
    spec = RepeatSpec.create(t2.task_id, "file:///l.html", "record",
                             3, 3, "detail record")
    from lakra.control.loops import _save
    _save(ns["db"], spec, "RUNNING")
    info = resume_info(ns["db"], t2.task_id, [])
    assert info["road"] == "loop" and info["loop_id"] == spec.loop_id
    assert info["cursor"] == 0 and info["findings"] == []


def test_discover_refusals(tmp_path):
    ns = rig(tmp_path)
    with pytest.raises(ResumeRefused):
        resume_info(ns["db"], "no-such-task", [])
    t = start(ns["registry"])
    with pytest.raises(ResumeRefused):
        resume_info(ns["db"], t.task_id, [])  # fresh task, no live unit
    ns["registry"].set_status(t.task_id, Status.CANCELLED)
    with pytest.raises(ResumeRefused):
        resume_info(ns["db"], t.task_id, [])

    t2 = start(ns["registry"], "Follow every batch record")
    from lakra.control.loops import _save
    for _ in range(2):
        _save(ns["db"], RepeatSpec.create(
            t2.task_id, "file:///l.html", "record", 1, 1,
            "detail record"), "RUNNING")
    with pytest.raises(ResumeRefused):
        resume_info(ns["db"], t2.task_id, [])


def test_discover_pending_and_uncertain_reported(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    plan = Planner().plan(t, dict(SUBMIT_HINTS))
    plan_store.save_plan(ns["db"], plan, {})
    from lakra.control.policy import Action
    apv = ns["approvals"].request(
        t.task_id, Action(kind="browser.submit", target="#s",
                          effect="consequential", task_id=t.task_id))
    events = [{"task_id": t.task_id, "type": "EXECUTION_STARTED",
               "payload": {"kind": "browser.submit"}},
              {"task_id": t.task_id, "type": "EXECUTION_COMPLETED",
               "payload": {"kind": "browser.navigate"}}]
    info = resume_info(ns["db"], t.task_id, events)
    assert info["pending_approvals"] == [apv.approval_id]
    assert info["uncertain"] == [{"task_id": t.task_id,
                                  "kind": "browser.submit"}]


# -- stub resume ---------------------------------------------------------------------

def park_submit(ns, t):
    """Run to the L3 gate with no decider: returns parked, then the
    caller drops the stack to simulate death."""
    plan = Planner().plan(t, dict(SUBMIT_HINTS))
    res = ns["runner"].run(plan, hints=dict(SUBMIT_HINTS))
    assert res.status == "ASK_PENDING"
    return plan


def test_park_resume_done_no_replay(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    plan = park_submit(ns, t)
    ns2 = fresh_stack(ns)  # death: brand-new objects, same database file
    info = resume_info(ns2["db"], t.task_id, ns2["audit"].replay())
    assert info["pending_approvals"] != []  # gate still holds a live park
    # Decide the ORIGINAL park first (approve.py pattern), then resume:
    # the resumed run re-parks fresh (crash rule) and the decider clears it.
    pend = ns2["approvals"].pending()
    assert len(pend) == 1
    ns2["approvals"].decide(pend[0]["approval_id"], True)
    t2 = ns2["registry"].get(t.task_id)
    out = resume_task(ns2["loop"], ns2["db"], t2, resume_info(
        ns2["db"], t.task_id, ns2["audit"].replay()),
        ScriptedDecider(True), observe=lambda: "fresh")
    assert out.status == "DONE"
    # navigate + type ran once (pre-death); only the submit re-ran.
    assert ns["stub"].calls + ns2["stub"].calls == 2 + 1
    rows = ns2["db"].execute(
        "SELECT COUNT(*) FROM approvals").fetchone()[0]
    assert rows == 2  # original settled + one fresh park
    assert sum(1 for e in ns2["audit"].replay()
               if e["type"] == "APPROVAL_CONSUMED") == 1  # fresh only


def test_resume_pending_refused_naming_id(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    park_submit(ns, t)
    ns2 = fresh_stack(ns)
    info = resume_info(ns2["db"], t.task_id, ns2["audit"].replay())
    t2 = ns2["registry"].get(t.task_id)
    before = ns2["db"].execute(
        "SELECT COUNT(*) FROM approvals").fetchone()[0]
    out = resume_task(ns2["loop"], ns2["db"], t2, info,
                      ScriptedDecider(True), observe=lambda: "fresh")
    assert out.status == "STOPPED" and out.plan_id == "-"
    assert info["pending_approvals"][0] in out.detail
    after = ns2["db"].execute(
        "SELECT COUNT(*) FROM approvals").fetchone()[0]
    assert after == before  # refused before anything parked or ran


def test_resume_owned_elsewhere_refused(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"], owner="other-live-process")
    plan = Planner().plan(t, dict(SUBMIT_HINTS))
    plan_store.save_plan(ns["db"], plan, dict(SUBMIT_HINTS))
    ns2 = fresh_stack(ns)
    info = resume_info(ns2["db"], t.task_id, [])
    t2 = ns2["registry"].get(t.task_id)
    out = resume_task(ns2["loop"], ns2["db"], t2, info,
                      ScriptedDecider(True), observe=lambda: "fresh")
    assert out.status == "STOPPED" and "ownership" in out.detail


def test_resume_uncertain_refused(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"])
    plan = park_submit(ns, t)
    ns2 = fresh_stack(ns)
    # Settle the park first so the UNCERTAIN rule (not the pending
    # rule) is what refuses: crash-rule precedence is pending first,
    # uncertain second.
    pend = ns2["approvals"].pending()
    assert len(pend) == 1
    ns2["approvals"].decide(pend[0]["approval_id"], True)
    events = ns2["audit"].replay() + [
        {"task_id": t.task_id, "type": "EXECUTION_STARTED",
         "payload": {"kind": "browser.submit"}}]
    info = resume_info(ns2["db"], t.task_id, events)
    assert info["uncertain"] != []
    assert info["pending_approvals"] == []
    t2 = ns2["registry"].get(t.task_id)
    out = resume_task(ns2["loop"], ns2["db"], t2, info,
                      ScriptedDecider(True), observe=lambda: "fresh")
    assert out.status == "STOPPED" and "uncertain" in out.detail
    assert "fresh run" in out.detail


def test_resume_loop_cursor(tmp_path):
    ns = rig(tmp_path)
    t = start(ns["registry"], "Follow every batch record")
    spec = RepeatSpec.create(t.task_id, "file:///l.html", "record",
                             2, 2, "detail record")
    from lakra.control.loops import _save, _update
    _save(ns["db"], spec, "RUNNING")
    _update(ns["db"], spec.loop_id, "RUNNING", 1, ["ax record"],
            None, None)
    ns2 = fresh_stack(ns)
    info = resume_info(ns2["db"], t.task_id, ns2["audit"].replay())
    assert info["road"] == "loop" and info["cursor"] == 1
    t2 = ns2["registry"].get(t.task_id)
    out = resume_task(
        ns2["loop"], ns2["db"], t2, info, ScriptedDecider(True),
        observe=lambda: "fresh",
        inventory_fn=lambda: ["ax record", "bx record"])
    assert (out.status, out.findings) == ("DONE", ["ax record",
                                                   "bx record"])
    assert out.findings[0] == "ax record"  # prior finding kept in order


def test_resume_loop_pending_parked_supervised_consent(tmp_path):
    # Slice-42/D-01: a loop whose pending body plan parked at a gate no
    # longer stops mislabeled ("approval vanished"). The live park
    # refuses first (never duplicate-parked); once the old park is
    # settled externally, resume re-parks FRESH and the decider's
    # consent governs: approve -> the body executes exactly once and
    # only executed work is counted (no phantom); deny -> STOPPED with
    # findings untouched.
    ns = rig(tmp_path)
    # Loop-owned body, as LoopRunner always plans them: the parked
    # approval belongs to the loop task so discovery sees it.
    t = start(ns["registry"], "Submit the batch loops")
    body = Planner().plan(t, dict(SUBMIT_HINTS))
    plan_store.save_plan(ns["db"], body, dict(SUBMIT_HINTS))
    plan_store.set_step_outcome(ns["db"], body.plan_id, 0, "done")
    plan_store.set_step_outcome(ns["db"], body.plan_id, 1, "done")
    res = ns["runner"].run(body, hints=dict(SUBMIT_HINTS))
    assert res.status == "ASK_PENDING"
    spec = RepeatSpec.create(t.task_id, "file:///l.html", "record",
                             1, 1, "detail record")
    from lakra.control.loops import _save, _update
    _save(ns["db"], spec, "RUNNING")
    _update(ns["db"], spec.loop_id, "RUNNING", 0, [], body.plan_id,
            "bx record")
    ns2 = fresh_stack(ns)
    info = resume_info(ns2["db"], t.task_id, ns2["audit"].replay())
    assert info["road"] == "loop"
    t2 = ns2["registry"].get(t.task_id)
    out = resume_task(
        ns2["loop"], ns2["db"], t2, info, ScriptedDecider(True),
        observe=lambda: "fresh",
        inventory_fn=lambda: ["ax record", "bx record"])
    assert out.status == "STOPPED" and "undecided approval" in out.detail
    pend = ns2["approvals"].pending()
    assert len(pend) == 1
    ns2["approvals"].decide(pend[0]["approval_id"], True)
    out = resume_task(
        ns2["loop"], ns2["db"], ns2["registry"].get(t.task_id),
        resume_info(ns2["db"], t.task_id, ns2["audit"].replay()),
        ScriptedDecider(True), observe=lambda: "fresh",
        inventory_fn=lambda: ["ax record", "bx record"])
    assert out.status == "DONE"
    assert out.findings == ["bx record"]  # only executed work counted
    assert sum(1 for e in ns2["audit"].replay()
               if e["type"] == "APPROVAL_CONSUMED") == 1  # fresh row only


# -- live CLI resume (subprocess) ------------------------------------------------------

def run_cli(tmp_path, *argv, db="cli.db"):
    import os
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / db)
    cmd = [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
           "--audit", str(tmp_path / "audit-do.jsonl"),
           "--profile-dir", str(tmp_path / "prof"),
           "--shots-dir", str(tmp_path / "shots"),
           "--ram-floor-mb", "0", "--cpu-ceiling", "100", *argv]
    return cmd, env


def wait_for_park(audit_path, timeout_s=120):
    import time
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


def test_cli_kill_approve_resume_done(tmp_path):
    cmd, env = run_cli(
        tmp_path, "--road", "form", "--url", FORM_PAGE, "--slots-json",
        json.dumps(SLOTS), "--submit", "#m-submit", "--poll", "120")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            cwd=str(ROOT), env=env)
    try:
        assert wait_for_park(tmp_path / "audit-do.jsonl"), \
            "CLI never parked at the gate"
        proc.terminate()  # death mid-wait
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    import os
    env2 = dict(os.environ)
    env2["LAKRA_DB"] = str(tmp_path / "cli.db")
    approved = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "approve.py"), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env2,
        timeout=120)
    assert "approved" in approved.stdout, approved.stdout
    # Pending is settled; resume re-parks fresh and --yes clears it.
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--audit", str(tmp_path / "audit-do.jsonl"),
         "--profile-dir", str(tmp_path / "prof2"),
         "--shots-dir", str(tmp_path / "shots2"),
         "--ram-floor-mb", "0", "--cpu-ceiling", "100",
         "--resume", _task_id(tmp_path), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env2,
        timeout=240)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "status: DONE" in out.stdout


def _task_id(tmp_path):
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "cli.db"))
    try:
        row = con.execute("SELECT task_id FROM tasks ORDER BY"
                          " created_at DESC LIMIT 1").fetchone()
    finally:
        con.close()
    assert row is not None
    return row[0]


def test_cli_resume_pending_refused(tmp_path):
    cmd, env = run_cli(
        tmp_path, "--road", "form", "--url", FORM_PAGE, "--slots-json",
        json.dumps({"name": {"text": "Ada"}}), "--submit", "#m-submit",
        "--poll", "120")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            cwd=str(ROOT), env=env)
    try:
        assert wait_for_park(tmp_path / "audit-do.jsonl"), \
            "CLI never parked at the gate"
        proc.terminate()
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    import os
    env2 = dict(os.environ)
    env2["LAKRA_DB"] = str(tmp_path / "cli.db")
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--audit", str(tmp_path / "audit-do.jsonl"),
         "--profile-dir", str(tmp_path / "prof2"),
         "--shots-dir", str(tmp_path / "shots2"),
         "--ram-floor-mb", "0", "--cpu-ceiling", "100",
         "--resume", _task_id(tmp_path), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env2,
        timeout=240)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "undecided approval" in out.stdout
