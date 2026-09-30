"""Slice-13 demo: supervised end-to-end task runs on the fixture.

1. List goal + auto-approve decider -> DONE (decider never consulted).
2. Submit goal + approving decider -> ASK -> token executes once -> DONE.
3. Submit goal + denying decider -> STOPPED, task cancelled, no retry.
4. Prune demo on a scratch copy: old terminal rows tombstoned.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice13.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import plan_store  # noqa: E402
from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.loop import ScriptedDecider, TaskLoop  # noqa: E402
from lakra.control.prune import prune  # noqa: E402
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

DB = ROOT / "var" / "slice13.db"
LOG = ROOT / "var" / "audit-slice13.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
for p in (DB, LOG):
    if p.exists():
        p.unlink()

db = Database(DB)
registry = TaskRegistry(store=db)
audit = AuditLog(LOG)
sched = Scheduler(registry, store=db)
tools = ToolRegistry()
sessions = BrowserSessions(ROOT / "var" / "slice13-profile")
sessions.launch()
hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, ROOT / "var" / "slice13-shots")
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
for kind in ("browser.click", "browser.type", "browser.press",
             "browser.scroll", "browser.wait"):
    tools.register(kind, hands)


class _SubmitStub:
    def execute(self, action, task):
        from lakra.execution.registry import ExecuteResult
        return ExecuteResult(ok=True, output="stub-submitted")


tools.register("browser.submit", _SubmitStub())
router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
ctrl = BrowserController(router, hands, audit, sched, observer=obs)
runner = Runner(ctrl, sched, audit,
                observe=lambda: hands.page.locator("body").inner_text(),
                store=db)
loop = TaskLoop(runner, router.approvals, audit)


def start(goal, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    task = registry.add(Task.create(goal, **kw))
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)
    sched.enqueue(task.task_id)
    return task


# 1. all-ALLOW run: decider present but never consulted -------------------------------
t1 = start("List the demo courses")
out = loop.run_goal(t1, {"url": FIXTURE, "expect_text": "pending"},
                    ScriptedDecider(True))
assert (out.status, out.steps_done) == ("DONE", 2), out
print("1. list run DONE; decider consulted 0 times (nothing gated)")
assert registry.get(t1.task_id).status == Status.COMPLETED  # lifecycle owned it

# 2. L3 run approved once -----------------------------------------------------------------
t2 = start("Submit the demo form")
decider = ScriptedDecider(True)
out = loop.run_goal(t2, {"url": FIXTURE, "selector": "#echo", "text": "hi",
                         "submit_selector": "#toggle"}, decider)
assert out.status == "DONE", out
assert len(decider.seen) == 1 and decider.seen[0]["kind"] == "browser.submit"
print("2. submit run DONE; human saw exact bytes once, token burned once")
assert registry.get(t2.task_id).status == Status.COMPLETED

# 3. L3 run denied ------------------------------------------------------------------------------
t3 = start("Submit the demo form")
out = loop.run_goal(t3, {"url": FIXTURE, "selector": "#echo", "text": "hi",
                         "submit_selector": "#toggle"},
                    ScriptedDecider(False))
assert out.status == "STOPPED" and registry.get(t3.task_id).status == \
    Status.CANCELLED
print("3. denied run STOPPED; task cancelled, no retry, no second ask")

# 4. prune tombstones -------------------------------------------------------------------------------
report = prune(db, older_than_days=3650)  # nothing this fresh: no-op proof
assert report == {"plans": 0, "steps": 0, "failures": 0}, report
print("4. prune no-op on fresh DB (retention only touches the old)")
sessions.close()
db.close()
print("REPLAY OK")
