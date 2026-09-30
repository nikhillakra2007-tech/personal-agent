"""Slice-13 thin CLI: one supervised action, end to end.

Builds the real stack (registry/scheduler/router + fs/terminal executors),
routes a single caller-specified action through policy, and on ASK resolves
it through a decider:
  --yes / --no : scripted answer (tests, demos)
  --poll SECS  : cross-process decider (waits for approve.py, fail-closed)
  (no flag)    : interactive y/n prompt — the ONLY TTY touchpoint in the
                 whole slice; loop.py and the router never see stdin.

On approval the identical action executes once via the one-shot token path.
Multi-step browser loops stay in replay/test harnesses (they need live
pages, which a CLI process cannot share meaningfully yet).

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\lakra_run.py
        --kind fs.list --target C:/path --effect read
        [--yes | --no | --poll SECS]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import ApprovalError, Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.loop import (  # noqa: E402
    PollingDecider,
    ScriptedDecider,
)
from lakra.control.policy import Action  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.filesystem import FileSystemExecutor  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.execution.terminal import ShellExecutor  # noqa: E402

args = sys.argv[1:]


def flag(name, default=None):
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else default
    return default


kind = flag("--kind", "fs.list")
target = flag("--target", ".")
effect = flag("--effect", "read")

db = Database()
registry = TaskRegistry(store=db)
audit = AuditLog(ROOT / "var" / "audit-run.jsonl")
sched = Scheduler(registry, store=db)
tools = ToolRegistry()
ws = ROOT / "var" / "run-ws"
ws.mkdir(parents=True, exist_ok=True)
for name in ("fs.read", "fs.write", "fs.list", "fs.delete"):
    tools.register(name, FileSystemExecutor(ws))
tools.register("terminal.run", ShellExecutor(ws))
approvals = Approvals(registry, store=db)
router = ToolRouter(registry, tools, approvals, audit, sched)

task = registry.add(Task.create(
    f"CLI run: {kind} {target}", allowed_tools=["fs", "terminal"],
    allowed_domains=[], allowed_paths=[str(ws)]))
registry.checkout(task.task_id, "cli")
registry.set_status(task.task_id, Status.RUNNING)

action = Action(kind=kind, target=target, effect=effect,
                task_id=task.task_id)
res = router.route(task.task_id, action)
if res.ok or not (res.error or "").startswith("PARKED"):
    print(f"verdict: {'ALLOW' if res.ok else 'DENIED/BLOCKED'}")
    print(f"output: {res.output or res.error or '(empty)'}")
    raise SystemExit(0 if res.ok else 1)

approval_id = audit.replay()[-1]["payload"]["approval_id"]
item = next(a for a in approvals.pending()
            if a["approval_id"] == approval_id)
print(f"ASK parked: {item['kind']} {item['target']} [{item['effect']}]")
if "--yes" in args:
    decider = ScriptedDecider(True)
elif "--no" in args:
    decider = ScriptedDecider(False)
elif "--poll" in args:
    decider = PollingDecider(approvals, task.task_id,
                             timeout_s=float(flag("--poll")))
else:
    try:
        answer = input("approve once? [y/n] ").strip().lower()
    except EOFError:
        answer = "n"
    decider = ScriptedDecider(answer == "y")

verdict = decider.decide(item)
# Cross-process boundary (slice-37): adopt an externally settled
# approval instead of re-deciding into the CAS. Unchanged local path
# when still pending.
try:
    settled = approvals.adopt(item["approval_id"])
except ApprovalError:
    settled = None
if settled is None:
    if not verdict:
        approvals.decide(approval_id, False)
        print("denied; task cancelled")
        raise SystemExit(2)
    approvals.decide(approval_id, True)
else:
    try:
        registry.refresh(task.task_id)
    except Exception as exc:
        print(f"refused: cannot refresh task state ({exc})")
        raise SystemExit(1)
    if settled.decided != "approved":
        print("denied; task cancelled")
        raise SystemExit(2)

token = approvals.mint_token(approval_id)
res = router.route(task.task_id, action, approval_token=token.token_id)
print(f"approved once; executed ok={res.ok}")
print(f"output: {res.output or res.error or '(empty)'}")
raise SystemExit(0 if res.ok else 3)
