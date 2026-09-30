"""Slice-09 demo: plan first (human reads it), then execute.

1. Planner builds a 2-step list plan; rationales print BEFORE any browser
   traffic. Runner executes to DONE.
2. Unknown goal refused (no hallucinated steps).
3. Fill-submit plan is created ending AT the L3 gate (not executed here).

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice09.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.planner import Planner, UnknownGoalError  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

PROFILE = ROOT / "var" / "slice09-profile"
SHOTS = ROOT / "var" / "slice09-shots"
LOG_PATH = ROOT / "var" / "audit-slice09.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
sched = Scheduler(registry)
tools = ToolRegistry()
sessions = BrowserSessions(PROFILE)
sessions.launch()
hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, SHOTS)
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
for kind in ("browser.click", "browser.type", "browser.press",
             "browser.scroll", "browser.wait"):
    tools.register(kind, hands)
router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
ctrl = BrowserController(router, hands, audit, sched, observer=obs)
runner = Runner(ctrl, sched, audit,
                observe=lambda: hands.page.locator("body").inner_text())

task = registry.add(Task.create(
    "List the demo portal courses", allowed_tools=["browser"],
    allowed_domains=["file:"], allowed_paths=[str(ROOT)]))
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)

# 1. plan, print FIRST, then run --------------------------------------------------
plan = Planner().plan(task, {"url": FIXTURE, "expect_text": "pending"})
print("PLAN (read before execution):")
for n, step in enumerate(plan.steps, 1):
    print(f"  {n}. {step.action.kind} {step.action.target}"
          f"  expect {step.expect.kind}={step.expect.target!r}")
    print(f"     why: {step.rationale}")
res = runner.run(plan)
assert (res.status, res.steps_done) == ("DONE", 2), res
print(f"1. plan executed to DONE ({res.steps_done} steps verified)")

# 2. unknown goal refused --------------------------------------------------------------
t2 = registry.add(Task.create(
    "Transcribe this meeting", allowed_tools=["browser"],
    allowed_domains=["file:"], allowed_paths=[]))
try:
    Planner().plan(t2, {})
    raise SystemExit("FAIL: unknown goal planned")
except UnknownGoalError as exc:
    print(f"2. unknown goal refused (escalate to ASK): {exc}")

# 3. L3 plan shape -----------------------------------------------------------------------
t3 = registry.add(Task.create(
    "Submit the demo form", allowed_tools=["browser"],
    allowed_domains=["file:"], allowed_paths=[]))
plan3 = Planner().plan(t3, {"url": FIXTURE, "selector": "#echo",
                            "text": "hi", "submit_selector": "#toggle"})
assert plan3.steps[-1].action.kind == "browser.submit"
print("3. fill-submit plan ends AT the gate (created, not executed here)")
sessions.close()

events = audit.replay()
for needed in ("PLAN_CREATED", "PLAN_OUTCOME"):
    assert any(e["type"] == needed for e in events), needed
print(f"4. audit replay OK: {len(events)} events -> {LOG_PATH}")
print("REPLAY OK")
