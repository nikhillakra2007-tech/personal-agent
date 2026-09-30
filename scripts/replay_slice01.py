"""Slice-01 T5 manual replay: L1 navigate -> ALLOW, L3 submit -> ASK,
double checkout fails, audit replay matches. Run from the project root:

    .\\.venv\\Scripts\\python.exe scripts\\replay_slice01.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.policy import (  # noqa: E402
    ALLOW,
    ASK,
    Action,
    Approval,
    evaluate,
)
from lakra.control.registry import CheckoutError, TaskRegistry  # noqa: E402
from lakra.control.tasks import ActionRecord, Status, Task  # noqa: E402

LOG_PATH = ROOT / "var" / "audit-slice01.jsonl"
if LOG_PATH.exists():
    LOG_PATH.unlink()  # fresh stream per run for readable manual inspection

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()

# --- Task A: safe docs search ------------------------------------------------
task_a = registry.add(Task.create(
    "Search the official docs for Playwright waits",
    allowed_tools=["browser"],
    allowed_domains=["playwright.dev"],
    allowed_paths=["C:/LakraWorkspace"],
))
audit.log("TASK_CREATED", task_a.task_id, {"goal": task_a.goal})
registry.checkout(task_a.task_id, "exec-a")
registry.set_status(task_a.task_id, Status.RUNNING)

nav = Action(kind="browser.navigate", target="https://playwright.dev/docs/waiting",
             effect="reversible", task_id=task_a.task_id)
verdict_a = evaluate(nav, task_a)
assert verdict_a == ALLOW, verdict_a
task_a.record(ActionRecord(action_kind=nav.kind, verdict=verdict_a,
                           observation="docs page reachable",
                           verification="VERIFIED"))
audit.log("ACTION_ALLOWED", task_a.task_id,
          {"kind": nav.kind, "target": nav.target})

# --- Task B: submission hits the L3 gate -------------------------------------
task_b = registry.add(Task.create(
    "Submit the pending course assignment",
    allowed_tools=["browser"],
    allowed_domains=["portal.example.edu"],
    allowed_paths=["C:/LakraWorkspace"],
))
audit.log("TASK_CREATED", task_b.task_id, {"goal": task_b.goal})
registry.checkout(task_b.task_id, "exec-b")
registry.set_status(task_b.task_id, Status.RUNNING)

submit = Action(kind="browser.submit",
                target="https://portal.example.edu/assign/42",
                effect="consequential", task_id=task_b.task_id)
verdict_b = evaluate(submit, task_b)
assert verdict_b == ASK, verdict_b
approval = Approval.request(submit)
registry.set_status(task_b.task_id, Status.WAITING_APPROVAL)
task_b.record(ActionRecord(action_kind=submit.kind, verdict=verdict_b,
                           observation="paused for user decision"))
audit.log("ACTION_REQUIRES_APPROVAL", task_b.task_id,
          {"kind": submit.kind, "approval_id": approval.approval_id})

# --- Double checkout must fail ------------------------------------------------
try:
    registry.checkout(task_a.task_id, "exec-intruder")
    raise SystemExit("FAIL: second checkout succeeded")
except CheckoutError as exc:
    audit.log("CHECKOUT_DENIED", task_a.task_id, {"reason": str(exc)})
    print(f"double checkout correctly denied: {exc}")

# --- Replay must match ---------------------------------------------------------
events = audit.replay()
types = [e["type"] for e in events]
assert types == ["TASK_CREATED", "ACTION_ALLOWED", "TASK_CREATED",
                 "ACTION_REQUIRES_APPROVAL", "CHECKOUT_DENIED"], types

print(f"navigate verdict : {verdict_a} (expected ALLOW)")
print(f"submit verdict   : {verdict_b} (expected ASK)")
print(f"approval id      : {approval.approval_id} (undecided, task WAITING_APPROVAL)")
print(f"audit events     : {len(events)} -> {LOG_PATH}")
print("REPLAY OK")
