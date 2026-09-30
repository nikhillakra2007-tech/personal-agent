"""Slice-06 demo: intelligence foundation WITHOUT a live model.

1. Ollama absent -> clean ProviderUnavailable (no hang, no traceback leak).
2. Real telemetry snapshot printed (compare with Task Manager/nvidia-smi).
3. Token ledger: charge, cap, exhaustion refuses before HTTP.
4. Proposal -> Action -> policy: L1 admitted + executed via router;
   L4 proposal parses (shape OK) but policy BLOCKs with zero execution.
5. Scheduler pressure gate flips on scripted starvation, recovers after.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice06.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.budgets import TokenLedger  # noqa: E402
from lakra.control.policy import evaluate  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Needs, Scheduler  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.filesystem import FileSystemExecutor  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.execution.terminal import ShellExecutor  # noqa: E402
from lakra.models.base import BudgetExhausted, ProviderUnavailable  # noqa: E402
from lakra.models.local import LocalProvider  # noqa: E402
from lakra.models.proposal import (  # noqa: E402
    ProposalRejected,
    parse_proposal,
    to_action,
)
from lakra.resources.monitor import ResourceMonitor  # noqa: E402

LOG_PATH = ROOT / "var" / "audit-slice06.jsonl"
if LOG_PATH.exists():
    LOG_PATH.unlink()
audit = AuditLog(LOG_PATH)

# 1. absent runtime fails cleanly ----------------------------------------------
provider = LocalProvider("tiny:1b")
print(f"1. ollama available: {provider.available()}")
try:
    provider.complete("hi", budget_tokens=8)
    print("   UNEXPECTED: a model answered (oleanna is installed?)")
except ProviderUnavailable as exc:
    print(f"   clean failure, no hang: {exc}")
    audit.log("PROVIDER_UNAVAILABLE", "-", {"provider": provider.name})

# 2. real telemetry --------------------------------------------------------------
snap = ResourceMonitor().sample(force=True)
print(f"2. RAM {snap.ram_available_mb}/{snap.ram_total_mb} MB free | "
      f"CPU {snap.cpu_percent}% | Lakra RSS {snap.lakra_rss_mb} MB")
if snap.gpu_available:
    print(f"   GPU {snap.gpu_util_percent}% | VRAM free {snap.vram_free_mb}/"
          f"{snap.vram_total_mb} MB | {snap.gpu_temp_c} C")
else:
    print("   GPU telemetry unavailable (degraded honestly, VRAM ungated)")
pressed, reason = ResourceMonitor().under_pressure()
print(f"   pressure: {pressed} ({reason})")

# 3. token ledger ------------------------------------------------------------------
ledger = TokenLedger(5)
print(f"3. grant cap: {ledger.precheck(100)} (asked 100, capped to 5)")
ledger.charge(5)
try:
    ledger.precheck(1)
    raise SystemExit("FAIL: exhausted ledger granted")
except BudgetExhausted as exc:
    print(f"   exhausted refuses pre-HTTP: {exc}")

# 4. proposal -> policy -> router ----------------------------------------------------
registry = TaskRegistry()
router = ToolRouter(registry, ToolRegistry(), Approvals(registry), audit,
                    Scheduler(registry))
ws = ROOT / "var" / "slice06-ws"
router.tools.register("fs.list", FileSystemExecutor(ws))
router.tools.register("terminal.run", ShellExecutor(ws))
task = registry.add(Task.create(
    "proposal demo", allowed_tools=["fs"], allowed_domains=[],
    allowed_paths=[str(ws)]))
audit.log("TASK_CREATED", task.task_id, {"goal": task.goal})
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)

good = parse_proposal('{"tool_kind": "fs.list", "target": "%s", '
                      '"effect": "read", "expect_kind": "text_contains", '
                      '"expect_target": "", "rationale": "list ws"}'
                      % str(ws).replace("\\", "\\\\"))
action, predicate = to_action(good, task.task_id)
print(f"4a. L1 proposal verdict: {evaluate(action, task)}")
res = router.route(task.task_id, action)
assert res.ok, res.error
print("    executed via router (policy-gated, history recorded)")

evil = parse_proposal('{"tool_kind": "terminal.run", '
                      '"target": "captcha.defeat", "effect": "blocked", '
                      '"expect_kind": "text_contains", "expect_target": "", '
                      '"rationale": "hostile model output"}')
b_action, _ = to_action(evil, task.task_id)
print(f"4b. hostile proposal verdict: {evaluate(b_action, task)}")
res = router.route(task.task_id, b_action)
assert not res.ok and "BLOCKED" in (res.error or "")
print("    hostile proposal BLOCKed with zero execution")

try:
    parse_proposal("just click it pls")
    raise SystemExit("FAIL: prose accepted as proposal")
except ProposalRejected as exc:
    print(f"4c. prose rejected at schema: {exc}")

# 5. pressure gate (scripted starvation) ----------------------------------------------
import time  # noqa: E402
from lakra.resources.monitor import SystemSnapshot  # noqa: E402


class ScriptedMonitor:
    def __init__(self, ram_avail):
        self.snap = SystemSnapshot(
            ts=time.time(), ram_total_mb=16384, ram_available_mb=ram_avail,
            cpu_percent=5.0, lakra_rss_mb=50.0, browser_rss_mb=0.0,
            gpu_available=False)

    def sample(self, force=False):
        return self.snap


r2 = TaskRegistry()
t2 = r2.add(Task.create("hungry", allowed_tools=["fs"],
                        allowed_domains=[], allowed_paths=[str(ws)]))
r2.checkout(t2.task_id, "exec")
r2.set_status(t2.task_id, Status.RUNNING)
from lakra.control.scheduler import Capacity  # noqa: E402
s = Scheduler(r2, Capacity(ram_mb_claimable=1 << 20),
              monitor=ScriptedMonitor(ram_avail=64))
s.enqueue(t2.task_id, Needs(ram_mb=4096))
assert s.next_turn() is None
s.monitor = ScriptedMonitor(ram_avail=1 << 20)
assert s.next_turn() is not None
print("5. pressure withholds turn; relief restores it (no task harmed)")
audit.log("RESOURCE_PRESSURE_DEMO", t2.task_id, {"denied_then": "admitted"})

print(f"   log -> {LOG_PATH} ({len(audit.replay())} events)")
print("REPLAY OK")
