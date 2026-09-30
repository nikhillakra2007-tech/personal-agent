"""Slice-08: boot + crash recovery.

Boot: load persisted tasks/approvals/tokens, invalidate stale owners
(ownership is runtime-only), rebuild the scheduler heap (order is never
persisted), sweep expired approvals.

Crash rule (the important one): EXECUTION_STARTED without a matching
EXECUTION_COMPLETED does NOT mean "it happened" — it means UNCERTAIN.
Recovery therefore: burn any still-live token that could authorize a
duplicate consequential execution, audit UNCERTAIN_STATE, and require the
world to be re-observed plus a FRESH approval before anything consequential
runs again. Never blindly re-execute; never assume success.
"""

from __future__ import annotations


def find_uncertain(events: list[dict]) -> list[tuple[str, str]]:
    """(task_id, kind) pairs with an unmatched EXECUTION_STARTED."""
    started: dict[tuple[str, str], int] = {}
    completed: dict[tuple[str, str], int] = {}
    for e in events:
        key = (e.get("task_id", ""), (e.get("payload", {}) or {}).get(
            "kind", ""))
        if e.get("type") == "EXECUTION_STARTED":
            started[key] = started.get(key, 0) + 1
        elif e.get("type") == "EXECUTION_COMPLETED":
            completed[key] = completed.get(key, 0) + 1
    return [key for key, n in started.items()
            if n > completed.get(key, 0)]


def boot(db, registry, approvals, scheduler, audit, ledger=None) -> dict:
    """Full boot recovery. Returns a report of what was done."""
    report: dict = {"tasks_loaded": 0, "owners_released": 0,
                    "scheduler_revived": [], "approvals_swept": [],
                    "tokens_burned": [], "uncertain": [],
                    "attempts_abandoned": []}
    registry.load_all()
    report["tasks_loaded"] = len(registry)
    approvals.load()
    if ledger is not None:
        # Crash-orphaned attempts are abandoned, never completed: an open
        # row means STARTED without COMPLETED, i.e. uncertain by definition.
        report["attempts_abandoned"] = ledger.abandon_stale()
        if report["attempts_abandoned"]:
            audit.log("RECOVERY", "-",
                      {"attempts_abandoned":
                       len(report["attempts_abandoned"])})
    approvals.load()
    report["owners_released"] = registry.release_stale_owners()
    if report["owners_released"]:
        audit.log("RECOVERY", "-",
                  {"stale_owners_released": report["owners_released"]})
    report["scheduler_revived"] = scheduler.rebuild()
    report["approvals_swept"] = approvals.sweep()
    for aid in report["approvals_swept"]:
        audit.log("APPROVAL_EXPIRED", "-", {"approval_id": aid})

    # Crash-witness scan: uncertain executions burn live tokens.
    events = audit.replay()
    consumed: dict[str, str] = {}  # (task_id, kind) -> approval_id
    for e in events:
        if e.get("type") == "APPROVAL_CONSUMED":
            payload = e.get("payload", {}) or {}
            consumed[(e.get("task_id", ""), payload.get("kind", ""))] = \
                payload.get("approval_id", "")
    for task_id, kind in find_uncertain(events):
        approval_id = consumed.get((task_id, kind))
        burned = None
        if approval_id:
            for tok_id, tok in list(approvals._tokens.items()):
                if (tok.approval_id == approval_id and not tok.used
                        and tok.task_id == task_id and tok.kind == kind):
                    approvals.burn(tok_id)
                    burned = tok_id
                    report["tokens_burned"].append(tok_id)
                    break
        report["uncertain"].append(
            {"task_id": task_id, "kind": kind, "token_burned": burned})
        audit.log("UNCERTAIN_STATE", task_id,
                  {"kind": kind, "token_burned": burned,
                   "note": "started but never completed; re-observe and"
                           " require fresh approval before retrying anything"
                           " consequential"})
    return report
