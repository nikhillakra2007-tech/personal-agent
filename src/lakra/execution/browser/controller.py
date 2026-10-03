"""Slice-05: browser controller — the orchestration seam for interaction.

run_step() executes ONE BrowserStep through the full loop:

  turn (scheduler) -> route (policy inside) -> execute -> verify
    -> CONTINUE | RETRY (bounded) -> RECOVERY (one alternate) -> ESCALATE

The controller is deterministic and contains no reasoning: a future
planner/model will CALL run_step(), never live inside it. ESCALATE leaves
the task untouched so the caller maps it to ASK / REPLAN / STOP.

Execution failure and verification failure are distinguished end to end
(result vs predicate, EXECUTION_FAILED vs VERIFICATION FAIL, separate
recovery alternates).
"""

from __future__ import annotations

from dataclasses import dataclass

from .recovery import recover_execution, recover_verification
from .verification import Predicate, check
from ..registry import ExecuteResult
from ...control.policy import Action


@dataclass
class BrowserStep:
    action: Action
    expect: Predicate
    max_retries: int = 2


@dataclass
class StepResult:
    ok: bool  # action executed without executor error
    verified: bool  # predicate passed on fresh state
    attempts: int  # total execution attempts
    outcome: str  # CONTINUE | ESCALATE
    detail: str = ""


def _selector_of(action: Action) -> str:
    if "\n---\n" in action.target:
        return action.target.split("\n---\n", 1)[0]
    if action.target.startswith("into-view:"):
        return action.target[len("into-view:"):]
    if action.target.startswith("selector:"):
        return action.target[len("selector:"):]
    if ":" in action.target and action.kind in ("browser.scroll",
                                                "browser.wait"):
        return ""
    return action.target


class BrowserController:
    def __init__(self, router, actions, audit, scheduler=None,
                 observer=None, snapshots=None,
                 transfer_root=None) -> None:
        self.router = router
        self.actions = actions
        self.audit = audit
        self.scheduler = scheduler
        # V2-05: sandbox root for file_nonempty predicates (download
        # proof). None keeps every file predicate fail-closed.
        self.transfer_root = transfer_root
        # Optional observer share: after a navigate step succeeds through
        # the observer, attach its live page so subsequent act/verify steps
        # see the same page. (Slice-05 direction: the controller wraps both.)
        self.observer = observer
        from .snapshots import SnapshotStore
        self.snapshots = snapshots or SnapshotStore()

    def _admit(self, task_id: str) -> str | None:
        """Consume one scheduler turn for this step. Returns an ESCALATE
        reason, or None when the turn was granted to this task."""
        if self.scheduler is None:
            return None
        turn = self.scheduler.next_turn()
        if turn is None:
            return "not-admitted"
        if turn.task.task_id != task_id:
            self.scheduler.release_turn(turn.task.task_id)
            return "scheduler-yielded-another-task"
        return None

    def _finish_turn(self, task_id: str) -> None:
        if self.scheduler is not None:
            try:
                self.scheduler.release_turn(task_id)
            except Exception:
                pass

    def run_step(self, task_id: str, step: BrowserStep) -> StepResult:
        denied = self._admit(task_id)
        if denied:
            return StepResult(False, False, 0, "ESCALATE", denied)
        try:
            return self._run(task_id, step)
        finally:
            self._finish_turn(task_id)

    def _attempt(self, task_id: str, action: Action,
                 parent: str | None = None) -> ExecuteResult:
        """Route one attempt. ALWAYS returns a result: policy/router
        refusals arrive as ok=False with a classified error prefix
        (BLOCKED / PARKED / router-refused) and must never be retried.
        pre_counted=True: run_step() holds a freshly granted scheduler
        turn, so the router must not re-count it (see route()). parent
        threads attempt lineage for retries/recovery (slice-17)."""
        try:
            return self.router.route(task_id, action, pre_counted=True,
                                     parent_attempt_id=parent)
        except Exception as exc:
            self.audit.log("EXECUTION_FAILED", task_id,
                           {"kind": action.kind,
                            "reason": f"router refused: {exc}"})
            return ExecuteResult(ok=False, error=f"router-refused: {exc}")

    @staticmethod
    def _refusal(res: ExecuteResult) -> str | None:
        """Classify a non-ok result: policy refusal reason, or None when the
        failure is transient (executor error) and retryable."""
        for prefix, label in (("BLOCKED", "blocked"),
                              ("PARKED", "ask-pending"),
                              ("router-refused", "router-refused")):
            if (res.error or "").startswith(prefix):
                return label
        return None

    def _share_page(self, step) -> None:
        """After a navigate, point the acting side at the observer's page."""
        if (self.observer is not None
                and step.action.kind == "browser.navigate"):
            page = self.observer.current_page
            if page is not None and hasattr(self.actions, "attach"):
                self.actions.attach(page)

    def _capture(self, task_id: str, step) -> None:
        """Record pre-action state for diff steps. Silently skips when no
        page is open (the verify then takes the explicit NO_BASELINE path)."""
        from .snapshots import SnapshotRecord
        from .verification import DIFF_PREDICATES
        if step.expect.kind not in DIFF_PREDICATES:
            return
        try:
            page = self.actions.page
            counts = {}
            if step.expect.kind.startswith("count_"):
                counts[step.expect.target] = page.locator(
                    step.expect.target).count()
            self.snapshots.record(task_id, SnapshotRecord(
                url=page.url,
                text=page.locator("body").inner_text() or ""))
            # NOTE: counts recorded post-capture below keep one code path;
            # pre-state counts for count-kinds are folded in here.
            if counts:
                self.snapshots.latest(task_id).counts.update(counts)
        except Exception:
            pass

    def _verify(self, task_id: str, step: BrowserStep,
                previous=None) -> bool:
        from .verification import MissingBaselineError
        try:
            passed = check(step.expect, self.actions.page, previous,
                           transfer_root=self.transfer_root)
        except MissingBaselineError:
            self.audit.log("VERIFICATION", task_id,
                           {"kind": step.expect.kind,
                            "target": step.expect.target,
                            "result": "NO_BASELINE"})
            return False
        self.audit.log("VERIFICATION", task_id,
                       {"kind": step.expect.kind,
                        "target": step.expect.target,
                        "result": "PASS" if passed else "FAIL"})
        return passed

    def _run(self, task_id: str, step: BrowserStep) -> StepResult:
        attempts = 0
        last_kind = "verify-fail"  # or "exec-fail"; drives recovery choice
        # Diff steps capture their own baseline first; every verify below
        # compares against it. No baseline -> explicit NO_BASELINE audit.
        self._capture(task_id, step)
        previous = self.snapshots.latest(task_id)

        res = self._attempt(task_id, step.action, parent=None)
        attempts += 1
        parent_id = res.attempt_id or None
        refusal = None if res.ok else self._refusal(res)
        if refusal:
            return StepResult(False, False, attempts, "ESCALATE", refusal)
        self._share_page(step)
        if res.ok and self._verify(task_id, step, previous):
            return StepResult(True, True, attempts, "CONTINUE", "verified")
        last_kind = "exec-fail" if res.ok is False else "verify-fail"

        # Bounded retries of the identical action (policy refusals excluded
        # above: an ASK must never spawn duplicate approvals via retry).
        for _ in range(max(0, step.max_retries)):
            self.audit.log("RETRY", task_id,
                           {"kind": step.action.kind, "attempt": attempts + 1})
            res = self._attempt(task_id, step.action, parent=parent_id)
            attempts += 1
            parent_id = res.attempt_id or parent_id
            refusal = None if res.ok else self._refusal(res)
            if refusal:
                return StepResult(False, False, attempts, "ESCALATE", refusal)
            if res.ok and self._verify(task_id, step, previous):
                return StepResult(True, True, attempts, "CONTINUE",
                                  "verified after retry")
            last_kind = "exec-fail" if res.ok is False else "verify-fail"

        # One deterministic recovery alternate, then exactly one last chance.
        selector = _selector_of(step.action)
        if last_kind == "exec-fail":
            recovered = recover_execution(self.actions.page, selector)
            self.audit.log("RECOVERY", task_id,
                           {"strategy": "requery-scroll-into-view",
                            "prepared": recovered})
            if recovered:
                res = self._attempt(task_id, step.action, parent=parent_id)
                attempts += 1
                parent_id = res.attempt_id or parent_id
                if res.ok and self._verify(task_id, step, previous):
                    return StepResult(True, True, attempts, "CONTINUE",
                                      "recovered via requery-scroll")
        else:
            recover_verification(self.actions.page)
            self.audit.log("RECOVERY", task_id,
                           {"strategy": "settle-then-reverify"})
            if self._verify(task_id, step, previous):
                return StepResult(True, True, attempts, "CONTINUE",
                                  "recovered after settle")

        return StepResult(False, False, attempts, "ESCALATE",
                          "bounded recovery exhausted")
