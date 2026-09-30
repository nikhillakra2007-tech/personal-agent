"""Lifecycle tests: the full outcome->task matrix, propagation, no overreach."""

import pytest

from lakra.control.audit import AuditLog
from lakra.control.lifecycle import cancel_task, decide, sync
from lakra.control.registry import TaskRegistry
from lakra.control.store import Database
from lakra.control.tasks import Status, Task


def test_decide_matrix():
    assert decide(Status.RUNNING, "DONE", "anything") == "COMPLETED"
    assert decide(Status.PAUSED, "DONE", "anything") == "COMPLETED"
    assert decide(Status.RUNNING, "STOPPED", "policy blocked") is None
    assert decide(Status.RUNNING, "STOPPED", "budget exhausted") is None
    assert decide(Status.RUNNING, "STOPPED",
                  "escalated: whatever") is None
    assert decide(Status.RUNNING, "STOPPED",
                  "controller: boom") == "FAILED"
    assert decide(Status.RUNNING, "ASK_PENDING", "x") is None
    assert decide(Status.RUNNING, "EXECUTING", "x") is None


def _task(r, goal="List lifecycle things", **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["x.example"])
    t = r.add(Task.create(goal, **kw))
    r.checkout(t.task_id, "e")
    r.set_status(t.task_id, Status.RUNNING)
    return t


def test_sync_done_completes(tmp_path):
    r = TaskRegistry()
    log = AuditLog(tmp_path / "a.jsonl")
    t = _task(r)
    assert sync(r, log, t.task_id, "DONE", "all steps verified") == \
        "task -> COMPLETED"
    assert r.get(t.task_id).status == Status.COMPLETED


def test_sync_blocked_stalls_resumable(tmp_path):
    r = TaskRegistry()
    log = AuditLog(tmp_path / "a.jsonl")
    t = _task(r)
    assert sync(r, log, t.task_id, "STOPPED",
                "policy blocked") == "stalled (resumable)"
    assert r.get(t.task_id).status == Status.RUNNING
    assert any(e["type"] == "TASK_STALLED" for e in log.replay())


def test_sync_controller_error_fails(tmp_path):
    r = TaskRegistry()
    log = AuditLog(tmp_path / "a.jsonl")
    t = _task(r)
    sync(r, log, t.task_id, "STOPPED", "controller: boom")
    assert r.get(t.task_id).status == Status.FAILED


def test_sync_leaves_terminal_tasks_alone(tmp_path):
    r = TaskRegistry()
    log = AuditLog(tmp_path / "a.jsonl")
    t = _task(r)
    r.set_status(t.task_id, Status.CANCELLED)
    assert sync(r, log, t.task_id, "DONE", "x") == \
        "task already terminal; untouched"


def test_sync_unknown_task_reported(tmp_path):
    r = TaskRegistry()
    log = AuditLog(tmp_path / "a.jsonl")
    assert sync(r, log, "nope", "DONE", "x").startswith("unknown task")


def test_cancel_propagates_to_live_plans(tmp_path):
    from lakra.control import plan_store
    from lakra.control.planner import Planner
    db = Database(tmp_path / "c.db")
    r = TaskRegistry(store=db)
    t = _task(r)
    plan = Planner().plan(t, {"url": "u", "expect_text": "x"})
    plan_store.save_plan(db, plan, {})
    out = cancel_task(r, t.task_id, store=db)
    assert r.get(t.task_id).status == Status.CANCELLED
    assert out == "cancelled, 1 plan(s) frozen"
    loaded, _, _ = plan_store.load_plan(db, plan.plan_id)
    assert loaded.status == "STOPPED"


def test_cancel_idempotent_and_refuses_garbage(tmp_path):
    r = TaskRegistry()
    t = _task(r)
    assert cancel_task(r, t.task_id) == "cancelled, 0 plan(s) frozen"
    assert cancel_task(r, t.task_id) == "already cancelled"
    with pytest.raises(Exception):
        cancel_task(r, "nope")
