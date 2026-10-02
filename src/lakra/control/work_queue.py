"""V2-02: crash-safe persisted work queue + run ledger.

Work items survive process death and are claimed atomically across
processes through single-statement compare-and-set UPDATES (the same
precedent as approval CAS). States: QUEUED -> CLAIMED -> terminal
(COMPLETED | FAILED | CANCELLED). A CLAIMED item carries a lease
(lease_until epoch seconds); expiry makes it reclaimable, never
stealable while live. The ledger records one row per claim so every
run's owner,span was CLAIMED (never a heap-wide rebuild that could
take another task's turn); usage continues from the persisted
counter, it never restarts.

This layer never executes: materialize() turns a claimed item into
validated road-goal legs for the existing V2-01/run_chain machinery
(the daemon's job in V2-03). No policy, no approvals, no tokens, no
audit writes, no lifecycle moves here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4

STATES = frozenset({"QUEUED", "CLAIMED", "COMPLETED", "FAILED",
                    "CANCELLED"})
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
RUN_OUTCOMES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "STALE"})

DEFAULT_TTL_S = 600


class WorkRefused(ValueError):
    """Malformed item or illegal queue transition. Fail-closed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch() -> int:
    return int(time.time())


def _check_owner(owner) -> str:
    if not isinstance(owner, str) or not owner.strip():
        raise WorkRefused("owner must be a non-empty string")
    return owner


def _check_ttl(ttl_s) -> int:
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, int) \
            or ttl_s < 0:
        raise WorkRefused("ttl_s must be a non-negative integer")
    return ttl_s


def _row_to_item(row) -> dict:
    (work_id, prose, legs_json, from_leg, state, owner, attempts,
     task_id, detail, created_at, claimed_at, lease_until,
     updated_at) = row
    return {"work_id": work_id, "input_prose": prose,
            "legs_json": legs_json, "from_leg": from_leg,
            "state": state, "owner": owner, "attempts": attempts,
            "task_id": task_id, "detail": detail or "",
            "created_at": created_at, "claimed_at": claimed_at,
            "lease_until": lease_until, "updated_at": updated_at}


_COLUMNS = ("work_id, input_prose, legs_json, from_leg, state, owner,"
            " attempts, task_id, detail, created_at, claimed_at,"
            " lease_until, updated_at")


def enqueue(db, *, prose=None, legs=None, from_leg: int = 1,
            ttl_s: int = DEFAULT_TTL_S) -> dict:
    """Persist one QUEUED work item. legs (when given) must already
    satisfy the real validate_chain(); prose must be non-empty (it is
    decomposed at materialize time, never here). from_leg range is
    checked now when legs are known, otherwise at materialize."""
    from .chain import validate_chain
    _check_ttl(ttl_s)
    if isinstance(from_leg, bool) or not isinstance(from_leg, int) \
            or from_leg < 1:
        raise WorkRefused("from_leg must be a positive integer")
    legs_json = None
    if legs is not None:
        from .chain import ChainRefused
        try:
            validated = validate_chain({"legs": legs})
        except ChainRefused as exc:
            raise WorkRefused(f"invalid legs ({exc})") from None
        if not (1 <= from_leg <= len(validated)):
            raise WorkRefused(
                f"invalid from_leg {from_leg!r} for a"
                f" {len(validated)}-leg chain")
        legs_json = json.dumps({"legs": validated})
    if prose is not None and \
            (not isinstance(prose, str) or not prose.strip()):
        raise WorkRefused("prose must be a non-empty string")
    if prose is None and legs_json is None:
        raise WorkRefused("work needs prose or legs")
    work_id = uuid4().hex
    now = _now()
    db.execute(
        f"INSERT INTO work_items({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,"
        "?,?,?,?)",
        (work_id, prose, legs_json, from_leg, "QUEUED", None, 0,
         None, "", now, None, 0, now))
    db.commit()
    item = get(db, work_id)
    assert item is not None
    return item


def get(db, work_id: str) -> dict | None:
    cur = db.execute(f"SELECT {_COLUMNS} FROM work_items WHERE"
                     " work_id=?", (work_id,))
    row = cur.fetchone()
    return _row_to_item(row) if row else None


def list_items(db, state: str | None = None) -> list:
    if state is None:
        cur = db.execute(f"SELECT {_COLUMNS} FROM work_items ORDER BY"
                         " created_at, work_id")
    else:
        if state not in STATES:
            raise WorkRefused(f"unknown state {state!r}")
        cur = db.execute(f"SELECT {_COLUMNS} FROM work_items WHERE"
                         " state=? ORDER BY created_at, work_id",
                         (state,))
    return [_row_to_item(r) for r in cur.fetchall()]


def _open_run(db, work_id: str, owner: str, now: str) -> None:
    db.execute("INSERT INTO runs(run_id, work_id, owner, claimed_at,"
               " ended_at, outcome, detail) VALUES (?,?,?,?,?,?,?)",
               (uuid4().hex, work_id, owner, now, None, None, ""))


def _take(db, work_id: str, owner: str, ttl_s: int,
          extra_where: str, extra_params: tuple) -> dict | None:
    """Atomic claim CAS + ledger row. Returns the item on success,
    None when the row is gone or held (clean miss, never an error)."""
    now = _now()
    cur = db.execute(
        "UPDATE work_items SET state='CLAIMED', owner=?, claimed_at=?,"
        " lease_until=?, attempts=attempts+1, updated_at=? WHERE"
        f" work_id=? AND {extra_where}",
        (owner, now, _epoch() + ttl_s, now, work_id, *extra_params))
    db.commit()
    if cur.rowcount != 1:
        return None
    _open_run(db, work_id, owner, now)
    db.commit()
    item = get(db, work_id)
    assert item is not None and item["owner"] == owner
    return item


def claim_next(db, owner: str, ttl_s: int = DEFAULT_TTL_S) -> dict | None:
    """Atomically claim the oldest QUEUED item. Contending processes
    race on the CAS: exactly one wins per item; losers move on to the
    next candidate instead of failing. Returns None when the queue
    is empty (or only held/terminal work remains)."""
    owner = _check_owner(owner)
    _check_ttl(ttl_s)
    while True:
        cur = db.execute("SELECT work_id FROM work_items WHERE"
                         " state='QUEUED' ORDER BY created_at, work_id"
                         " LIMIT 1")
        row = cur.fetchone()
        if row is None:
            return None
        got = _take(db, row[0], owner, ttl_s, "state='QUEUED'", ())
        if got is not None:
            return got
        # Lost the race on this row; look for the next candidate.


def reclaim_stale(db, owner: str,
                  ttl_s: int = DEFAULT_TTL_S) -> dict | None:
    """Reclaim the oldest CLAIMED item whose lease expired. Live
    claims (lease still held) are never touched: the CAS requires
    expiry, so an active holder cannot be stolen. The previous run
    is closed STALE and a fresh ledger row opens. Terminal work is
    never reclaimable (only CLAIMED rows match)."""
    owner = _check_owner(owner)
    _check_ttl(ttl_s)
    now_epoch = _epoch()
    cur = db.execute("SELECT work_id, owner FROM work_items WHERE"
                     " state='CLAIMED' AND lease_until<=? ORDER BY"
                     " claimed_at, work_id LIMIT 1", (now_epoch,))
    row = cur.fetchone()
    if row is None:
        return None
    work_id, old_owner = row
    now = _now()
    cur = db.execute(
        "UPDATE work_items SET owner=?, claimed_at=?, lease_until=?,"
        " attempts=attempts+1, updated_at=? WHERE work_id=?"
        " AND state='CLAIMED' AND lease_until<=?",
        (owner, now, now_epoch + ttl_s, now, work_id, now_epoch))
    db.commit()
    if cur.rowcount != 1:
        return None  # settled or re-claimed between scan and CAS
    db.execute("UPDATE runs SET ended_at=?, outcome='STALE' WHERE"
               " work_id=? AND outcome IS NULL AND owner=?",
               (now, work_id, old_owner))
    _open_run(db, work_id, owner, now)
    db.commit()
    item = get(db, work_id)
    assert item is not None and item["owner"] == owner
    return item


def _settle(db, work_id: str, owner: str, outcome: str,
            task_id=None, detail: str = "") -> dict:
    """Holder-only terminal transition + ledger close. Anything else
    (unknown id, wrong holder, non-CLAIMED state) refuses."""
    owner = _check_owner(owner)
    if outcome not in TERMINAL:
        raise WorkRefused(f"unknown outcome {outcome!r}")
    now = _now()
    if outcome == "COMPLETED":
        cur = db.execute(
            "UPDATE work_items SET state=?, task_id=?, detail=?,"
            " updated_at=? WHERE work_id=? AND state='CLAIMED'"
            " AND owner=?", (outcome, task_id, detail or "", now,
                             work_id, owner))
    else:
        cur = db.execute(
            "UPDATE work_items SET state=?, detail=?, updated_at=?"
            " WHERE work_id=? AND state='CLAIMED' AND owner=?",
            (outcome, detail or "", now, work_id, owner))
    db.commit()
    if cur.rowcount != 1:
        raise WorkRefused(
            f"cannot settle {work_id}: not CLAIMED by {owner}")
    db.execute("UPDATE runs SET ended_at=?, outcome=?, detail=?"
               " WHERE work_id=? AND outcome IS NULL AND owner=?",
               (now, outcome, detail or "", work_id, owner))
    db.commit()
    item = get(db, work_id)
    assert item is not None
    return item


def complete(db, work_id: str, owner: str, task_id=None,
             detail: str = "") -> dict:
    """Mark claimed work COMPLETED (optionally linked to the V1 task
    that executed it). Terminal work can never run again: every other
    transition refuses once terminal."""
    return _settle(db, work_id, owner, "COMPLETED", task_id, detail)


def fail(db, work_id: str, owner: str, detail: str = "") -> dict:
    """Mark claimed work FAILED with a recorded reason."""
    return _settle(db, work_id, owner, "FAILED", None, detail)


def cancel(db, work_id: str, owner: str | None = None,
           detail: str = "") -> dict:
    """Cancel QUEUED work (any caller) or CLAIMED work (holder only).
    Terminal work stays terminal."""
    now = _now()
    if owner is None:
        cur = db.execute(
            "UPDATE work_items SET state='CANCELLED', detail=?,"
            " updated_at=? WHERE work_id=? AND state='QUEUED'",
            (detail or "", now, work_id))
        db.commit()
        if cur.rowcount != 1:
            raise WorkRefused(
                f"cannot cancel {work_id}: not QUEUED")
    else:
        _check_owner(owner)
        cur = db.execute(
            "UPDATE work_items SET state='CANCELLED', detail=?,"
            " updated_at=? WHERE work_id=? AND state='CLAIMED'"
            " AND owner=?", (detail or "", now, work_id, owner))
        db.commit()
        if cur.rowcount != 1:
            raise WorkRefused(
                f"cannot cancel {work_id}: not CLAIMED by {owner}")
        db.execute("UPDATE runs SET ended_at=?, outcome='CANCELLED',"
                   " detail=? WHERE work_id=? AND outcome IS NULL"
                   " AND owner=?", (now, detail or "", work_id, owner))
        db.commit()
    item = get(db, work_id)
    assert item is not None
    return item


def bind_task(db, work_id: str, owner: str, task_id: str) -> dict:
    """Record the V1 task executing claimed work, holder-only CAS.

    Persisted at task creation (before execution) so a crash between
    creation and settle cannot orphan the task: recovery reuses the
    same task instead of creating a duplicate. Anything else
    (unknown id, wrong holder, non-CLAIMED state) refuses."""
    _check_owner(owner)
    if not isinstance(task_id, str) or not task_id:
        raise WorkRefused("task_id must be a non-empty string")
    cur = db.execute(
        "UPDATE work_items SET task_id=?, updated_at=? WHERE work_id=?"
        " AND state='CLAIMED' AND owner=?",
        (task_id, _now(), work_id, owner))
    db.commit()
    if cur.rowcount != 1:
        raise WorkRefused(
            f"cannot bind task on {work_id}: not CLAIMED by {owner}")
    item = get(db, work_id)
    assert item is not None
    return item


def runs_for(db, work_id: str) -> list:
    """Ledger rows for one work item, oldest first."""
    cur = db.execute("SELECT run_id, work_id, owner, claimed_at,"
                     " ended_at, outcome, detail FROM runs WHERE"
                     " work_id=? ORDER BY claimed_at, run_id",
                     (work_id,))
    keys = ("run_id", "work_id", "owner", "claimed_at", "ended_at",
            "outcome", "detail")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def materialize(item: dict) -> dict:
    """Execution seam (no daemon, no browser): a claimed item ->
    {"legs", "from_leg"} runnable through the existing V2-01 +
    run_chain machinery. Stored legs re-validate; prose decomposes
    fresh. Anything malformed refuses before anything executes."""
    from .chain import ChainRefused, validate_chain
    from .decompose import DecomposeRefused, decompose_goal
    if not isinstance(item, dict):
        raise WorkRefused("work item must be a mapping")
    from_leg = item.get("from_leg", 1)
    if isinstance(from_leg, bool) or not isinstance(from_leg, int) \
            or from_leg < 1:
        raise WorkRefused("from_leg must be a positive integer")
    if item.get("legs_json"):
        try:
            legs = json.loads(item["legs_json"])["legs"]
            validated = validate_chain({"legs": legs})
        except (ValueError, KeyError, ChainRefused) as exc:
            raise WorkRefused(f"stored legs invalid ({exc})") from None
    elif item.get("input_prose"):
        # Decomposition is the validation here: decompose_goal only
        # returns well-shaped legs (search submit selectors ground at
        # the execution edge, where the real validate_chain runs on
        # the exact execution dicts).
        try:
            validated = [dict(leg) for leg in
                         decompose_goal(item["input_prose"])]
        except DecomposeRefused as exc:
            raise WorkRefused(f"prose does not decompose ({exc})"
                              ) from None
    else:
        raise WorkRefused("work item has neither legs nor prose")
    if not (1 <= from_leg <= len(validated)):
        raise WorkRefused(f"invalid from_leg {from_leg!r} for a"
                          f" {len(validated)}-leg chain")
    return {"legs": validated, "from_leg": from_leg}
