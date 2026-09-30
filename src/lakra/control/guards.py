"""Slice-20: automatic guardrail enforcement (meters -> run verdict).

Guards is the single pre-step check owned by the run loop. It fuses three
dimensions that previously lived apart:

  steps/time  (scheduler books) ............ -> STOP "budget exhausted"
  token spend (TokenLedger.remaining <= 0) .. -> STOP "budget exhausted ..."
  machine pressure (ResourceMonitor) ........ -> PAUSE "paused: <reason>"

Semantics (approved slice-20 brief):

* STOP is terminal-for-the-plan (fail-closed; a human must raise the
  budget). The task itself stays resumable via lifecycle's stalled path.
* PAUSE is resumable: the plan freezes PAUSED, the task moves RUNNING ->
  PAUSED, and Runner.resume() continues after relief + scheduler resume.
* Unknown task -> STOP (never run blind).
* ANY guard-internal error (ledger provider raises, monitor raises, sample
  unreadable) -> PAUSE "guard-error: ...", never proceed, never crash.
  Pause is the safe default: it halts work but preserves resume.

Guards never touches the audit log itself (plan.md dependency rule: audit
is a sink the runner writes, nobody reads for control flow). The runner
logs GUARD_TRIP alongside PLAN_OUTCOME. A None scheduler/ledger/monitor
means that dimension is skipped, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuardVerdict:
    action: str  # "continue" | "pause" | "stop"
    reason: str = ""
    detail: str = ""
    meters: dict = field(default_factory=dict)

    @property
    def proceed(self) -> bool:
        return self.action == "continue"


class Guards:
    """Pre-step gate. scheduler: step/time books; ledger_for(task_id):
    TokenLedger | None; monitor: ResourceMonitor | stub with
    under_pressure() and optional sample(). All three optional."""

    def __init__(self, scheduler=None, ledger_for=None, monitor=None) -> None:
        self.scheduler = scheduler
        self.ledger_for = ledger_for
        self.monitor = monitor

    def check(self, task_id: str) -> GuardVerdict:
        # 1. Step/time books (legacy Runner._budget_ok logic, centralized).
        if self.scheduler is not None:
            try:
                task = self.scheduler.registry.get(task_id)
            except Exception as exc:
                return GuardVerdict(
                    action="stop", reason="unknown-task",
                    detail=f"unknown task: {exc}")
            try:
                if (self.scheduler.steps_used(task_id)
                        >= task.budget.max_steps):
                    return GuardVerdict(
                        action="stop", reason="budget-exhausted",
                        detail="budget exhausted",
                        meters=self._meters(task_id))
                if not self.scheduler.time_ok(task):
                    return GuardVerdict(
                        action="stop", reason="budget-exhausted",
                        detail="budget exhausted",
                        meters=self._meters(task_id))
            except Exception as exc:
                return GuardVerdict(
                    action="pause", reason="guard-error",
                    detail=f"paused: guard-error: {exc}",
                    meters=self._meters(task_id))
        # 2. Token spend.
        if self.ledger_for is not None:
            try:
                ledger = self.ledger_for(task_id)
            except Exception as exc:
                return GuardVerdict(
                    action="pause", reason="guard-error",
                    detail=f"paused: guard-error: {exc}",
                    meters=self._meters(task_id))
            try:
                if ledger is not None and ledger.remaining <= 0:
                    return GuardVerdict(
                        action="stop", reason="budget-exhausted",
                        detail=(f"budget exhausted (tokens "
                                f"{ledger.used_tokens}/"
                                f"{ledger.limit_tokens})"),
                        meters=self._meters(task_id))
            except Exception as exc:
                return GuardVerdict(
                    action="pause", reason="guard-error",
                    detail=f"paused: guard-error: {exc}",
                    meters=self._meters(task_id))
        # 3. Machine pressure.
        if self.monitor is not None:
            try:
                pressed, reason = self.monitor.under_pressure()
            except Exception as exc:
                return GuardVerdict(
                    action="pause", reason="guard-error",
                    detail=f"paused: guard-error: {exc}",
                    meters=self._meters(task_id))
            if pressed:
                return GuardVerdict(
                    action="pause", reason="resource-pressure",
                    detail=f"paused: {reason}",
                    meters=self._meters(task_id))
        return GuardVerdict(action="continue", reason="ok",
                            detail="guards pass",
                            meters=self._meters(task_id))

    def _meters(self, task_id: str) -> dict:
        meters: dict = {}
        if self.scheduler is not None:
            try:
                task = self.scheduler.registry.get(task_id)
                meters["steps_used"] = self.scheduler.steps_used(task_id)
                meters["max_steps"] = task.budget.max_steps
            except Exception:
                pass
        if self.ledger_for is not None:
            try:
                ledger = self.ledger_for(task_id)
                if ledger is not None:
                    meters["tokens_used"] = ledger.used_tokens
                    meters["tokens_limit"] = ledger.limit_tokens
            except Exception:
                pass
        if self.monitor is not None:
            try:
                snap = self.monitor.sample()
                meters["ram_available_mb"] = snap.ram_available_mb
                meters["cpu_percent"] = snap.cpu_percent
                meters["gpu_available"] = snap.gpu_available
                if snap.gpu_available:
                    meters["vram_free_mb"] = snap.vram_free_mb
            except Exception:
                pass
        return meters
