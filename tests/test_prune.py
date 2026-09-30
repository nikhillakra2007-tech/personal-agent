"""Prune tests: retention boundaries, tombstones, live rows untouched."""

from lakra.control import plan_store
from lakra.control.prune import prune
from lakra.control.store import Database
from lakra.control.tasks import Task


def _old_plan(db, task_id="t", days_old=60):
    from datetime import datetime, timedelta, timezone
    old_ts = (datetime.now(timezone.utc)
              - timedelta(days=days_old)).isoformat()
    db.execute("INSERT INTO plans(plan_id, task_id, goal, status, hints,"
               " supersedes, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (f"p-{task_id}-{days_old}", task_id, "g", "DONE", "{}",
                None, old_ts, old_ts))
    db.execute("INSERT INTO plan_steps(plan_id, idx, kind, target, effect,"
               " expect_kind, expect_target, rationale, max_retries,"
               " outcome, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (f"p-{task_id}-{days_old}", 0, "k", "t", "read", "ek",
                "et", "r", 0, "done", old_ts))
    plan_store.record_failure(db, task_id, f"p-{task_id}-{days_old}", 0,
                              "k", None, "d", "snap", None)
    # Backdate the failure row too (writer stamps now).
    db.execute("UPDATE failure_records SET created_at=? WHERE plan_id=?",
               (old_ts, f"p-{task_id}-{days_old}"))
    db.commit()


def test_prune_removes_old_terminal_keeps_tombstone(tmp_path):
    db = Database(tmp_path / "p.db")
    _old_plan(db, "t1")
    t = Task.create("List fresh things", allowed_tools=["browser"],
                    allowed_domains=["x.example"])
    from lakra.control.planner import Planner
    plan = Planner().plan(t, {"url": "u", "expect_text": "x"})
    plan_store.save_plan(db, plan, {})
    report = prune(db, older_than_days=30)
    assert report == {"plans": 1, "steps": 1, "failures": 1}
    cur = db.execute("SELECT status, hints FROM plans WHERE plan_id LIKE"
                     " 'p-t1%'").fetchone()
    assert cur[0] == "DONE"  # tombstone: row lives, detail gone
    assert plan_store.load_plan(db, plan.plan_id)[0].status == "DRAFT"
    db.close()


def test_prune_spares_live_and_recent(tmp_path):
    db = Database(tmp_path / "p.db")
    _old_plan(db, "old-terminal")
    db.execute("INSERT INTO plans(plan_id, task_id, goal, status, hints,"
               " supersedes, created_at, updated_at)"
               " VALUES ('live','t','g','EXECUTING','{}',NULL,"
               " '2030-01-01T00:00:00+00:00','2030-01-01T00:00:00+00:00')")
    db.commit()
    report = prune(db, older_than_days=30)
    assert report["plans"] == 1
    assert db.execute("SELECT COUNT(*) FROM plans WHERE plan_id='live'"
                      ).fetchone()[0] == 1
    db.close()


def test_prune_empty_db_is_noop(tmp_path):
    db = Database(tmp_path / "p.db")
    assert prune(db) == {"plans": 0, "steps": 0, "failures": 0}
    db.close()
