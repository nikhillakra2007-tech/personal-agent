"""Monitor + budgets + pressure-admission tests (real telemetry, sane ranges)."""

import pytest

from lakra.control.budgets import TokenLedger
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Capacity, Needs, Scheduler
from lakra.control.tasks import Status, Task
from lakra.models.base import BudgetExhausted
from lakra.resources.monitor import ResourceMonitor


def test_sample_sane_ranges():
    snap = ResourceMonitor().sample(force=True)
    assert 0 < snap.ram_total_mb < 1_000_000
    assert 0 <= snap.ram_available_mb <= snap.ram_total_mb
    assert 0.0 <= snap.cpu_percent <= 100.0 * 64  # logical-core ceiling
    assert snap.lakra_rss_mb > 0
    assert isinstance(snap.gpu_available, bool)
    if snap.gpu_available:
        assert snap.vram_total_mb and snap.vram_total_mb > 0
        assert 0 <= (snap.vram_free_mb or -1) <= snap.vram_total_mb
    else:
        assert snap.vram_free_mb is None  # unknown, never zero-filled


def test_sample_cached_by_ttl():
    mon = ResourceMonitor(ttl_s=60)
    assert mon.sample() is mon.sample()
    assert mon.sample(force=True) is not mon.sample(force=True)


def test_pressure_check_returns_verdict_tuple():
    pressed, reason = ResourceMonitor().under_pressure()
    assert isinstance(pressed, bool) and isinstance(reason, str)


def test_ledger_arithmetic_and_exhaustion():
    ledger = TokenLedger(10)
    assert ledger.precheck(6) == 6
    ledger.charge(6)
    assert ledger.remaining == 4
    assert ledger.precheck(64) == 4  # grant capped, not refused
    ledger.charge(4)
    with pytest.raises(BudgetExhausted):
        ledger.precheck(1)
    with pytest.raises(ValueError):
        TokenLedger(-1)


class StubMonitor:
    """Scriptable stand-in for pressure tests (real monitor covered above)."""

    def __init__(self, ram_avail, vram_free=None):
        from lakra.resources.monitor import SystemSnapshot
        import time
        self.snap = SystemSnapshot(
            ts=time.time(), ram_total_mb=16384, ram_available_mb=ram_avail,
            cpu_percent=5.0, lakra_rss_mb=50.0, browser_rss_mb=0.0,
            gpu_available=vram_free is not None,
            vram_total_mb=6144 if vram_free is not None else None,
            vram_free_mb=vram_free)

    def sample(self, force=False):
        return self.snap


def _task(r, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["x.example"])
    t = r.add(Task.create("g", **kw))
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    return t


def test_pressure_denies_and_recovers():
    r = TaskRegistry()
    t = _task(r)
    s = Scheduler(r, Capacity(ram_mb_claimable=1 << 20),
                  monitor=StubMonitor(ram_avail=64))  # starved
    s.enqueue(t.task_id, Needs(ram_mb=4096))
    assert s.next_turn() is None
    s.monitor = StubMonitor(ram_avail=1 << 20)  # relief
    turn = s.next_turn()
    assert turn is not None and turn.task.task_id == t.task_id


def test_unknown_gpu_skips_vram_gate():
    r = TaskRegistry()
    t = _task(r)
    # Declared headroom is ample: only the measured (unknown-GPU) path decides.
    s = Scheduler(r, Capacity(vram_mb_claimable=1 << 20),
                  monitor=StubMonitor(ram_avail=1 << 20, vram_free=None))
    s.enqueue(t.task_id, Needs(vram_mb=99999))  # would fail if gated
    assert s.next_turn() is not None
