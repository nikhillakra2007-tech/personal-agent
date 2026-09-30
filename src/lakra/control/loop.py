"""Slice-13: supervised end-to-end task loop (TTY-free).

run_goal() walks goal -> plan -> execute -> verify -> DONE, pausing for a
human exactly where policy demands. Approval decisions arrive through a
Decider interface — never stdin, never a TTY — so tests inject scripted
deciders, the CLI injects y/n prompts, and cross-process flows inject a
poller. The loop adds no authority: every step still routes through
policy, tokens still mint/burn per slice-07, verification still decides
success. Approved L3 steps are human-verified by construction (the approver
saw the exact action bytes); the loop records that, it does not re-verify.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from . import plan_store
from .planner import Planner


class Decider(ABC):
    """Human (or scripted stand-in) behind an ASK gate."""

    @abstractmethod
    def decide(self, item: dict) -> bool:
        """True = approve once, False = deny (fail-closed default)."""
        raise NotImplementedError


class ScriptedDecider(Decider):
    """Fixed answer for tests and --yes/--no flows."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.seen: list[dict] = []

    def decide(self, item: dict) -> bool:
        self.seen.append(item)
        return self.answer


class PollingDecider(Decider):
    """Cross-process decider: waits until the item leaves pending (someone
    decided externally, e.g. approve.py), then reports the outcome. Timeout
    or disappearance -> False (deny-safe default). Read-only itself."""

    def __init__(self, approvals, task_id: str, timeout_s: float = 300,
                 interval_s: float = 2) -> None:
        self.approvals = approvals
        self.task_id = task_id
        self.timeout_s = timeout_s
        self.interval_s = interval_s

    def decide(self, item: dict) -> bool:
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            pending_ids = {p["approval_id"]
                           for p in self.approvals.pending()}
            if item["approval_id"] not in pending_ids:
                try:
                    task = self.approvals.registry.get(self.task_id)
                    from .tasks import Status
                    return task.status == Status.RUNNING
                except Exception:
                    return False
            time.sleep(self.interval_s)
        return False


class TaskLoop:
    """Owns nothing but orchestration; every effect goes through runner."""

    def __init__(self, runner, approvals, audit, planner=None) -> None:
        self.runner = runner
        self.approvals = approvals
        self.audit = audit
        self.planner = planner or Planner()

    def run_goal(self, task, hints: dict, decider: Decider):
        """Full supervised run. Returns the terminal RunResult."""
        from .runner import RunResult
        from .planner import UnknownGoalError
        try:
            plan = self.planner.plan(task, hints)
        except UnknownGoalError as exc:
            self.audit.log("PLAN_OUTCOME", task.task_id,
                           {"plan_id": "-", "status": "STOPPED",
                            "steps_done": 0,
                            "detail": f"cannot plan: {exc}"})
            return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                             detail=f"cannot plan: {exc}")
        return self.run_plan(plan, hints, decider)

    def run_plan(self, plan, hints: dict, decider: Decider):
        """Supervised run of an ALREADY-BUILT plan (slice-29A seam).

        Same ASK/decide/mint/token/execute loop as run_goal, minus
        planning — so repeat drivers (loops) reuse the exact human-gate
        path instead of reimplementing it. PAUSED/STOPPED/DONE results
        pass through untouched for the caller to map."""
        from .runner import RunResult
        try:
            task = self.runner.controller.router.registry.get(plan.task_id)
        except Exception as exc:
            self.audit.log("PLAN_OUTCOME", plan.task_id,
                           {"plan_id": plan.plan_id, "status": "STOPPED",
                            "steps_done": 0,
                            "detail": f"unknown task: {exc}"})
            return RunResult(plan_id=plan.plan_id, status="STOPPED",
                             steps_done=0, detail=f"unknown task: {exc}")
        result = self.runner.run(plan, hints=hints)
        return self._drain_ask(task, plan, hints, result, decider)

    def resume_plan(self, plan_id: str, observe, decider: Decider):
        """Supervised resume of a persisted plan (slice-38).

        Runner.resume() continues from the first open step after a
        mandatory fresh observation; ASK gates met along the way drain
        through the identical adopt/decide/mint/execute loop as
        run_plan (slice-37 adoption included), so resumed L3 gates
        always collect fresh consent. Non-ASK outcomes pass through
        already lifecycle-mapped by the runner.
        """
        from .runner import RunResult
        try:
            result = self.runner.resume(plan_id, observe)
        except Exception as exc:
            self.audit.log("PLAN_OUTCOME", "-",
                           {"plan_id": plan_id, "status": "STOPPED",
                            "steps_done": 0,
                            "detail": f"cannot resume ({exc})"})
            return RunResult(plan_id=plan_id, status="STOPPED",
                             steps_done=0,
                             detail=f"cannot resume ({exc})")
        if result.status != "ASK_PENDING":
            return result
        from . import plan_store
        try:
            plan, hints, _ = plan_store.load_plan(self.runner.store,
                                                  plan_id)
            task = self.runner.controller.router.registry.get(
                plan.task_id)
        except Exception as exc:
            self.audit.log("PLAN_OUTCOME", plan_id,
                           {"plan_id": plan_id, "status": "STOPPED",
                            "steps_done": 0,
                            "detail": f"cannot resume ({exc})"})
            return RunResult(plan_id=plan_id, status="STOPPED",
                             steps_done=0,
                             detail=f"cannot resume ({exc})")
        return self._drain_ask(task, plan, hints, result, decider)

    def _drain_ask(self, task, plan, hints, result, decider):
        """Shared ASK-gate drain for run_plan and resume_plan: the
        entry results differ (fresh run vs. resumed run) but every gate
        from here decides, mints, executes, and continues identically.
        """
        while result.status == "ASK_PENDING":
            item = self._pending_for(task.task_id)
            if item is None:
                return self._stop(plan, result.steps_done,
                                  "approval vanished before decision")
            verdict = decider.decide(item)
            # Cross-process boundary (slice-37): the approval may have
            # been settled by someone else (approve.py) while deciding.
            # Shared truth wins over the stale verdict — adopt it
            # instead of re-deciding into the CAS.
            from .approvals import ApprovalError
            try:
                settled = self.approvals.adopt(item["approval_id"])
            except ApprovalError:
                settled = None
            if settled is None:
                # Unchanged local path: first decider wins via CAS.
                if not verdict:
                    self.approvals.decide(item["approval_id"], False)
                    return self._stop(plan, result.steps_done,
                                      "human denied approval")
                self.approvals.decide(item["approval_id"], True)
            else:
                # Adopted settlement: resync the task so redeem()'s
                # RUNNING re-check reads shared truth, not a stale
                # pre-park cache. Denial maps to the identical clean
                # deny outcome (the external decider already applied
                # CANCELLED/release; never re-applied here).
                try:
                    self._resync(task.task_id)
                except Exception as exc:
                    return self._stop(plan, result.steps_done,
                                      f"cannot refresh task state ({exc})")
                if settled.decided != "approved":
                    return self._stop(plan, result.steps_done,
                                      "human denied approval")
            token = self.approvals.mint_token(item["approval_id"])
            res = self._execute_approved(task, item, token)
            if not res["ok"]:
                return self._stop(plan, result.steps_done,
                                  f"approved step failed: {res['error']}")
            self._record_done(plan, result.steps_done)
            result = self.runner.run(plan, from_index=result.steps_done + 1,
                                     hints=hints)
        return result

    def _pending_for(self, task_id: str) -> dict | None:
        for item in self.approvals.pending():
            if item["task_id"] == task_id:
                return item
        return None

    def _resync(self, task_id: str) -> None:
        """Refresh the task from shared truth (slice-37 adopt path).

        Same registry seam run_plan already uses; failures propagate
        to the caller, which maps them to a shaped STOPPED.
        """
        self.runner.controller.router.registry.refresh(task_id)

    def _execute_approved(self, task, item: dict, token) -> dict:
        """Route the identical bound action with the one-shot token. The
        human saw these exact bytes; their approval IS the verification."""
        from .policy import Action
        action = Action(kind=item["kind"], target=item["target"],
                        effect=item["effect"], task_id=item["task_id"])
        res = self.runner.controller.router.route(
            task.task_id, action, approval_token=token.token_id)
        self.audit.log("APPROVED_STEP_EXECUTED", task.task_id,
                       {"kind": action.kind, "ok": res.ok,
                        "approval_id": item["approval_id"]})
        return {"ok": res.ok, "error": res.error}

    def _record_done(self, plan, idx: int) -> None:
        store = getattr(self.runner, "store", None)
        if store is not None:
            try:
                plan_store.set_step_outcome(store, plan.plan_id, idx, "done")
            except Exception:
                pass

    def _stop(self, plan, steps_done: int, detail: str):
        from .runner import RunResult
        plan.status = "STOPPED"
        store = getattr(self.runner, "store", None)
        if store is not None:
            try:
                plan_store.set_plan_status(store, plan.plan_id, "STOPPED")
                plan_store.record_failure(
                    store, plan.task_id, plan.plan_id, steps_done,
                    "loop-stop", None, detail, "", None)
            except Exception:
                pass  # corpus is observational: never fail the run
        self.audit.log("PLAN_OUTCOME", plan.task_id,
                       {"plan_id": plan.plan_id, "status": "STOPPED",
                        "steps_done": steps_done, "detail": detail})
        return RunResult(plan_id=plan.plan_id, status="STOPPED",
                         steps_done=steps_done, detail=detail)
