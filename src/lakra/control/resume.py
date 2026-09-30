"""Slice-38: resume a stranded task by id.

Discovery (resume_info) is a pure read over persisted rows: which road
owns the task, which plan/loop row to continue, and what blocks it
(pending approvals, uncertain executions). It writes nothing and
decides nothing. The driver (resume_task) enforces the fail-closed
preconditions, then resumes through the existing Runner.resume /
LoopRunner.resume machinery with supervised ASK handling:

* still-pending approval -> refuse naming the id (never duplicate-park
  a second approval beside it; decide via approve.py or let it expire,
  then resume — a settled approval is adopted on the way back in);
* UNCERTAIN consequential step (EXECUTION_STARTED without COMPLETED) ->
  refuse under the slice-08 crash rule: re-observe plus a FRESH
  approval, i.e. a fresh manual run, never a blind continue;
* resumed L3 gates always re-park for fresh consent, even when the
  pre-death approval was approved-but-unexecuted.

No new executors, predicates, templates, policy, tokens, migrations,
or audit event types. Terminal history stays immutable.
"""

from __future__ import annotations


class ResumeRefused(ValueError):
    """Nothing resumable, or resuming now would be unsafe. Fail-closed
    before anything launches, with an actionable detail."""


LOOP_LIVE = frozenset({"RUNNING", "PAUSED"})
PLAN_LIVE = frozenset({"EXECUTING", "DRAFT", "PAUSED"})


def _pending_approvals(db, task_id: str) -> list[str]:
    cur = db.execute("SELECT approval_id FROM approvals WHERE task_id=?"
                     " AND decided IS NULL ORDER BY requested_at",
                     (task_id,))
    return [row[0] for row in cur.fetchall()]


def _settled_approvals(db, task_id: str) -> list[str]:
    cur = db.execute("SELECT approval_id FROM approvals WHERE task_id=?"
                     " AND decided IS NOT NULL ORDER BY decided_at",
                     (task_id,))
    return [row[0] for row in cur.fetchall()]


def _subkind(hints: dict) -> str:
    if not isinstance(hints, dict):
        return "single"
    if hints.get("fields"):
        return "form"
    if hints.get("link_text"):
        return "follow"
    return "single"


def resume_info(db, task_id: str, audit_events: list) -> dict:
    """Describe what, if anything, can resume for a task.

    Pure read: tasks row, loops rows, plans rows, approvals rows, plus
    the caller-provided audit events for the UNCERTAIN scan. Raises
    ResumeRefused for unknown tasks, terminal tasks, ambiguous live
    loops, tasks with no live unit, and WAITING_APPROVAL tasks with no
    approval row at all (inconsistent state). Pending approvals and
    UNCERTAIN executions are REPORTED, not raised — the driver refuses
    on them with shaped results.
    """
    from . import plan_store, task_store
    from .recovery import find_uncertain
    from .tasks import TERMINAL

    task = task_store.load_task(db, task_id)
    if task is None:
        raise ResumeRefused(f"unknown task {task_id}")
    status = task.status
    if status in TERMINAL:
        raise ResumeRefused(
            f"task {task_id} is {status.value}; history immutable")

    cur = db.execute("SELECT loop_id, status, cursor, findings,"
                     " pending_plan, pending_item, updated_at FROM loops"
                     " WHERE task_id=? ORDER BY updated_at", (task_id,))
    live_loops = [row for row in cur.fetchall()
                  if row[1] in LOOP_LIVE]
    if len(live_loops) > 1:
        raise ResumeRefused(
            f"task {task_id} has {len(live_loops)} live loops;"
            " ambiguous, refusing")
    info: dict = {"task_id": task_id, "status": status.value,
                  "pending_approvals": _pending_approvals(db, task_id),
                  "uncertain": [{"task_id": tid, "kind": kind}
                                for tid, kind in find_uncertain(audit_events)
                                if tid == task_id]}
    if live_loops:
        loop_id, loop_status, cursor, findings_json, pending, item, _ = \
            live_loops[0]
        import json
        # Pending-body addressing (slice-42/D-01): a loop stranded with
        # an incomplete body plan resumes through Runner.resume, which
        # mandates a fresh observation — on a fresh stack with no page
        # open, the observe seam establishes from this persisted URL
        # (same precedent as single-road resume). Absent plan or URL,
        # url is None and resume fails shaped downstream, as before.
        url = None
        if pending is not None:
            try:
                _, pending_hints, _ = plan_store.load_plan(db, pending)
                if isinstance(pending_hints, dict):
                    url = pending_hints.get("url")
            except Exception:
                url = None
        info.update({"road": "loop", "loop_id": loop_id,
                     "loop_status": loop_status, "cursor": cursor,
                     "findings": json.loads(findings_json),
                     "pending_plan": pending, "pending_item": item,
                     "url": url,
                     "detail": f"loop {loop_id} at cursor {cursor}"})
        return info

    live_plan = None
    for pid in plan_store.plans_for_task(db, task_id):
        plan, hints, outcomes = plan_store.load_plan(db, pid)
        if plan.status in PLAN_LIVE:
            live_plan = (plan, hints, outcomes)
    if live_plan is None:
        raise ResumeRefused(
            f"task {task_id} has no live plan or loop to resume")
    plan, hints, outcomes = live_plan
    # First step not recorded done (a recorded "failed" outcome on a
    # live plan marks its parking gate, per Runner.resume).
    first_open = next((n for n, o in enumerate(outcomes) if o != "done"),
                      len(plan.steps))
    info.update({"road": "single", "subkind": _subkind(hints),
                 "plan_id": plan.plan_id, "plan_status": plan.status,
                 "first_open": first_open, "steps": len(plan.steps),
                 "url": hints.get("url") if isinstance(hints, dict)
                 else None,
                 "detail": f"plan {plan.plan_id} from step {first_open}"
                           f" of {len(plan.steps)}"})
    if str(status.value) == "WAITING_APPROVAL" and not info[
            "pending_approvals"] and not _settled_approvals(db, task_id):
        raise ResumeRefused(
            f"task {task_id} waits on approval but no approval row"
            " exists; state inconsistent, refusing")
    return info


def resume_task(taskloop, store, task, info: dict, decider, *,
                observe, inventory_fn=None):
    """Resume the unit described by resume_info(). Returns the road
    result (RunResult for single, LoopResult for loop); preconditions
    fail as shaped single-PLAN_OUTCOME STOPPED RunResults.

    Preconditions, in order: no undecided approval (name it, never
    duplicate-park); no UNCERTAIN consequential step (crash rule);
    single live road (discovery guarantees it); task ownership
    (another live holder refuses); shared-truth status with PAUSED
    moved back to RUNNING through the scheduler. Loop bodies re-park
    and single plans re-verify from the first open step; L3 gates
    always collect fresh consent.
    """
    from .loops import LoopRunner
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if info.get("pending_approvals"):
        ids = ", ".join(info["pending_approvals"])
        return refuse(
            f"task {task.task_id} has undecided approval {ids}:"
            " decide it via approve.py or let it expire, then resume")
    if info.get("uncertain"):
        kinds = ", ".join(u["kind"] for u in info["uncertain"])
        return refuse(
            f"task {task.task_id} has uncertain execution ({kinds}):"
            " started but never completed; re-observe and require a"
            " fresh approval, i.e. start a fresh run instead")
    reg = taskloop.runner.controller.router.registry
    try:
        reg.checkout(task.task_id, "cli")
    except Exception as exc:
        return refuse(f"cannot take ownership ({exc})")
    try:
        task = reg.refresh(task.task_id)
    except Exception as exc:
        return refuse(f"cannot refresh task state ({exc})")
    # Rejoin the scheduler queue in this process (the dead process's
    # heap died with it; without membership the controller grants no
    # turns). Enqueue is idempotent; usage is restored from the
    # write-through counter so the step budget continues, not
    # restarts. Only this task is enqueued — never a heap-wide
    # rebuild that could yield another task's turn.
    sched = taskloop.runner.scheduler
    if sched is not None:
        try:
            sched.enqueue(task.task_id)
            from . import task_store
            steps, _ = task_store.get_usage(store, task.task_id)
            if steps:
                sched._steps_used[task.task_id] = steps
        except Exception:
            pass
        if str(task.status.value) == "PAUSED":
            try:
                sched.resume(task.task_id)
                task = reg.get(task.task_id)
            except Exception as exc:
                return refuse(f"cannot resume paused task ({exc})")
    elif str(task.status.value) == "PAUSED":
        return refuse("cannot resume paused task without a scheduler")
    if info.get("road") == "loop":
        if inventory_fn is None:
            return refuse("loop resume needs a link inventory seam")
        if observe is None:
            return refuse("resume needs a fresh observation callable")
        lr = LoopRunner(taskloop, store, inventory_fn=inventory_fn,
                        observe=observe)
        return lr.resume(info["loop_id"], decider)
    if observe is None:
        return refuse("resume needs a fresh observation callable")
    return taskloop.resume_plan(info["plan_id"], observe, decider)
