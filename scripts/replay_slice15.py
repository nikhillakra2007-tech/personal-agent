"""Slice-15 demo: raw goals -> derived hints -> plan -> DONE.

Three natural phrasings of the fixture goal must all derive hints equal to
the hand-fed ones from slice-09 demos; one runs end to end. An unparseable
goal refuses. No model calls anywhere.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice15.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.analyzer import analyze  # noqa: E402
from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.planner import Planner, UnknownGoalError  # noqa: E402
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

DB = ROOT / "var" / "slice15.db"
LOG = ROOT / "var" / "audit-slice15.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
for p in (DB, LOG):
    if p.exists():
        p.unlink()

GOALS = [
    f"List my pending courses at {FIXTURE}",
    f"Show me the pending courses on {FIXTURE}",
    f"List pending courses at {FIXTURE}",
]
for goal in GOALS:
    hints = analyze(goal)
    assert hints["url"] == FIXTURE, hints
    assert hints["expect_text"] == "pending", hints
    print(f"derived: {hints['intent']} url=ok expect_text='pending'")
print("1. three phrasings -> identical hand-fed-equivalent hints")

try:
    analyze("Um")
    raise SystemExit("FAIL: unparseable goal accepted")
except UnknownGoalError as exc:
    print(f"2. unparseable goal refused: {exc}")

db = Database(DB)
registry = TaskRegistry(store=db)
audit = AuditLog(LOG)
sched = Scheduler(registry, store=db)
tools = ToolRegistry()
sessions = BrowserSessions(ROOT / "var" / "slice15-profile")
sessions.launch()
hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, ROOT / "var" / "slice15-shots")
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
ctrl = BrowserController(router, hands, audit, sched, observer=obs)
runner = Runner(ctrl, sched, audit,
                observe=lambda: hands.page.locator("body").inner_text(),
                store=db)
task = registry.add(Task.create(
    GOALS[0], allowed_tools=["browser"], allowed_domains=["file:"],
    allowed_paths=[str(ROOT)]))
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)
hints = {k: v for k, v in analyze(GOALS[0]).items() if k != "intent"}
plan = Planner().plan(task, hints)
res = runner.run(plan, hints=hints)
assert (res.status, res.steps_done) == ("DONE", 2), res
print("3. derived hints -> planned -> executed DONE (no hand-feeding)")
sessions.close()
db.close()
print("REPLAY OK")
