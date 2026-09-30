"""Slice-09 loop, slice-11 replan + resume + persistence, slice-20 guards.

run(plan, hints): per step, guards.check first (slice-20: steps/time +
tokens + resource pressure -> continue / STOPPED / PAUSED with GUARD_TRIP
audit), then controller.run_step through the normal policy
path; CONTINUE advances (outcome persisted); ESCALATE maps to STOPPED /
ASK_PENDING, or ONE genuine replan: fresh observation + refreshed hints
(originals + achieved rationales) -> brand-new plan persisted with
supersedes set, old plan frozen SUPERSEDED, continue at the new plan's
step 0. Second failure STOPS.

resume(plan_id, observe): post-restart continuation. Loads the persisted
plan, REQUIRES a fresh observation before anything moves (persisted
progress is not proof of world state), then continues from the first step
without a recorded outcome. Refuses when observe is missing.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import plan_store
from .planner import Plan, Planner


@dataclass
class RunResult:
    plan_id: str
    status: str  # DONE | STOPPED | ASK_PENDING | PAUSED
    steps_done: int
    detail: str = ""


class Runner:
    def __init__(self, controller, scheduler, audit, planner=None,
                 observe=None, store=None, proposer=None, guards=None,
                 complete_task: bool = True) -> None:
        self.controller = controller
        self.scheduler = scheduler
        self.audit = audit
        self.planner = planner or Planner()
        self.observe = observe  # callable[[], str] | None: fresh snapshot
        self.store = store  # Database or None: plan persistence
        self.proposer = proposer  # ProposingPlanner or None (slice-12)
        self.guards = guards  # Guards or None (slice-20)
        # Slice-29A: repeat drivers run body plans whose DONE must NOT
        # complete the task (the loop owns the task lifecycle). False
        # leaves task mapping to the caller; True preserves legacy.
        self.complete_task = complete_task

    def run(self, plan: Plan, from_index: int = 0,
            hints: dict | None = None, _depth: int = 0) -> RunResult:
        task_id = plan.task_id
        plan.status = "EXECUTING"
        if self.store is not None and from_index == 0:
            try:
                plan_store.save_plan(self.store, plan, hints or {})
            except Exception:
                pass  # re-run of an already-persisted plan: keep history
        self.audit.log("PLAN_CREATED", task_id,
                       {"plan_id": plan.plan_id, "steps": len(plan.steps),
                        "from_index": from_index,
                        "rationales": [s.rationale for s in plan.steps]})
        i = from_index
        replanned = False
        last_seen = ""
        while i < len(plan.steps):
            if self.guards is not None:
                verdict = self.guards.check(task_id)
                if verdict.action == "stop":
                    self.audit.log("GUARD_TRIP", task_id,
                                   {"plan_id": plan.plan_id, "at_step": i,
                                    "action": "stop",
                                    "reason": verdict.reason,
                                    "detail": verdict.detail,
                                    "meters": verdict.meters})
                    return self._finish(plan, "STOPPED", i, verdict.detail)
                if verdict.action == "pause":
                    self.audit.log("GUARD_TRIP", task_id,
                                   {"plan_id": plan.plan_id, "at_step": i,
                                    "action": "pause",
                                    "reason": verdict.reason,
                                    "detail": verdict.detail,
                                    "meters": verdict.meters})
                    return self._finish_paused(plan, i, verdict.detail,
                                               verdict.meters)
            elif not self._budget_ok(task_id):
                return self._finish(plan, "STOPPED", i, "budget exhausted")
            step = plan.steps[i]
            try:
                res = self.controller.run_step(
                    task_id, step.to_browser_step())
            except Exception as exc:
                return self._finish(plan, "STOPPED", i, f"controller: {exc}")
            if res.outcome == "CONTINUE":
                self._record(plan.plan_id, i, "done")
                i += 1
                continue
            self._record(plan.plan_id, i, "failed")
            if res.detail == "blocked":
                return self._finish(plan, "STOPPED", i, "policy blocked")
            if res.detail == "ask-pending":
                return self._finish(plan, "ASK_PENDING", i,
                                    "waiting on human approval")
            if not replanned and _depth == 0:
                replanned = True
                new_plan, seen = self._replan(task_id, plan, hints or {}, i)
                last_seen = seen
                if new_plan is not None:
                    return self.run(new_plan, from_index=0,
                                    hints=self._last_hints, _depth=_depth + 1)
            return self._finish(plan, "STOPPED", i,
                                f"escalated: {res.detail}", last_seen)
        return self._finish(plan, "DONE", i, "all steps verified")
        return self._finish(plan, "DONE", i, "all steps verified")

    def _replan(self, task_id: str, plan: Plan, hints: dict, at: int):
        """Genuine replan: fresh state + refreshed hints -> NEW persisted
        plan; the old one freezes SUPERSEDED (immutable history). Returns
        (new_plan_or_None, snapshot_text_seen). Model-derived plans
        (plan_id "-m") replan through the proposer when one is attached;
        everything else uses templates. One bound, shared across paths."""
        if self.observe is None:
            self.audit.log("REPLAN", task_id,
                           {"plan_id": plan.plan_id, "at_step": at,
                            "result": "no-observe-callable"})
            return None, ""
        try:
            seen = self.observe()
        except Exception as exc:
            self.audit.log("REPLAN", task_id,
                           {"plan_id": plan.plan_id, "at_step": at,
                            "result": f"re-observe failed: {exc}"})
            return None, ""
        achieved = [s.rationale for s in plan.steps[:at]]
        refreshed = dict(hints)
        refreshed["achieved"] = achieved
        new_plan = None
        if (self.proposer is not None and plan.plan_id.endswith("-m")
                and at < len(plan.steps)):
            failed_step = plan.steps[at]
            model_plan, attempt = self.proposer.replan(
                self.controller.router.registry.get(task_id), seen,
                refreshed,
                {"kind": failed_step.action.kind,
                 "effect": failed_step.action.effect},
                parent=None)
            self.audit.log("MODEL_REPLAN", task_id,
                           {"plan_id": plan.plan_id,
                            "attempt": attempt.attempt_id,
                            "parsed": attempt.parsed,
                            "verdict": attempt.verdict,
                            "fallback_used": attempt.fallback_used})
            new_plan = model_plan
        if new_plan is None:
            from .planner import UnknownGoalError
            try:
                task = self.controller.router.registry.get(task_id)
                new_plan = self.planner.plan(task, refreshed)
            except UnknownGoalError as exc:
                self.audit.log("REPLAN", task_id,
                               {"plan_id": plan.plan_id, "at_step": at,
                                "result": f"refused: {exc}"})
                return None, seen
        self._last_hints = refreshed
        if self.store is not None:
            try:
                plan_store.save_plan(self.store, new_plan, refreshed)
                plan_store.set_plan_status(self.store, plan.plan_id,
                                           "SUPERSEDED")
            except Exception:
                pass
        plan.status = "SUPERSEDED"
        self.audit.log("PLAN_SUPERSEDED", task_id,
                       {"old_plan": plan.plan_id, "new_plan": new_plan.plan_id,
                        "at_step": at, "snapshot_chars": len(seen)})
        return new_plan, seen

    def resume(self, plan_id: str, observe) -> RunResult:
        """Post-restart continuation. observe is MANDATORY: persisted
        progress never substitutes for a fresh look at the world."""
        if self.store is None:
            raise ValueError("resume needs a store-backed runner")
        if observe is None:
            raise ValueError("resume needs a fresh observation callable")
        plan, hints, outcomes = plan_store.load_plan(self.store, plan_id)
        if plan.status in ("DONE", "STOPPED", "SUPERSEDED"):
            return RunResult(plan_id, plan.status, len(plan.steps),
                             "plan already terminal")
        try:
            seen = observe()
        except Exception as exc:
            return RunResult(plan_id, "STOPPED", 0,
                             f"resume refused: no fresh state ({exc})")
        self.audit.log("PLAN_RESUMED", plan.task_id,
                       {"plan_id": plan_id, "snapshot_chars": len(seen)})
        # First step not recorded done. A recorded "failed" outcome on a
        # live plan marks the gate where the run parked (every other
        # failed path terminates the plan, so only ASK_PENDING leaves a
        # live plan behind): resume re-drives it instead of skipping a
        # gate that never executed.
        first_open = next((n for n, o in enumerate(outcomes)
                           if o != "done"), len(plan.steps))
        self.observe = observe
        # Persisted hints travel into the resumed run so a later replan has
        # the same context the original run had (clarification: context yes,
        # world state no — that comes only from observe()).
        return self.run(plan, from_index=first_open, hints=hints)

    def _record(self, plan_id: str, idx: int, outcome: str) -> None:
        if self.store is not None:
            try:
                plan_store.set_step_outcome(self.store, plan_id, idx,
                                            outcome)
            except Exception:
                pass

    def _budget_ok(self, task_id: str) -> bool:
        try:
            task = self.controller.router.registry.get(task_id)
        except Exception:
            return False
        if self.scheduler.steps_used(task_id) >= task.budget.max_steps:
            return False
        return self.scheduler.time_ok(task)

    def _finish_paused(self, plan: Plan, i: int, detail: str,
                       meters: dict | None = None) -> RunResult:
        """Slice-20: resource-pressure pause. Plan freezes PAUSED (resumable,
        non-terminal in plan_store), task moves RUNNING -> PAUSED via the
        scheduler so Scheduler.resume() + Runner.resume() continue after
        relief. Lifecycle.sync is deliberately skipped: PAUSED is ours, not
        a terminal plan outcome it maps."""
        plan.status = "PAUSED"
        if self.store is not None:
            try:
                plan_store.set_plan_status(self.store, plan.plan_id,
                                           plan.status)
            except Exception:
                pass  # e.g. resuming an already-terminal plan: history wins
        try:
            if self.scheduler is not None:
                try:
                    self.scheduler.pause(plan.task_id)
                except Exception:
                    from .tasks import Status
                    try:
                        self.controller.router.registry.set_status(
                            plan.task_id, Status.PAUSED)
                    except Exception:
                        pass
        except Exception:
            pass  # pause bookkeeping never fails a run; audit tells truth
        self.audit.log("PLAN_OUTCOME", plan.task_id,
                       {"plan_id": plan.plan_id, "status": "PAUSED",
                        "steps_done": i, "detail": detail})
        return RunResult(plan_id=plan.plan_id, status="PAUSED", steps_done=i,
                         detail=detail)

    def _finish(self, plan: Plan, status: str, i: int,
                detail: str, snapshot: str = "") -> RunResult:
        plan.status = status if status != "ASK_PENDING" else "EXECUTING"
        if self.store is not None:
            try:
                plan_store.set_plan_status(self.store, plan.plan_id,
                                           plan.status)
            except Exception:
                pass  # e.g. resuming an already-terminal plan: history wins
            if status == "STOPPED":
                try:
                    kind = "blocked" if detail == "policy blocked" else (
                        "budget" if detail == "budget exhausted" else (
                            "controller-error"
                            if detail.startswith("controller:") else
                            "recovery-exhausted"))
                    model = (self.proposer.provider.name
                             if self.proposer is not None
                             and plan.plan_id.endswith("-m") else None)
                    plan_store.record_failure(
                        self.store, plan.task_id, plan.plan_id, i, kind,
                        None, detail, snapshot, model)
                except Exception:
                    pass  # corpus is observational: never fail the run
        # Slice-14: terminal plan outcomes own their task's lifecycle —
        # unless a repeat driver holds it (slice-29A: intermediate body
        # DONE must not complete the task; the loop completes it at END).
        if status in ("DONE", "STOPPED") and self.complete_task:
            try:
                from . import lifecycle
                lifecycle.sync(self.controller.router.registry, self.audit,
                               plan.task_id, status, detail)
            except Exception:
                pass  # lifecycle never fails a run; it only maps state
        self.audit.log("PLAN_OUTCOME", plan.task_id,
                       {"plan_id": plan.plan_id, "status": status,
                        "steps_done": i, "detail": detail})
        return RunResult(plan_id=plan.plan_id, status=status, steps_done=i,
                         detail=detail)
