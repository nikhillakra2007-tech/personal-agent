"""Resource admission tests: declared capacity only, no hardware claims."""

from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Capacity, Needs, Scheduler
from lakra.control.tasks import Status, Task


def make(r, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["x.example"])
    t = r.add(Task.create("g", **kw))
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    return t


def test_within_declared_budget_admitted():
    s = Scheduler(TaskRegistry(), Capacity(ram_mb_claimable=2048))
    t = make(s.registry)
    s.enqueue(t.task_id, Needs(ram_mb=512))
    assert s.next_turn() is not None


def test_exceeding_declared_capacity_rejected():
    s = Scheduler(TaskRegistry(), Capacity(ram_mb_claimable=2048,
                                           vram_mb_claimable=1024))
    t = make(s.registry)
    s.enqueue(t.task_id, Needs(ram_mb=99999))
    assert s.next_turn() is None  # still queued, not dropped
    assert len(s) == 1


def test_capacity_defaults_are_placeholders_not_telemetry():
    c = Capacity()
    # Guard: nobody may mistake these for measured hardware values.
    assert c.ram_mb_claimable <= 4096
    assert c.vram_mb_claimable <= 2048
    assert c.max_concurrent_turns == 1


def test_release_turn_requeues_and_terminal_drops():
    s = Scheduler(TaskRegistry())
    t = make(s.registry)
    s.enqueue(t.task_id)
    turn = s.next_turn()
    assert turn is not None
    s.release_turn(t.task_id)
    assert s.next_turn() is not None
    s.cancel(t.task_id)
    s.release_turn(t.task_id)
    assert len(s) == 0
