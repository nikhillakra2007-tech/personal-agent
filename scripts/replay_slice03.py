"""Slice-03 demo: first real actuators through the choke point.

Covers: fs write/read/list (ALLOW), `..` escape refused at executor level,
pip refused by command allowlist, unregistered browser tool rejected (seam
proven, capability absent), ASK -> approve -> recheck -> re-route, BLOCK with
no execution. Run from the project root:

    .\\.venv\\Scripts\\python.exe scripts\\replay_slice03.py
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
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.filesystem import FileSystemExecutor  # noqa: E402
from lakra.execution.registry import (  # noqa: E402
    ToolRegistry,
    UnknownToolError,
)
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.execution.terminal import ShellExecutor  # noqa: E402

WS = ROOT / "var" / "slice03-ws"
LOG_PATH = ROOT / "var" / "audit-slice03.jsonl"
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
sched = Scheduler(registry)
approvals = Approvals(registry)
tools = ToolRegistry()
fs = FileSystemExecutor(WS)
tools.register("fs.read", fs)
tools.register("fs.write", fs)
tools.register("fs.list", fs)
tools.register("fs.delete", fs)
tools.register("terminal.run", ShellExecutor(WS))


class _NeverRuns:
    """Proves BLOCK/ASK paths never reach an executor."""

    def __init__(self):
        self.calls = 0

    def execute(self, action, task):
        self.calls += 1
        from lakra.execution.registry import ExecuteResult
        return ExecuteResult(ok=True, output="must never happen")


never = _NeverRuns()
tools.register("browser.click", never)  # seam placeholder, capability absent
tools.register("captcha.defeat", never)
audit.log("TOOL_REGISTERED", "-", {"tools": ["fs.*", "terminal.run"]})
router = ToolRouter(registry, tools, approvals, audit, sched)

task = registry.add(Task.create(
    "Prepare workspace notes",
    allowed_tools=["fs", "terminal"],
    allowed_domains=["x.example"],
    allowed_paths=[str(WS)]))
audit.log("TASK_CREATED", task.task_id, {"goal": task.goal})
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)


def act(kind, target, effect):
    return Action(kind=kind, target=target, effect=effect,
                  task_id=task.task_id)


# 1. fs write -> read round-trip through the router (ALLOW) ---------------------
r = router.route(task.task_id,
                 act("fs.write", f"{WS / 'notes.txt'}\n---\nhello lakra",
                     "bounded-mutation"))
assert r.ok, r.error
r = router.route(task.task_id,
                 act("fs.read", str(WS / "notes.txt"), "read"))
assert r.ok and r.output == "hello lakra", r
r = router.route(task.task_id, act("fs.list", str(WS), "read"))
assert r.ok and "notes.txt" in r.output, r
print("1. fs write/read/list OK")

# 2. `..` escape: policy string-prefix passes, executor realpath refuses ------
evil = f"{WS}/../escape.txt\n---\nx"
assert evil.startswith(str(WS))  # policy sees in-bounds prefix...
res = router.route(task.task_id, act("fs.write", evil, "bounded-mutation"))
assert not res.ok and "escapes sandbox" in res.error, res
assert not (ROOT / "var" / "escape.txt").exists()
print("2. sandbox escape refused at executor level (defense in depth)")

# 3. pip refused by the command allowlist (policy tool-prefix passed it) -------
res = router.route(task.task_id, act("terminal.run", "pip install requests",
                                     "bounded-mutation"))
assert not res.ok and "allowlist" in res.error, res
print("3. pip refused: executor allowlist catches what policy prefix misses")

# 4. real command executes -------------------------------------------------------
res = router.route(task.task_id, act(
    "terminal.run", f"{sys.executable} --version", "bounded-mutation"))
assert res.ok and "Python" in res.output, res
print(f"4. terminal.run OK ({res.output.strip()})")

# 5. unregistered browser tool: seam proven, capability absent -------------------
try:
    router.route(task.task_id, act(
        "browser.navigate", "https://x.example", "reversible"))
    raise SystemExit("FAIL: unregistered tool executed")
except UnknownToolError as exc:
    print(f"5. browser.navigate correctly rejected: {exc}")

# 6. ASK -> approve -> recheck -> re-route ----------------------------------------
a = act("fs.write", "C:/outside/scope.txt", "bounded-mutation")
res = router.route(task.task_id, a)
assert not res.ok and "PARKED" in res.error, res
approval_id = audit.replay()[-1]["payload"]["approval_id"]
decided = approvals.decide(approval_id, True)
assert approvals.recheck(decided) == "ASK"
approvals.must_be_gated("ASK")
print("6. ASK parked, approved, recheck confirms still-gated ASK")

# 7. BLOCK never executes ----------------------------------------------------------
n_before = len(task.history)
res = router.route(task.task_id,
                   act("captcha.defeat", "https://x.example", "read"))
assert not res.ok and "BLOCKED" in res.error
assert never.calls == 0 and len(task.history) == n_before
print("7. BLOCK path OK (denied, executor never invoked)")

events = audit.replay()
types = [e["type"] for e in events]
for needed in ("TOOL_REGISTERED", "TOOL_SELECTED", "ACTION_ALLOWED",
               "ACTION_DENIED", "ACTION_REQUIRES_APPROVAL",
               "EXECUTION_STARTED", "EXECUTION_COMPLETED", "EXECUTION_FAILED"):
    assert needed in types, f"missing {needed}"
print(f"8. audit replay OK: {len(events)} events -> {LOG_PATH}")
print("REPLAY OK")
