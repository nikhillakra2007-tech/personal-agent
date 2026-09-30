"""Scheduler tests: ordering, exclusion, admission, lifecycle."""

import pytest

from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Capacity, Needs, Scheduler
from lakra.control.tasks import Budget, Status, Task


def make(r, goal="g", priority=1, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["x.example"])
    t = r.add(Task.create(goal, priority=priority, **kw))
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    return t


def sched():
    return Scheduler(TaskRegistry())


def test_priority_ordering_and_fifo_tiebreak():
    s = sched()
    r = s.registry
    low1 = make(r, "low1", priority=0)
    high = make(r, "high", priority=3)
    low2 = make(r, "low2", priority=0)
    for t in (low1, high, low2):
        s.enqueue(t.task_id)
    assert s.next_turn().task.task_id == high.task_id
    assert s.next_turn().task.task_id == low1.task_id
    assert s.next_turn().task.task_id == low2.task_id


def test_terminal_paused_waiting_never_yielded():
    s = sched()
    r = s.registry
    t = make(r, priority=3)
    s.enqueue(t.task_id)
    s.pause(t.task_id)
    assert s.next_turn() is None  # paused: skipped, stays queued
    s.resume(t.task_id)
    assert s.next_turn().task.task_id == t.task_id


def test_waiting_approval_never_yielded():
    s = sched()
    r = s.registry
    t = make(r)
    s.enqueue(t.task_id)
    r.set_status(t.task_id, Status.WAITING_APPROVAL)
    assert s.next_turn() is None


def test_cancelled_dropped_on_release():
    s = sched()
    r = s.registry
    t = make(r)
    s.enqueue(t.task_id)
    s.cancel(t.task_id)
    assert s.next_turn() is None
    assert len(s) == 0


def test_budget_exhaustion_blocks_turns():
    s = sched()
    r = s.registry
    t = make(r, budget=Budget(max_steps=1, max_tokens_cents=0, max_minutes=30))
    s.enqueue(t.task_id)
    assert s.next_turn() is not None
    s.release_turn(t.task_id)
    assert s.next_turn() is None  # steps_used (1) >= max_steps (1)


def test_no_owner_no_turn():
    s = sched()
    r = s.registry
    t = r.add(Task.create("g", allowed_tools=["browser"],
                          allowed_domains=["x.example"]))
    s.enqueue(t.task_id)  # never checked out
    assert s.next_turn() is None


def test_pause_resume_cancel_roundtrip():
    s = sched()
    r = s.registry
    t = make(r)
    s.pause(t.task_id)
    assert r.get(t.task_id).status == Status.PAUSED
    s.resume(t.task_id)
    assert r.get(t.task_id).status == Status.RUNNING
    s.cancel(t.task_id)
    assert r.get(t.task_id).status == Status.CANCELLED
    with pytest.raises(Exception):
        r.set_status(t.task_id, Status.RUNNING)  # terminal immutable


def test_time_budget_helper():
    s = sched()
    r = s.registry
    t = make(r)
    assert s.time_ok(t) is True
