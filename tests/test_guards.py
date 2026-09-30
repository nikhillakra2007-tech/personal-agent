"""Slice-20: guard verdicts + run-loop enforcement (stub executors, no browser)."""

import time

from lakra.control.audit import AuditLog
from lakra.control.budgets import TokenLedger
from lakra.control.guards import Guards
from lakra.control.planner import Planner
from lakra.control.registry import TaskRegistry
from lakra.control.runner import Runner
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Budget, Status, Task
from lakra.execution.browser.controller import StepResult
from lakra.resources.monitor import SystemSnapshot


def _task(r, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    t = r.add(Task.create("List the test things", **kw))
    r.checkout(t.task_id, "e")
    r.set_status(t.task_id, Status.RUNNING)
    return t


def _start(r, sched, **kw):
    t = _task(r, **kw)
    sched.enqueue(t.task_id)
    return t


class HealthyMonitor:
    def sample(self, force=False):
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384, ram_available_mb=8192,
            cpu_percent=5.0, lakra_rss_mb=50.0, browser_rss_mb=0.0,
            gpu_available=False)

    def under_pressure(self, **kw):
        return False, "ok"


class StarvedMonitor(HealthyMonitor):
    def sample(self, force=False):
        snap = super().sample(force)
        snap.ram_available_mb = 64
        snap.cpu_percent = 99.0
        return snap

    def under_pressure(self, **kw):
        return True, "low RAM: 64 MB available"


class ExplodingMonitor:
    def sample(self, force=False):
        raise RuntimeError("nvidia-smi went away")

    def under_pressure(self):
        raise RuntimeError("telemetry unavailable")


class FakeController:
    """Always-verify controller over stub steps (no browser needed)."""

    def __init__(self, router=None):
        self.router = router
        self.steps = 0

    def run_step(self, task_id, step):
        self.steps += 1
        return StepResult(True, True, 1, "CONTINUE", "verified")


def _rig(tmp_path, monitor=None, ledger_for=None):
    db = Database(tmp_path / "g.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    guards = Guards(scheduler=sched, ledger_for=ledger_for,
                    monitor=monitor)
    runner = Runner(FakeController(), sched, audit, store=db,
                    guards=guards)
    return registry, sched, audit, runner, db


def _plan(task):
    return Planner().plan(task, {"url": "file:///t.html",
                                 "expect_text": "x"})


# -- pure verdicts -----------------------------------------------------------

def test_healthy_proceeds(tmp_path):
    r, s, _, _, _ = _rig(tmp_path, monitor=HealthyMonitor())
    t = _start(r, s)
    v = Guards(scheduler=s, monitor=HealthyMonitor()).check(t.task_id)
    assert v.action == "continue" and v.proceed


def test_no_guards_means_proceed():
    v = Guards().check("anything")
    assert v.action == "continue"


def test_pressure_pauses_with_reason_and_meters(tmp_path):
    r, s, _, _, _ = _rig(tmp_path, monitor=StarvedMonitor())
    t = _start(r, s)
    v = Guards(scheduler=s, monitor=StarvedMonitor()).check(t.task_id)
    assert v.action == "pause"
    assert v.detail.startswith("paused:")
    assert v.meters["ram_available_mb"] == 64


def test_token_exhaustion_stops(tmp_path):
    r, s, _, _, _ = _rig(tmp_path)
    t = _start(r, s)
    spent = TokenLedger(10)
    spent.charge(10)
    v = Guards(scheduler=s,
               ledger_for=lambda tid: spent).check(t.task_id)
    assert v.action == "stop" and "tokens" in v.detail


def test_monitor_exception_fails_safe_to_pause(tmp_path):
    r, s, _, _, _ = _rig(tmp_path, monitor=ExplodingMonitor())
    t = _start(r, s)
    v = Guards(scheduler=s, monitor=ExplodingMonitor()).check(t.task_id)
    assert v.action == "pause" and "guard-error" in v.detail


def test_ledger_provider_exception_fails_safe_to_pause(tmp_path):
    r, s, _, _, _ = _rig(tmp_path)
    t = _start(r, s)

    def boom(tid):
        raise RuntimeError("ledger store down")

    v = Guards(scheduler=s, ledger_for=boom).check(t.task_id)
    assert v.action == "pause" and "guard-error" in v.detail


def test_unknown_task_stops_closed(tmp_path):
    v = Guards(scheduler=Scheduler(TaskRegistry())).check("nope")
    assert v.action == "stop"


def test_step_budget_stops(tmp_path):
    r, s, _, _, _ = _rig(tmp_path, monitor=HealthyMonitor())
    t = _start(r, s, budget=Budget(max_steps=0, max_tokens_cents=0,
                                   max_minutes=30))
    v = Guards(scheduler=s, monitor=HealthyMonitor()).check(t.task_id)
    assert v.action == "stop" and v.detail == "budget exhausted"


# -- run-loop enforcement ----------------------------------------------------

def test_healthy_run_goes_done(tmp_path):
    r, s, a, runner, _ = _rig(tmp_path, monitor=HealthyMonitor())
    t = _start(r, s)
    res = runner.run(_plan(t))
    assert (res.status, res.steps_done) == ("DONE", 2)
    assert [e["type"] for e in a.replay()].count("GUARD_TRIP") == 0


def test_pressure_pauses_then_relief_resumes_to_done(tmp_path):
    mon = StarvedMonitor()
    r, s, a, runner, db = _rig(tmp_path, monitor=mon)
    t = _start(r, s)
    plan = _plan(t)
    res = runner.run(plan)
    assert res.status == "PAUSED" and res.steps_done == 0
    assert r.get(t.task_id).status == Status.PAUSED
    trips = [e for e in a.replay() if e["type"] == "GUARD_TRIP"]
    assert trips and trips[0]["payload"]["action"] == "pause"
    assert any(e["type"] == "PLAN_OUTCOME" and
               e["payload"]["status"] == "PAUSED" for e in a.replay())
    # Relief: healthy air + scheduler resume + runner resume from store.
    runner.guards.monitor = HealthyMonitor()
    s.resume(t.task_id)
    out = runner.resume(plan.plan_id, observe=lambda: "fresh snapshot")
    assert (out.status, out.steps_done) == ("DONE", 2)


def test_token_exhaustion_stops_with_guard_trip(tmp_path):
    spent = TokenLedger(4)
    spent.charge(4)
    r, s, a, runner, _ = _rig(tmp_path, monitor=HealthyMonitor(),
                              ledger_for=lambda tid: spent)
    t = _start(r, s)
    res = runner.run(_plan(t))
    assert res.status == "STOPPED" and "tokens" in res.detail
    trips = [e for e in a.replay() if e["type"] == "GUARD_TRIP"]
    assert trips and trips[0]["payload"]["action"] == "stop"


def test_telemetry_exception_pauses_not_crashes(tmp_path):
    r, s, a, runner, _ = _rig(tmp_path, monitor=ExplodingMonitor())
    t = _start(r, s)
    res = runner.run(_plan(t))
    assert res.status == "PAUSED" and "guard-error" in res.detail
