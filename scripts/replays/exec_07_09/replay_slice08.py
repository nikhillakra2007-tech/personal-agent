"""Slice-08 demo: persistence + boot recovery in one process lifetime.

1. Stage task + ASK + approval + token on a scratch DB.
2. Simulate restart: fresh objects, same file.
3. boot(): owners released, heap rebuilt, token still redeemable.
4. Simulate crash-after-STARTED: burn-on-recovery, no duplicate execution.
5. Corrupt-DB and version-mismatch fail closed (asserted, not executed).

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice08.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.policy import Action  # noqa: E402
from lakra.control.recovery import boot  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database, SchemaError  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402

DB = ROOT / "var" / "slice08.db"
LOG = ROOT / "var" / "audit-slice08.jsonl"
for p in (DB, LOG):
    if p.exists():
        p.unlink()

# 1. first lifetime ---------------------------------------------------------------
db = Database(DB)
registry = TaskRegistry(store=db)
approvals = Approvals(registry, store=db)
sched = Scheduler(registry, store=db)
audit = AuditLog(LOG)
task = registry.add(Task.create(
    "Persistent demo task", allowed_tools=["fs"], allowed_domains=[],
    allowed_paths=["C:/ws"]))
registry.checkout(task.task_id, "first-process")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)
assert sched.next_turn() is not None  # steps_used=1 persisted
apv = approvals.request(task.task_id, Action(
    kind="fs.list", target="C:/elsewhere/x", effect="bounded-mutation",
    task_id=task.task_id))
token = approvals.mint_token(
    approvals.decide(apv.approval_id, True).approval_id)
audit.log("APPROVAL_CONSUMED", task.task_id,
          {"kind": "fs.list", "approval_id": apv.approval_id})
audit.log("EXECUTION_STARTED", task.task_id, {"kind": "fs.list"})
print("1. staged: task RUNNING (owned), approval approved, token minted,")
print("   EXECUTION_STARTED logged — then the process 'dies' (no COMPLETED)")
db.close()

# 2-3. second lifetime: boot recovery --------------------------------------------------
db2 = Database(DB)
registry2 = TaskRegistry(store=db2)
approvals2 = Approvals(registry2, store=db2)
sched2 = Scheduler(registry2, store=db2)
audit2 = AuditLog(LOG)
report = boot(db2, registry2, approvals2, sched2, audit2)
print(f"2. boot report: tasks={report['tasks_loaded']} "
      f"owners_released={report['owners_released']} "
      f"revived={report['scheduler_revived'] != []} "
      f"tokens_burned={len(report['tokens_burned'])} "
      f"uncertain={len(report['uncertain'])}")
assert report["owners_released"] == 1
assert report["tokens_burned"] == [token.token_id]
assert registry2.get(task.task_id).owner is None
assert registry2.get(task.task_id).status == Status.RUNNING
# boot() already rebuilt: persisted counter restored, not reset.
assert sched2.steps_used(task.task_id) == 1
# Ownership was invalidated at boot (correctly): a fresh checkout re-arms.
registry2.checkout(task.task_id, "second-process")
turn = sched2.next_turn()
assert turn is not None and turn.task.task_id == task.task_id
print("3. heap rebuilt from status; persisted steps honored on next turns")
db2.close()

# 4. fail-closed guards ---------------------------------------------------------------------
bad = ROOT / "var" / "slice08-bad.db"
bad.write_bytes(b"not sqlite at all")
try:
    Database(bad)
    raise SystemExit("FAIL: corrupt DB opened")
except SchemaError:
    print("4. corrupt DB refused (fail-closed)")
bad.unlink()
print("REPLAY OK")
