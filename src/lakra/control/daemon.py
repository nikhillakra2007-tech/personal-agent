"""V2-03: supervised worker over the persisted work queue.

Orchestration only: claim (or reclaim) one item, materialize it to
validated legs, hand execution to the caller-provided exec_fn (the
CLI wires the existing V2-01 + run_chain machinery; tests inject
fakes), then settle the item from the authoritative outcome. No
browser, no policy, no approvals, no tokens, no audit writes, no
lifecycle moves here — the execution plane stays solely V1's.

Loop contract (serve): recover stale claims before fresh ones,
process FIFO, stop when no work remains, when the item budget is
reached, or on explicit stop. A KeyboardInterrupt stops cleanly:
the held claim stays CLAIMED under its lease, recoverable through
the existing lease rules. No unbounded unsupervised looping: every
iteration either settles, defers, or stops.
"""

from __future__ import annotations

from . import task_store, work_queue
from .work_queue import WorkRefused

# exec_fn outcomes (authoritative task outcome, mapped 1:1 below).
DONE = "COMPLETED"
DENIED = "CANCELLED"
FAILED = "FAILED"
DEFERRED = "DEFERRED"  # task parked/paused/stalled: keep CLAIMED


def process_item(db, owner: str, item: dict, exec_fn,
                 ttl_s: int = work_queue.DEFAULT_TTL_S) -> dict:
    """Run one CLAIMED item to settled-or-deferred.

    exec_fn(item, legs, from_leg) returns {"outcome", "task_id",
    "detail"}. COMPLETED/CANCELLED/FAILED settle the queue item;
    DEFERRED leaves it CLAIMED under its lease (a later pass reclaims
    and reruns it fresh). An item whose bound task already reads
    COMPLETED settles without executing (adoption, never a rerun).
    Unmaterializable items settle FAILED with the reason recorded —
    they can never execute, so they must never block the queue.
    """
    work_id = item["work_id"]
    try:
        material = work_queue.materialize(item)
    except WorkRefused as exc:
        settled = work_queue.fail(db, work_id, owner,
                                  f"unmaterializable ({exc})")
        return {"work_id": work_id, "action": "failed",
                "state": settled["state"],
                "detail": settled["detail"]}
    legs, from_leg = material["legs"], material["from_leg"]
    bound = item.get("task_id")
    if bound:
        task = task_store.load_task(db, bound)
        if task is not None and str(task.status.value) == "COMPLETED":
            settled = work_queue.complete(db, work_id, owner,
                                          task_id=bound,
                                          detail="already COMPLETED")
            return {"work_id": work_id, "action": "adopted",
                    "state": settled["state"], "task_id": bound}
    result = exec_fn(item, legs, from_leg) or {}
    outcome = result.get("outcome", DEFERRED)
    task_id = result.get("task_id") or bound
    detail = result.get("detail", "") or ""
    if outcome == DONE:
        settled = work_queue.complete(db, work_id, owner,
                                      task_id=task_id, detail=detail)
        return {"work_id": work_id, "action": "completed",
                "state": settled["state"], "task_id": task_id}
    if outcome == DENIED:
        settled = work_queue.cancel(db, work_id, owner, detail)
        return {"work_id": work_id, "action": "cancelled",
                "state": settled["state"], "task_id": task_id}
    if outcome == FAILED:
        settled = work_queue.fail(db, work_id, owner, detail)
        return {"work_id": work_id, "action": "failed",
                "state": settled["state"], "task_id": task_id}
    return {"work_id": work_id, "action": "deferred",
            "state": "CLAIMED", "task_id": task_id}


def serve(db, owner: str, exec_fn, *, once: bool = False,
          max_items: int | None = None,
          ttl_s: int = work_queue.DEFAULT_TTL_S) -> dict:
    """Drain the queue: stale recovery first, then FIFO claims.

    Returns a summary {"processed", "settled", "deferred",
    "actions"}. Stops with no work remaining, after max_items
    processed items (once=True means one), or on KeyboardInterrupt
    (clean stop; the held claim stays recoverable).
    """
    if max_items is not None and (
            isinstance(max_items, bool) or not isinstance(max_items, int)
            or max_items < 1):
        raise WorkRefused("max_items must be a positive integer")
    limit = 1 if once else max_items
    summary: dict = {"processed": 0, "settled": 0, "deferred": 0,
                     "actions": []}
    try:
        while True:
            if limit is not None and summary["processed"] >= limit:
                break
            item = work_queue.reclaim_stale(db, owner, ttl_s)
            if item is None:
                item = work_queue.claim_next(db, owner, ttl_s)
            if item is None:
                break  # nothing queued and nothing reclaimable
            result = process_item(db, owner, item, exec_fn,
                                  ttl_s=ttl_s)
            summary["processed"] += 1
            if result["action"] == "deferred":
                summary["deferred"] += 1
            else:
                summary["settled"] += 1
            summary["actions"].append(result)
    except KeyboardInterrupt:
        summary["stopped"] = "interrupted"
    return summary
