"""Slice-03 router, extended slice-07 with one-shot approval tokens.

Single choke point for execution: resolve -> policy -> budget/admission ->
execute -> history + audit. Returns ExecuteResult in every case (ok=False
for denied/parked/refused paths). Never executes on BLOCK or unapproved ASK.
An ASK verdict executes ONLY with a valid approval_token bound to the
identical action (docs/contracts/approval-token.md); the token burns iff
the authorized execution returns ok=True.
"""

from __future__ import annotations

from .registry import ExecuteResult, ToolRegistry, UnknownToolError
from ..control.approvals import ApprovalError, Approvals
from ..control.attempts import AttemptLedger, DuplicateAttempt
from ..control.audit import AuditLog
from ..control.policy import ALLOW, ASK, BLOCK, Action, evaluate
from ..control.registry import TaskRegistry
from ..control.scheduler import Scheduler
from ..control.tasks import ActionRecord


class RouterError(RuntimeError):
    """Ownerless task, over-budget task, or inadmissible task."""


class ToolRouter:
    def __init__(self, registry: TaskRegistry, tools: ToolRegistry,
                 approvals: Approvals, audit: AuditLog,
                 scheduler: Scheduler | None = None,
                 ledger: AttemptLedger | None = None) -> None:
        self.registry = registry
        self.tools = tools
        self.approvals = approvals
        self.audit = audit
        self.scheduler = scheduler
        self.ledger = ledger or AttemptLedger()

    def route(self, task_id: str, action: Action,
                approval_token: str | None = None,
                pre_counted: bool = False,
                parent_attempt_id: str | None = None,
                plan_id: str | None = None) -> ExecuteResult:
        """pre_counted=True asserts the caller already holds a freshly
        granted scheduler turn for this step (e.g. BrowserController):
        the step budget was consumed at grant time, so the router skips
        its steps re-check (which would otherwise see the just-counted
        turn as exhaustion). Time and admissibility gates still apply.
        Direct callers leave it False: one route() call spends one step
        from an uncounted budget, enforced here."""
        task = self.registry.get(task_id)
        if task.owner is None:
            raise RouterError(f"task {task_id} has no owner; checkout first")

        try:
            executor = self.tools.resolve(action.kind)
        except UnknownToolError as exc:
            self.audit.log("EXECUTION_FAILED", task_id,
                           {"kind": action.kind, "reason": str(exc)})
            raise
        self.audit.log("TOOL_SELECTED", task_id, {"kind": action.kind})

        verdict = evaluate(action, task)
        if verdict == BLOCK:
            self.audit.log("ACTION_DENIED", task_id,
                           {"kind": action.kind, "target": action.target})
            return ExecuteResult(ok=False, error=f"BLOCKED: {action.kind}")

        token = None
        if verdict == ASK:
            if approval_token is None:
                approval = self.approvals.request(task_id, action)
                self.audit.log("ACTION_REQUIRES_APPROVAL", task_id,
                               {"kind": action.kind,
                                "approval_id": approval.approval_id})
                return ExecuteResult(
                    ok=False,
                    error=f"PARKED: approval {approval.approval_id} required")
            try:
                token = self.approvals.redeem(approval_token, action)
            except ApprovalError as exc:
                self.audit.log("APPROVAL_REJECTED", task_id,
                               {"kind": action.kind, "reason": str(exc)})
                return ExecuteResult(ok=False,
                                     error=f"TOKEN REJECTED: {exc}")
            self.audit.log("APPROVAL_CONSUMED", task_id,
                           {"kind": action.kind,
                            "approval_id": token.approval_id})

        # ALLOW path: budget + admission gates before touching the machine.
        # (Steps are skipped only when the caller proves a counted turn.)
        if self.scheduler is not None:
            if ((not pre_counted
                 and self.scheduler.steps_used(task_id)
                 >= task.budget.max_steps)
                    or not self.scheduler.time_ok(task)):
                self.audit.log("EXECUTION_FAILED", task_id,
                               {"kind": action.kind, "reason": "budget exhausted"})
                raise RouterError(f"task {task_id} budget exhausted")
            if not self.scheduler.is_admissible(task_id):
                self.audit.log("EXECUTION_FAILED", task_id,
                               {"kind": action.kind,
                                "reason": "not admissible"})
                raise RouterError(f"task {task_id} not admissible")

        self.audit.log("ACTION_ALLOWED", task_id,
                       {"kind": action.kind, "target": action.target})
        try:
            attempt_id = self.ledger.begin(
                task_id, action.kind, action.target, action.effect,
                plan_id=plan_id, parent_attempt_id=parent_attempt_id)
        except DuplicateAttempt as exc:
            self.audit.log("DUPLICATE_SUPPRESSED", task_id,
                           {"kind": action.kind, "reason": str(exc)})
            return ExecuteResult(ok=False, error=f"DUPLICATE: {exc}")
        self.audit.log("EXECUTION_STARTED", task_id,
                       {"kind": action.kind, "attempt_id": attempt_id})
        result = executor.execute(action, task)  # type: ignore[union-attr]
        result.attempt_id = attempt_id
        self.ledger.close(attempt_id, result.ok)
        if token is not None and result.ok:
            self.approvals.burn(token.token_id)  # single success only
        task.record(ActionRecord(
            action_kind=action.kind,
            verdict="ASK+approved" if token is not None else ALLOW,
            observation=result.output if result.ok else (result.error or ""),
            verification="UNVERIFIED",  # router records stay UNVERIFIED;
            # task-level verification is the slice-05 verifier's job
        ))
        self.audit.log(
            "EXECUTION_COMPLETED" if result.ok else "EXECUTION_FAILED",
            task_id,
            {"kind": action.kind, "error": result.error})
        return result
