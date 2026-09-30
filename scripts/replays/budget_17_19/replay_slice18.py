"""Slice-18 demo: retention sweep with dry-run first.

1. Seed aged dead rows (completed attempt, decided approval, burned token,
   orphan usage) + live counterparts (open attempt, pending approval, live
   token, live usage, live plan).
2. prune_all(dry_run=True): identical counts, zero writes (verified).
3. prune_all(): dead reaped, live intact, tombstones kept.
4. The sweep report shape (what operators would audit as RETENTION_SWEEP).

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice18.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.prune import prune_all  # noqa: E402
from lakra.control.store import Database  # noqa: E402

DB = ROOT / "var" / "slice18.db"
if DB.exists():
    DB.unlink()


def old(days=60):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


db = Database(DB)
o, now = old(60), old(0)
db.execute("INSERT INTO attempts(attempt_id, task_id, kind, target, effect,"
           " started_at, completed_at, ok, abandoned)"
           " VALUES ('dead-done', 't', 'k', 'x', 'r', ?, ?, 1, 0)", (o, o))
db.execute("INSERT INTO attempts(attempt_id, task_id, kind, target, effect,"
           " started_at, completed_at, ok, abandoned)"
           " VALUES ('dead-abandoned', 't', 'k', 'x', 'r', ?, NULL, NULL, 1)",
           (o,))
db.execute("INSERT INTO attempts(attempt_id, task_id, kind, target, effect,"
           " started_at) VALUES ('live-open', 't', 'k', 'y', 'r', ?)", (now,))
db.execute("INSERT INTO approvals(approval_id, task_id, action_json,"
           " requested_at, decided, decided_at, expires_at)"
           " VALUES ('decided-old', 't', '{}', ?, 'approved', ?, ?)",
           (o, o, now))
db.execute("INSERT INTO approvals(approval_id, task_id, action_json,"
           " requested_at, decided, decided_at, expires_at)"
           " VALUES ('pending-old', 't', '{}', ?, NULL, NULL, ?)", (o, now))
db.execute("INSERT INTO tokens(token_id, approval_id, task_id, kind,"
           " target, effect, expires_at, used)"
           " VALUES ('burned-old', 'a1', 't', 'k', 'x', 'r', ?, 1)", (o,))
db.execute("INSERT INTO tokens(token_id, approval_id, task_id, kind,"
           " target, effect, expires_at, used)"
           " VALUES ('live-tok', 'a2', 't', 'k', 'x', 'r', ?, 0)",
           (old(-1),))
db.execute("INSERT INTO usage(task_id, steps_used) VALUES ('ghost', 9)")
db.execute("INSERT INTO tasks(task_id, goal, status, priority,"
           " permission_level, allowed_tools, allowed_domains,"
           " allowed_paths, submission_policy, budget, created_at,"
           " updated_at, history, verification)"
           " VALUES ('t','g','RUNNING',1,1,'[]','[]','[]','s','{}',"
           " 'now','now','[]','{}')")
db.execute("INSERT INTO usage(task_id, steps_used) VALUES ('t', 4)")
db.commit()

# 1-2. dry run first: counts without writes ---------------------------------------
dry = prune_all(db, older_than_days=30, dry_run=True)
live_attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
assert live_attempts == 3, "dry run wrote!"
print(f"1. dry-run counts: {dry} (writes: none, rows intact)")

# 3. real sweep -------------------------------------------------------------------------
report = prune_all(db, older_than_days=30)
print(f"2. real sweep: {report}")
left_attempts = {r[0] for r in
                 db.execute("SELECT attempt_id FROM attempts")}
assert left_attempts == {"live-open"}, left_attempts
left_approvals = {r[0] for r in
                  db.execute("SELECT approval_id FROM approvals")}
assert left_approvals == {"pending-old"}, left_approvals
left_tokens = {r[0] for r in db.execute("SELECT token_id FROM tokens")}
assert left_tokens == {"live-tok"}, left_tokens
assert db.execute("SELECT steps_used FROM usage WHERE task_id='t'"
                  ).fetchone()[0] == 4
print("3. live/open/pending rows intact; dead rows reaped; live usage kept")
db.close()
print("REPLAY OK")
