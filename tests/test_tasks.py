"""T1 tests: task contract transitions and validation."""

import pytest

from lakra.control.tasks import Status, Task, TransitionError


def make_task(**kw):
    return Task.create("test goal", **kw)


def test_create_defaults():
    t = make_task()
    assert t.status == Status.QUEUED
    assert t.owner is None
    assert t.task_id and t.created_at and t.updated_at
    assert t.verification.state == "UNVERIFIED"


def test_create_rejects_bad_priority_and_level():
    with pytest.raises(ValueError):
        make_task(priority=9)
    with pytest.raises(ValueError):
        make_task(permission_level=3)


def test_running_requires_owner():
    t = make_task()
    with pytest.raises(TransitionError):
        t.transition(Status.RUNNING)


def test_queued_running_completed_is_legal():
    t = make_task()
    t.owner = "exec-1"
    t.transition(Status.RUNNING)
    t.transition(Status.COMPLETED)
    assert t.status == Status.COMPLETED


def test_terminal_states_immutable():
    t = make_task()
    t.owner = "exec-1"
    t.transition(Status.RUNNING)
    t.transition(Status.FAILED)
    for s in Status:
        with pytest.raises(TransitionError):
            t.transition(s)


def test_illegal_jump_rejected():
    t = make_task()
    with pytest.raises(TransitionError):
        t.transition(Status.COMPLETED)  # QUEUED -> COMPLETED skips RUNNING


def test_pause_resume_cycle():
    t = make_task()
    t.owner = "exec-1"
    t.transition(Status.RUNNING)
    t.transition(Status.PAUSED)
    t.transition(Status.RUNNING)
    assert t.status == Status.RUNNING


def test_waiting_approval_cycle():
    t = make_task()
    t.owner = "exec-1"
    t.transition(Status.RUNNING)
    t.transition(Status.WAITING_APPROVAL)
    t.transition(Status.RUNNING)
    assert t.status == Status.RUNNING
