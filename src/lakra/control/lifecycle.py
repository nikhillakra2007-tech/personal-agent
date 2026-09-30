"""Slice-14: task lifecycle ownership (Candidate A).

One total mapping from plan terminal outcomes to task state. Pure function
of (task status, plan outcome, detail) plus application through the
registry; every application is audited. Rules:

  DONE (+ any detail)            -> task COMPLETED
  STOPPED "policy blocked"       -> task stays RUNNING + TASK_STALLED audit
  STOPPED budget-exhausted       -> task stays RUNNING + TASK_STALLED audit
                                     (scheduler grant-time check skips it)
  STOPPED controller-error       -> task FAILED
  STOPPED anything else          -> task stays RUNNING + TASK_STALLED audit
  ASK_PENDING / vanished / deny  -> no mapping (human flow owns the task;
                                     deny already CANCELLED via decide)

Cancel propagation: cancel_task() CANCELs the task and marks all its
non-terminal plans STOPPED(cancelled) so no orphaned plan outlives it.
"""

from __future__ import annotations

from .tasks import Status


def decide(task_status: Status, plan_status: str,
           detail: str) -> str | None:
    """Pure mapping. Returns the target task status, or None to leave it."""
    if plan_status == "DONE":
        return Status.COMPLETED.value
    if plan_status != "STOPPED":
        return None
    if detail.startswith("controller:"):
        return Status.FAILED.value
    return None  # blocked / budget / escalated: stalled, resumable


def sync(registry, audit, task_id: str, plan_status: str,
         detail: str) -> str:
    """Apply decide() through the registry. Returns what happened."""
    try:
        task = registry.get(task_id)
    except Exception as exc:
        return f"unknown task: {exc}"
    if task.status in (Status.COMPLETED, Status.FAILED, Status.CANCELLED):
        return "task already terminal; untouched"
    target = decide(task.status, plan_status, detail)
    if target is None:
        audit.log("TASK_STALLED", task_id,
                  {"plan_status": plan_status, "detail": detail})
        return "stalled (resumable)"
    try:
        registry.set_status(task_id, Status(target))
    except Exception as exc:
        audit.log("TASK_STALLED", task_id,
                  {"plan_status": plan_status, "detail": detail,
                   "transition_refused": str(exc)})
        return "transition refused; stalled"
    audit.log("TASK_LIFECYCLE", task_id,
              {"plan_status": plan_status, "task_status": target})
    return f"task -> {target}"


def cancel_task(registry, task_id: str, store=None, audit=None) -> str:
    """Cancel a task and freeze its live plans. Returns what happened."""
    from . import plan_store
    task = registry.get(task_id)
    if task.status == Status.CANCELLED:
        return "already cancelled"
    try:
        if task.owner:
            try:
                registry.release(task_id, task.owner)
            except Exception:
                pass
        registry.set_status(task_id, Status.CANCELLED)
    except Exception as exc:
        return f"cancel refused: {exc}"
    frozen = 0
    if store is not None:
        try:
            for pid in plan_store.plans_for_task(store, task_id):
                plan, _, _ = plan_store.load_plan(store, pid)
                if plan.status not in ("DONE", "STOPPED", "SUPERSEDED"):
                    plan_store.set_plan_status(store, pid, "STOPPED")
                    frozen += 1
        except Exception:
            pass
    if audit is not None:
        try:
            audit.log("TASK_CANCELLED", task_id, {"plans_frozen": frozen})
        except Exception:
            pass
    return f"cancelled, {frozen} plan(s) frozen"
