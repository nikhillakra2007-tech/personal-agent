"""Failure-corpus tests: observational only, capped, content-free."""

from lakra.control import plan_store
from lakra.control.store import Database


def test_record_and_query_roundtrip(tmp_path):
    db = Database(tmp_path / "c.db")
    plan_store.record_failure(db, "t1", "p1", 2, "recovery-exhausted", None,
                              "bounded recovery exhausted", "page text here",
                              "ollama/qwen2.5:0.5b")
    rows = plan_store.failures_for_task(db, "t1")
    assert len(rows) == 1
    row = rows[0]
    assert (row["plan_id"], row["step_idx"], row["kind"]) == \
        ("p1", 2, "recovery-exhausted")
    assert row["model_name"] == "ollama/qwen2.5:0.5b"
    import hashlib
    assert row["snapshot_hash"] == hashlib.sha256(
        b"page text here").hexdigest()
    db.close()


def test_detail_capped_and_no_page_text_stored(tmp_path):
    db = Database(tmp_path / "c.db")
    secret_page = "password hunter2 " * 100
    plan_store.record_failure(db, "t1", "p1", 0, "blocked", "BLOCK",
                              secret_page * 50, secret_page, None)
    row = plan_store.failures_for_task(db, "t1")[0]
    assert len(row["detail"]) <= 500
    stored = db.execute("SELECT detail FROM failure_records").fetchone()[0]
    assert len(secret_page * 50) > 500 >= len(stored)  # capped, not whole
    # Full page text is never a column: only its hash.
    cols = [r[1] for r in db.execute("PRAGMA table_info(failure_records)")]
    assert "snapshot" not in cols and "page_text" not in cols
    db.close()


def test_per_task_cap_prunes_oldest(tmp_path):
    db = Database(tmp_path / "c.db")
    for i in range(55):
        plan_store.record_failure(db, "t1", f"p{i}", 0, "k", None, "d",
                                  f"snap{i}", None)
    rows = plan_store.failures_for_task(db, "t1")
    assert len(rows) == 50
    assert rows[0]["plan_id"] == "p5"  # oldest pruned first
    db.close()


def test_empty_task_queries_cleanly(tmp_path):
    db = Database(tmp_path / "c.db")
    assert plan_store.failures_for_task(db, "nobody") == []
    db.close()
