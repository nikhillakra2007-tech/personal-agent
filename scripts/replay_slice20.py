"""Slice-20 demo: automatic guardrails in the run loop (live browser).

1. Healthy run on the fixture -> DONE, zero GUARD_TRIPs.
2. Starved monitor -> GUARD_TRIP(pause) -> PAUSED at step 0; relief +
   scheduler resume + runner.resume(observe) -> DONE.
3. Exhausted TokenLedger -> GUARD_TRIP(stop) -> STOPPED (no browser needed).
4. Exploding telemetry -> GUARD_TRIP(pause, guard-error), no crash.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice20.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.budgets import TokenLedger  # noqa: E402
from lakra.control.guards import Guards  # noqa: E402
from lakra.control.planner import Planner  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.resources.monitor import SystemSnapshot  # noqa: E402

LOG = ROOT / "var" / "audit-slice20.jsonl"
DBP = ROOT / "var" / "slice20.db"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
for p in (LOG, DBP):
    if p.exists():
        p.unlink()


class ScriptedMonitor:
    """Healthy or starved on demand; explodes when told to."""

    def __init__(self, mode="healthy"):
        self.mode = mode

    def sample(self, force=False):
        if self.mode == "exploding":
            raise RuntimeError("telemetry unavailable")
        starved = self.mode == "starved"
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384,
            ram_available_mb=64 if starved else 8192,
            cpu_percent=99.0 if starved else 5.0,
            lakra_rss_mb=50.0, browser_rss_mb=0.0, gpu_available=False)

    def under_pressure(self, **kw):
        if self.mode == "exploding":
            raise RuntimeError("telemetry unavailable")
        if self.mode == "starved":
            return True, "low RAM: 64 MB available"
        return False, "ok"


def rig(monitor, ledger_for=None):
    db = Database(DBP)
    registry = TaskRegistry(store=db)
    audit = AuditLog(LOG)
    sched = Scheduler(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(ROOT / "var" / "slice20-profile")
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, ROOT / "var" / "slice20-shots")
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    guards = Guards(scheduler=sched, ledger_for=ledger_for, monitor=monitor)
    runner = Runner(ctrl, sched, audit,
                    observe=lambda: hands.page.locator("body").inner_text(),
                    store=db, guards=guards)
    return db, registry, audit, sched, sessions, hands, runner


def start(registry, sched, goal):
    task = registry.add(Task.create(
        goal, allowed_tools=["browser"], allowed_domains=["file:"],
        allowed_paths=[str(ROOT)]))
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)
    sched.enqueue(task.task_id)
    return task


HINTS = {"url": FIXTURE, "expect_text": "pending"}

# 1. Healthy -> DONE ---------------------------------------------------------
mon = ScriptedMonitor("healthy")
db, registry, audit, sched, sessions, hands, runner = rig(mon)
task = start(registry, sched, "List my pending courses")
res = runner.run(Planner().plan(task, dict(HINTS)), hints=dict(HINTS))
assert (res.status, res.steps_done) == ("DONE", 2), res
assert not [e for e in audit.replay() if e["type"] == "GUARD_TRIP"]
print("1. healthy -> DONE, zero GUARD_TRIPs")

# 2. Pressure -> PAUSED -> relief -> resume -> DONE ----------------------------
mon.mode = "starved"
task2 = start(registry, sched, "List my pending courses")
plan2 = Planner().plan(task2, dict(HINTS))
res2 = runner.run(plan2, hints=dict(HINTS))
assert res2.status == "PAUSED" and res2.steps_done == 0, res2
assert registry.get(task2.task_id).status == Status.PAUSED
trips = [e for e in audit.replay() if e["type"] == "GUARD_TRIP"]
assert trips and trips[-1]["payload"]["action"] == "pause", trips
print(f"2a. pressure -> GUARD_TRIP(pause) -> PAUSED ({res2.detail})")
mon.mode = "healthy"
sched.resume(task2.task_id)
out = runner.resume(plan2.plan_id, observe=lambda: "fresh snapshot")
assert (out.status, out.steps_done) == ("DONE", 2), out
print("2b. relief + resume -> DONE")
sessions.close()
db.close()

# 3 + 4. No browser needed: pure guard verdicts -------------------------------
from lakra.control.scheduler import Scheduler as _S  # noqa: E402
from lakra.control.registry import TaskRegistry as _R  # noqa: E402

r = _R()
spent = TokenLedger(4)
spent.charge(4)
s = _S(r)
t = r.add(Task.create("g", allowed_tools=["browser"]))
r.checkout(t.task_id, "e")
r.set_status(t.task_id, Status.RUNNING)
s.enqueue(t.task_id)
v = Guards(scheduler=s, ledger_for=lambda tid: spent).check(t.task_id)
assert v.action == "stop" and "tokens" in v.detail, v
print(f"3. token exhaustion -> stop ({v.detail})")

r2 = _R()
s2 = _S(r2)
t2 = r2.add(Task.create("g", allowed_tools=["browser"]))
r2.checkout(t2.task_id, "e")
r2.set_status(t2.task_id, Status.RUNNING)
s2.enqueue(t2.task_id)
v2 = Guards(scheduler=s2,
            monitor=ScriptedMonitor("exploding")).check(t2.task_id)
assert v2.action == "pause" and "guard-error" in v2.detail, v2
print(f"4. telemetry exception -> pause, fail-safe ({v2.detail})")
print("REPLAY OK")
