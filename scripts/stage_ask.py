"""Slice-08 two-terminal manual-test stager (Terminal A).

Creates a task on the SHARED database and drives it into ASK, then exits —
leaving the pending approval for approve.py (Terminal B) in another process.
Usage:
    set LAKRA_DB=<shared file>
    .\\.venv\\Scripts\\python.exe scripts\\stage_ask.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.policy import Action  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.filesystem import FileSystemExecutor  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

db = Database()
registry = TaskRegistry(store=db)
approvals = Approvals(registry, store=db)
tools = ToolRegistry()
tools.register("fs.list", FileSystemExecutor(ROOT / "var" / "stage-ws"))
router = ToolRouter(registry, tools, approvals,
                    AuditLog(ROOT / "var" / "audit-stage.jsonl"),
                    Scheduler(registry))

task = registry.add(Task.create(
    "Cross-process approval demo", allowed_tools=["fs"], allowed_domains=[],
    allowed_paths=[str(ROOT / "var" / "stage-ws")]))
registry.checkout(task.task_id, "terminal-a")
registry.set_status(task.task_id, Status.RUNNING)
res = router.route(task.task_id, Action(
    kind="fs.list", target="C:/outside/scope", effect="bounded-mutation",
    task_id=task.task_id))
assert not res.ok and "PARKED" in (res.error or ""), res
print(f"STAGED task={task.task_id} status=WAITING_APPROVAL")
print("Terminal B: run scripts/approve.py against the same LAKRA_DB")
