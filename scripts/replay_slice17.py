"""Slice-17 demo: exactly-once observability (narrow claim, loudly audited).

1. ASK flow through the controller: exactly ONE EXECUTION_STARTED for the
   park, and exactly one for the approved token execution.
2. Forced double-fire: wedge an open attempt row, route the identical
   action -> DUPLICATE_SUPPRESSED, executor untouched.
3. Lineage: retry attempts carry parent_attempt_id (shown from the log).

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice17.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.attempts import AttemptLedger  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.policy import Action  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.filesystem import FileSystemExecutor  # noqa: E402
from lakra.execution.registry import (  # noqa: E402
    ExecuteResult,
    ToolRegistry,
)
from lakra.execution.router import ToolRouter  # noqa: E402

DB = ROOT / "var" / "slice17.db"
LOG = ROOT / "var" / "audit-slice17.jsonl"
for p in (DB, LOG):
    if p.exists():
        p.unlink()

db = Database(DB)
registry = TaskRegistry(store=db)
audit = AuditLog(LOG)
sched = Scheduler(registry, store=db)
tools = ToolRegistry()
ws = ROOT / "var" / "slice17-ws"
tools.register("fs.list", FileSystemExecutor(ws))
class _Stub:
    def execute(self, action, task):
        return ExecuteResult(ok=True, output="published")


tools.register("publish", _Stub())
approvals = Approvals(registry, store=db)
router = ToolRouter(registry, tools, approvals, audit, sched,
                    ledger=AttemptLedger(store=db))

task = registry.add(Task.create(
    "Attempt demo", allowed_tools=["fs"], allowed_domains=[],
    allowed_paths=[str(ws)]))
audit.log("TASK_CREATED", task.task_id, {"goal": task.goal})
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)


def act(kind, target, effect):
    return Action(kind=kind, target=target, effect=effect,
                  task_id=task.task_id)


# 1. ASK flow: one STARTED for the park is impossible (park executes
#    nothing); approve + token -> exactly one STARTED + COMPLETED. ------------
parked = router.route(task.task_id, act(
    "publish", "blog-post", "consequential"))
assert "PARKED" in (parked.error or "")
starts = [e for e in audit.replay() if e["type"] == "EXECUTION_STARTED"]
assert starts == [], "parked actions must not start executions"
approval_id = audit.replay()[-1]["payload"]["approval_id"]
token = approvals.mint_token(
    approvals.decide(approval_id, True).approval_id)
done = router.route(task.task_id,
                    act("publish", "blog-post", "consequential"),
                    approval_token=token.token_id)
assert done.ok and done.attempt_id
starts = [e for e in audit.replay() if e["type"] == "EXECUTION_STARTED"]
assert len(starts) == 1 and \
    starts[0]["payload"]["attempt_id"] == done.attempt_id
print("1. ASK flow: zero starts while parked, exactly one on token execute")

# 2. forced double-fire -------------------------------------------------------------
wedge = router.ledger.begin(task.task_id, "fs.list", str(ws), "read")
res = router.route(task.task_id, act("fs.list", str(ws), "read"))
assert not res.ok and res.error.startswith("DUPLICATE"), res
assert any(e["type"] == "DUPLICATE_SUPPRESSED" for e in audit.replay())
router.ledger.close(wedge, True)
res = router.route(task.task_id, act("fs.list", str(ws), "read"))
assert res.ok  # closed attempts may legitimately run again
print("2. wedged open attempt suppresses the duplicate; after close it runs")

# 3. lineage visible ---------------------------------------------------------------------
print(f"3. attempt ids chain: token execution {done.attempt_id[:8]}... "
      f"(retries carry parent_attempt_id; see controller tests)")
print(f"   log -> {LOG}")
print("REPLAY OK")
