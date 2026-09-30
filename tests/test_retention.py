"""Retention tests: delete only the dead, never the live; dry-run purity."""

from datetime import datetime, timedelta, timezone

from lakra.control.prune import (
    prune_all,
    prune_approvals,
    prune_attempts,
    prune_tokens,
    prune_usage,
)
from lakra.control.store import Database


def old_ts(days=60):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def seed_attempt(db, aid, days_old, completed=True, abandoned=False):
    ts = old_ts(days_old)
    db.execute("INSERT INTO attempts(attempt_id, task_id, kind, target,"
               " effect, started_at, completed_at, ok, abandoned)"
               " VALUES (?,?,?,?,?,?,?,?,?)",
               (aid, "t", "k", "x", "read", ts,
                ts if completed else None, 1 if completed else None,
                1 if abandoned else 0))
    db.commit()


def test_attempts_old_completed_and_abandoned_reaped():
    db = Database(":memory:")
    seed_attempt(db, "done-old", 60, completed=True)
    seed_attempt(db, "abandoned-old", 60, completed=False, abandoned=True)
    assert prune_attempts(db, older_than_days=30) == 2
    db.close()


def test_attempts_open_and_young_spared():
    db = Database(":memory:")
    seed_attempt(db, "open-old", 60, completed=False, abandoned=False)
    seed_attempt(db, "done-young", 0, completed=True)
    assert prune_attempts(db, older_than_days=30) == 0
    db.close()


def test_approvals_decided_old_reaped_pending_spared():
    db = Database(":memory:")
    old, now = old_ts(60), old_ts(0)
    db.execute("INSERT INTO approvals(approval_id, task_id, action_json,"
               " requested_at, decided, decided_at, expires_at)"
               " VALUES (?,?,?,?,?,?,?)",
               ("decided-old", "t", "{}", old, "approved", old, now))
    db.execute("INSERT INTO approvals(approval_id, task_id, action_json,"
               " requested_at, decided, decided_at, expires_at)"
               " VALUES (?,?,?,?,?,?,?)",
               ("pending-old", "t", "{}", old, None, None, now))
    db.execute("INSERT INTO approvals(approval_id, task_id, action_json,"
               " requested_at, decided, decided_at, expires_at)"
               " VALUES (?,?,?,?,?,?,?)",
               ("decided-young", "t", "{}", now, "denied", now, now))
    db.commit()
    assert prune_approvals(db, older_than_days=30) == 1
    left = {r[0] for r in
            db.execute("SELECT approval_id FROM approvals").fetchall()}
    assert left == {"pending-old", "decided-young"}
    db.close()


def test_tokens_dead_reaped_live_spared():
    db = Database(":memory:")
    old, future = old_ts(60), old_ts(-1)
    db.execute("INSERT INTO tokens(token_id, approval_id, task_id, kind,"
               " target, effect, expires_at, used) VALUES (?,?,?,?,?,?,?,?)",
               ("burned-old", "a1", "t", "k", "x", "r", old, 1))
    db.execute("INSERT INTO tokens(token_id, approval_id, task_id, kind,"
               " target, effect, expires_at, used) VALUES (?,?,?,?,?,?,?,?)",
               ("live", "a2", "t", "k", "x", "r", future, 0))
    db.commit()
    assert prune_tokens(db, older_than_days=30) == 1
    left = {r[0] for r in db.execute("SELECT token_id FROM tokens")}
    assert left == {"live"}
    db.close()


def test_usage_orphans_reaped_live_kept():
    db = Database(":memory:")
    db.execute("INSERT INTO usage(task_id, steps_used) VALUES ('ghost', 3)")
    db.execute("INSERT INTO tasks(task_id, goal, status, priority,"
               " permission_level, allowed_tools, allowed_domains,"
               " allowed_paths, submission_policy, budget, created_at,"
               " updated_at, history, verification)"
               " VALUES ('t','g','QUEUED',1,1,'[]','[]','[]','s','{}',"
               " 'now','now','[]','{}')")
    db.execute("INSERT INTO usage(task_id, steps_used) VALUES ('t', 1)")
    db.commit()
    assert prune_usage(db) == 1
    assert db.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 1
    db.close()


def test_dry_run_counts_without_writes():
    db = Database(":memory:")
    seed_attempt(db, "done-old", 60, completed=True)
    n_before = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    assert prune_attempts(db, older_than_days=30, dry_run=True) == 1
    assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == \
        n_before
    db.close()


def test_prune_all_composes_and_counts():
    db = Database(":memory:")
    seed_attempt(db, "a1", 60, completed=True)
    report = prune_all(db, older_than_days=30)
    assert report["attempts"] == 1
    assert set(report) >= {"plans", "steps", "failures", "attempts",
                           "approvals", "tokens", "usage"}
    db.close()


def test_prune_all_dry_run_is_pure():
    db = Database(":memory:")
    seed_attempt(db, "a1", 60, completed=True)
    before = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    report = prune_all(db, older_than_days=30, dry_run=True)
    assert report["attempts"] == 1
    assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == \
        before
    db.close()
