"""T3 tests: registry ownership + atomic checkout. T4 tests: audit replay."""

import threading

import pytest

from lakra.control.audit import AuditLog
from lakra.control.registry import CheckoutError, RegistryError, TaskRegistry
from lakra.control.tasks import Status, Task


def test_add_get_roundtrip():
    r = TaskRegistry()
    t = r.add(Task.create("g"))
    assert r.get(t.task_id) is t
    assert len(r) == 1


def test_get_unknown_raises():
    with pytest.raises(RegistryError):
        TaskRegistry().get("nope")


def test_second_checkout_fails():
    r = TaskRegistry()
    t = r.add(Task.create("g"))
    r.checkout(t.task_id, "exec-1")
    with pytest.raises(CheckoutError):
        r.checkout(t.task_id, "exec-2")
    assert r.get(t.task_id).owner == "exec-1"


def test_release_by_holder_only():
    r = TaskRegistry()
    t = r.add(Task.create("g"))
    r.checkout(t.task_id, "exec-1")
    with pytest.raises(CheckoutError):
        r.release(t.task_id, "exec-2")
    r.release(t.task_id, "exec-1")
    assert r.get(t.task_id).owner is None


def test_concurrent_checkout_single_winner():
    r = TaskRegistry()
    t = r.add(Task.create("g"))
    winners, errors = [], []

    def grab(owner):
        try:
            r.checkout(t.task_id, owner)
            winners.append(owner)
        except CheckoutError:
            errors.append(owner)

    threads = [threading.Thread(target=grab, args=(f"exec-{i}",))
               for i in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(winners) == 1 and len(errors) == 15


def test_set_status_validates():
    r = TaskRegistry()
    t = r.add(Task.create("g"))
    r.checkout(t.task_id, "exec-1")
    r.set_status(t.task_id, Status.RUNNING)
    assert r.get(t.task_id).status == Status.RUNNING


def test_audit_append_and_replay_equal(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.log("TASK_CREATED", "t1", {"goal": "g"})
    log.log("ACTION_ALLOWED", "t1", {"kind": "browser.navigate"})
    events = log.replay()
    assert [e["type"] for e in events] == ["TASK_CREATED", "ACTION_ALLOWED"]
    assert all(e["task_id"] == "t1" for e in events)
    assert all("ts" in e for e in events)


def test_audit_replay_missing_file_is_empty(tmp_path):
    assert AuditLog(tmp_path / "nope.jsonl").replay() == []
