"""Slice-13: retention pruning (tombstones, never live rows).

Deletes step detail + failure rows for terminal plans older than N days,
keeping the plan row itself as a one-line tombstone (id, task, goal,
status, timestamps). Never touches non-terminal plans, approvals, tokens,
tasks, or usage. Reports counts; callers audit the result.

Slice-18 extension (same file, same discipline): retention for attempts,
approvals, tokens, and usage. Deletion predicates cover ONLY terminal /
settled / consumed rows older than the cutoff:
  attempts: completed or abandoned AND started before cutoff
  approvals: decided (approved or denied) AND decided before cutoff;
             pending approvals are NEVER touched regardless of age
  tokens: used=1 (burned) OR expired, AND minted before cutoff
  usage: only rows whose task no longer exists
Everything deleted is fully described by audit events, which are NEVER
pruned. dry_run=True returns identical counts with zero writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def prune(db, older_than_days: int = 30) -> dict:
    """Returns {"plans": n, "steps": m, "failures": k} deleted."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=older_than_days)).isoformat()
    cur = db.execute("SELECT plan_id FROM plans WHERE status IN"
                     " ('DONE','STOPPED','SUPERSEDED') AND updated_at < ?",
                     (cutoff,))
    old = [r[0] for r in cur.fetchall()]
    steps, failures = 0, 0
    for pid in old:
        cur = db.execute("DELETE FROM plan_steps WHERE plan_id=?", (pid,))
        steps += cur.rowcount
        cur = db.execute("DELETE FROM failure_records WHERE plan_id=?",
                         (pid,))
        failures += cur.rowcount
        db.execute("UPDATE plans SET hints='{}', updated_at=? WHERE"
                   " plan_id=?", (datetime.now(timezone.utc).isoformat(),
                                   pid))
    # Corpus rows for deleted-unknown plans (task deleted, etc.): same age.
    cur = db.execute("DELETE FROM failure_records WHERE created_at < ?"
                     " AND plan_id NOT IN (SELECT plan_id FROM plans)",
                     (cutoff,))
    failures += cur.rowcount
    db.commit()
    return {"plans": len(old), "steps": steps, "failures": failures}


def _cutoff(older_than_days: int) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=older_than_days)).isoformat()


def prune_attempts(db, older_than_days: int = 30,
                   dry_run: bool = False) -> int:
    """Delete completed/abandoned attempts started before cutoff. Open
    (uncompleted, unabandoned) rows are NEVER touched at any age."""
    cutoff = _cutoff(older_than_days)
    sel = ("SELECT attempt_id FROM attempts WHERE completed_at IS NOT NULL"
           " AND started_at < ?")
    cur = db.execute(sel, (cutoff,))
    done = [r[0] for r in cur.fetchall()]
    cur = db.execute("SELECT attempt_id FROM attempts WHERE abandoned=1"
                     " AND started_at < ?", (cutoff,))
    dead = [r[0] for r in cur.fetchall()]
    if not dry_run and (done or dead):
        db.execute("DELETE FROM attempts WHERE completed_at IS NOT NULL"
                   " AND started_at < ?", (cutoff,))
        db.execute("DELETE FROM attempts WHERE abandoned=1"
                   " AND started_at < ?", (cutoff,))
        db.commit()
    return len(done) + len(dead)


def prune_approvals(db, older_than_days: int = 30,
                    dry_run: bool = False) -> int:
    """Delete DECIDED approvals decided before cutoff. Pending (undecided)
    approvals are NEVER touched regardless of age."""
    cutoff = _cutoff(older_than_days)
    cur = db.execute("SELECT approval_id FROM approvals WHERE decided IS NOT"
                     " NULL AND decided_at IS NOT NULL AND decided_at < ?",
                     (cutoff,))
    old = [r[0] for r in cur.fetchall()]
    if not dry_run and old:
        db.execute("DELETE FROM approvals WHERE decided IS NOT NULL AND"
                   " decided_at IS NOT NULL AND decided_at < ?", (cutoff,))
        db.commit()
    return len(old)


def prune_tokens(db, older_than_days: int = 30,
                 dry_run: bool = False) -> int:
    """Delete tokens whose whole lifetime predates the cutoff
    (expires_at < cutoff implies burned-long-ago or long-expired; live
    tokens always outlive the cutoff and are NEVER touched)."""
    cutoff = _cutoff(older_than_days)
    cur = db.execute("SELECT token_id FROM tokens WHERE expires_at < ?",
                     (cutoff,))
    old = [r[0] for r in cur.fetchall()]
    if not dry_run and old:
        db.execute("DELETE FROM tokens WHERE expires_at < ?", (cutoff,))
        db.commit()
    return len(old)


def prune_usage(db, older_than_days: int = 30,
                dry_run: bool = False) -> int:
    """Delete usage rows whose task no longer exists. Usage for live tasks
    is NEVER touched (counters are load-bearing). Age criterion does not
    apply: orphanhood is the predicate."""
    cur = db.execute("SELECT task_id FROM usage WHERE task_id NOT IN"
                     " (SELECT task_id FROM tasks)")
    orphans = [r[0] for r in cur.fetchall()]
    if not dry_run and orphans:
        db.execute("DELETE FROM usage WHERE task_id NOT IN"
                   " (SELECT task_id FROM tasks)")
        db.commit()
    return len(orphans)


def prune_all(db, older_than_days: int = 30,
              dry_run: bool = False) -> dict:
    """Full retention sweep. dry_run=True: identical counts, zero writes.
    Callers audit the result as RETENTION_SWEEP (slice-17 event family)."""
    report = dict(prune(db, older_than_days) if not dry_run else
                  _dry_plans(db, older_than_days))
    report["attempts"] = prune_attempts(db, older_than_days, dry_run)
    report["approvals"] = prune_approvals(db, older_than_days, dry_run)
    report["tokens"] = prune_tokens(db, older_than_days, dry_run)
    report["usage"] = prune_usage(db, dry_run=dry_run)
    return report


def _dry_plans(db, older_than_days: int) -> dict:
    cutoff = _cutoff(older_than_days)
    cur = db.execute("SELECT plan_id FROM plans WHERE status IN"
                     " ('DONE','STOPPED','SUPERSEDED') AND updated_at < ?",
                     (cutoff,))
    old = [r[0] for r in cur.fetchall()]
    steps = failures = 0
    for pid in old:
        cur = db.execute("SELECT COUNT(*) FROM plan_steps WHERE plan_id=?",
                         (pid,))
        steps += cur.fetchone()[0]
        cur = db.execute("SELECT COUNT(*) FROM failure_records WHERE"
                         " plan_id=?", (pid,))
        failures += cur.fetchone()[0]
    cur = db.execute("SELECT COUNT(*) FROM failure_records WHERE"
                     " created_at < ? AND plan_id NOT IN"
                     " (SELECT plan_id FROM plans)", (cutoff,))
    failures += cur.fetchone()[0]
    return {"plans": len(old), "steps": steps, "failures": failures}
