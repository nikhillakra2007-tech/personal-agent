"""Slice-11 demo: genuine replan + kill-and-resume.

1. Plan runs into a wrong expectation -> escalate -> fresh observation ->
   NEW persisted plan (old frozen SUPERSEDED) -> bounded STOP.
2. Kill-and-resume: step 0 recorded done, process "dies", fresh objects
   reopen the same DB, resume() REQUIRES fresh observation, continues at
   step 1 to DONE. Superseded/done history is never rewritten.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice11.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import plan_store  # noqa: E402
from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
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

DB = ROOT / "var" / "slice11.db"
LOG = ROOT / "var" / "audit-slice11.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
for p in (DB, LOG):
    if p.exists():
        p.unlink()


def rig(db_path, log_path, profile):
    db = Database(db_path)
    registry = TaskRegistry(store=db)
    audit = AuditLog(log_path)
    sched = Scheduler(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(profile)
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, profile.parent / "shots11")
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    runner = Runner(ctrl, sched, audit,
                    observe=lambda: hands.page.locator("body").inner_text(),
                    store=db)
    return (db, registry, audit, sched, sessions, hands, obs, runner,
            router, tools)


def start(rig_registry, rig_sched, goal, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    kw.setdefault("priority", 1)
    task = rig_registry.add(Task.create(goal, **kw))
    rig_registry.checkout(task.task_id, "exec")
    rig_registry.set_status(task.task_id, Status.RUNNING)
    rig_sched.enqueue(task.task_id)
    return task


# 1. genuine replan ------------------------------------------------------------------
db, registry, audit, sched, sessions, hands, obs, runner = rig(
    DB, LOG, ROOT / "var" / "slice11-profile")[:8]
task = start(registry, sched, "Click the toggle button")
hands.open(FIXTURE)
hints = {"selector": "#toggle", "expect_text": "never-appears"}
plan = Planner().plan(task, dict(hints))
res = runner.run(plan, hints=hints)
assert res.status == "STOPPED", res
ids = plan_store.plans_for_task(db, task.task_id)
assert len(ids) == 2, ids
old, _, _ = plan_store.load_plan(db, plan.plan_id)
assert old.status == "SUPERSEDED"
new_id = [i for i in ids if i != plan.plan_id][0]
_, new_hints, _ = plan_store.load_plan(db, new_id)
assert new_hints.get("achieved") == []
print(f"1. escalate -> replan -> new plan {new_id[:8]}... (old SUPERSEDED,"
      " frozen) -> bounded STOP")
sessions.close()
db.close()

# 2. kill-and-resume ----------------------------------------------------------------------
db2, registry2, audit2, sched2, sessions2, hands2, obs2, runner2, \
    router2, _ = rig(DB, LOG, ROOT / "var" / "slice11-profile-b")
from lakra.control.recovery import boot  # noqa: E402
report = boot(db2, registry2, Approvals(registry2, store=db2), sched2, audit2)
print(f"   boot: owners_released={report['owners_released']}")
registry2.load_all()
# Simulate the pre-crash state on a FRESH list task: navigate step recorded
# done (as if it completed pre-crash), then the process "dies".
t2 = start(registry2, sched2, "List the resumed courses", priority=3)
hints2 = {"url": FIXTURE, "expect_text": "pending"}
plan2 = Planner().plan(t2, dict(hints2))
plan_store.save_plan(db2, plan2, hints2)
plan_store.set_step_outcome(db2, plan2.plan_id, 0, "done")
# The observer side needs its page: navigate through the router once, then
# share it (the same two-object share the controller itself relies on).
from lakra.control.policy import Action  # noqa: E402
router2.route(t2.task_id, Action(
    kind="browser.navigate", target=FIXTURE, effect="reversible",
    task_id=t2.task_id))
hands2.attach(obs2.current_page)
res = runner2.resume(plan2.plan_id,
                     lambda: hands2.page.locator("body").inner_text())
assert (res.status, res.steps_done) == ("DONE", 2), res
_, _, outcomes = plan_store.load_plan(db2, plan2.plan_id)
assert outcomes == ["done", "done"], outcomes  # step 0 pre-crash, 1 resumed
assert plan_store.plans_for_task(db2, t2.task_id) == [plan2.plan_id]  # no replan needed
print("2. kill-and-resume: fresh observation first, step 1 executed to DONE")
print("   outcomes on disk:", outcomes)
sessions2.close()
db2.close()

print("REPLAY OK")
