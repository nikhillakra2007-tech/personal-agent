"""Slice-02 demo: priority, pause/resume/cancel, lock contention, approval
round-trip (approve + deny), audit trail. Run from the project root:

    .\\.venv\\Scripts\\python.exe scripts\\replay_slice02.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.locks import ComputerLock, LockBusyError  # noqa: E402
from lakra.control.policy import Action, evaluate  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Needs, Scheduler  # noqa: E402
from lakra.control.tasks import ActionRecord, Status, Task  # noqa: E402

LOG_PATH = ROOT / "var" / "audit-slice02.jsonl"
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
sched = Scheduler(registry)
lock = ComputerLock()
approvals = Approvals(registry)


def new_task(goal, priority, domain="portal.example.edu"):
    t = registry.add(Task.create(
        goal, priority=priority, allowed_tools=["browser"],
        allowed_domains=[domain], allowed_paths=["C:/LakraWorkspace"]))
    audit.log("TASK_CREATED", t.task_id, {"goal": goal, "priority": priority})
    return t


# --- 1. priority: critical served before low, FIFO on ties --------------------
low1 = new_task("background docs read", 0, "docs.example.com")
crit = new_task("find pending courses", 3)
low2 = new_task("second background read", 0, "docs.example.com")
for t in (low1, crit, low2):
    registry.checkout(t.task_id, "exec")
    registry.set_status(t.task_id, Status.RUNNING)
    sched.enqueue(t.task_id)
order = [sched.next_turn().task.goal for _ in range(3)]
assert order[0] == "find pending courses", order
assert order[1:] == ["background docs read", "second background read"], order
for t in (low1, crit, low2):
    sched.release_turn(t.task_id)
print(f"1. priority order OK: {order}")
audit.log("TASK_ADMITTED", crit.task_id, {"reason": "priority-3-first"})

# --- 2. pause skips, resume restores -------------------------------------------
sched.pause(crit.task_id)
audit.log("TASK_PAUSED", crit.task_id, {})
assert sched.next_turn().task.task_id != crit.task_id
sched.release_turn(low1.task_id)
sched.resume(crit.task_id)
audit.log("TASK_RESUMED", crit.task_id, {})
assert sched.next_turn().task.task_id == crit.task_id
sched.release_turn(crit.task_id)
print("2. pause/resume OK (paused task skipped, resumed task served)")

# --- 3. cancel drops ------------------------------------------------------------
sched.cancel(low2.task_id)
audit.log("TASK_CANCELLED", low2.task_id, {})
assert registry.get(low2.task_id).status == Status.CANCELLED
print("3. cancel OK")

# --- 4. computer-lock contention -------------------------------------------------
lock.acquire("exec")
audit.log("COMPUTER_LOCK_ACQUIRED", crit.task_id, {"owner": "exec"})
try:
    lock.acquire("exec-intruder")
    raise SystemExit("FAIL: second lock acquisition succeeded")
except LockBusyError as exc:
    audit.log("COMPUTER_LOCK_DENIED", crit.task_id, {"reason": str(exc)})
    print(f"4. lock contention OK: {exc}")
lock.release("exec")
audit.log("COMPUTER_LOCK_RELEASED", crit.task_id, {"owner": "exec"})
lock.acquire("exec-2")
lock.release("exec-2")
print("   lock release + re-acquire OK")

# --- 5. approval: approve path with mandatory recheck ---------------------------
submit = Action(kind="browser.submit",
                target="https://portal.example.edu/assign/7",
                effect="consequential", task_id=crit.task_id)
assert evaluate(submit, crit) == "ASK"
apv = approvals.request(crit.task_id, submit)
audit.log("APPROVAL_REQUESTED", crit.task_id,
          {"approval_id": apv.approval_id, "kind": submit.kind})
served_while_parked = []
while True:
    turn = sched.next_turn()
    if turn is None:
        break
    served_while_parked.append(turn.task.task_id)
    sched.release_turn(turn.task.task_id)
    if len(served_while_parked) > 10:  # safety bound; queue is tiny
        break
assert crit.task_id not in served_while_parked  # parked task gets no turns
decided = approvals.decide(apv.approval_id, True)  # user says yes
verdict = approvals.recheck(decided)
approvals.must_be_gated(verdict)
audit.log("APPROVAL_GRANTED", crit.task_id,
          {"approval_id": apv.approval_id, "recheck": verdict})
crit.record(ActionRecord(action_kind=submit.kind, verdict="ASK+approved",
                         verification="VERIFIED"))
print(f"5. approval GRANT path OK (recheck={verdict})")

# --- 6. approval: deny path ends the task ----------------------------------------
submit2 = Action(kind="browser.submit",
                 target="https://portal.example.edu/assign/8",
                 effect="consequential", task_id=crit.task_id)
apv2 = approvals.request(crit.task_id, submit2)
audit.log("APPROVAL_REQUESTED", crit.task_id,
          {"approval_id": apv2.approval_id, "kind": submit2.kind})
approvals.decide(apv2.approval_id, False)  # user says no
audit.log("APPROVAL_DENIED", crit.task_id,
          {"approval_id": apv2.approval_id})
assert registry.get(crit.task_id).status == Status.CANCELLED
print("6. approval DENY path OK (task CANCELLED, no retry)")

# --- 7. replay integrity ----------------------------------------------------------
events = audit.replay()
types = [e["type"] for e in events]
for needed in ("TASK_ADMITTED", "TASK_PAUSED", "TASK_RESUMED",
               "TASK_CANCELLED", "COMPUTER_LOCK_ACQUIRED",
               "COMPUTER_LOCK_DENIED", "COMPUTER_LOCK_RELEASED",
               "APPROVAL_REQUESTED", "APPROVAL_GRANTED", "APPROVAL_DENIED"):
    assert needed in types, f"missing {needed}"
print(f"7. audit replay OK: {len(events)} events -> {LOG_PATH}")
print("REPLAY OK")
