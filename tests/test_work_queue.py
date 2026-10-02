"""V2-02: crash-safe persisted work queue + run ledger.

Single-statement CAS claims (approvals-CAS precedent): exactly one
process wins per item; losers get a clean miss. Leases make stale
claims reclaimable without ever stealing live ones; terminal work
never runs again. The ledger records every claim span. This layer
never executes — materialize() hands validated legs to the existing
V2-01/run_chain machinery (the daemon's job in V2-03).

Unit tests are same-process multi-connection (WAL); the competing-
claim test uses two real OS processes.
"""

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import task_store, work_queue as wq
from lakra.control.registry import TaskRegistry
from lakra.control.store import CODE_VERSION, Database
from lakra.control.tasks import Status, Task
from lakra.control.work_queue import WorkRefused

LEGS2 = [{"road": "observe", "url": "file:///l.html",
          "expect_text": "Records"},
         {"road": "observe", "url": "file:///b.html",
          "expect_text": "detail"}]
PROSE2 = ("Show me the Records page at file:///l.html with Records,"
          " then show me the beta page at file:///b.html"
          " with detail record")


def db_at(tmp_path, name="q.db"):
    return Database(tmp_path / name)


# -- migration ---------------------------------------------------------------

def _v5_db(path):
    """Hand-built v5 database: full v1..v5 scripts + version stamp,
    no v6 objects (mirrors the v1->v2 migration-test precedent)."""
    from lakra.control.store import (MIGRATE_V1_TO_V2, MIGRATE_V2_TO_V3,
                                     MIGRATE_V3_TO_V4, MIGRATE_V4_TO_V5,
                                     SCHEMA_V1)
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA_V1)
    con.executescript(MIGRATE_V1_TO_V2)
    con.executescript(MIGRATE_V2_TO_V3)
    con.executescript(MIGRATE_V3_TO_V4)
    con.executescript(MIGRATE_V4_TO_V5)
    con.execute("INSERT INTO meta(key, value) VALUES ('v', '5')")
    con.commit()
    return con


def test_fresh_db_is_v6_with_queue_tables(tmp_path):
    db = db_at(tmp_path)
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='v'").fetchone()[0] \
            == str(CODE_VERSION) == "6"
        names = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"work_items", "runs"} <= names
    finally:
        db.close()


def test_v5_data_survives_migration_unchanged(tmp_path):
    path = tmp_path / "old.db"
    con = _v5_db(path)
    con.execute(
        "INSERT INTO tasks(task_id, goal, parent_id, status, priority,"
        " permission_level, allowed_tools, allowed_domains,"
        " allowed_paths, submission_policy, budget, created_at,"
        " updated_at, history, verification, error, owner) VALUES"
        " ('t1','Do it',NULL,'RUNNING',1,1,'[]','[]','[]','stop',"
        " '{}','c','u','[]','{}',NULL,'cli')")
    con.execute(
        "INSERT INTO approvals(approval_id, task_id, action_json,"
        " requested_at, decided, decided_at, expires_at) VALUES"
        " ('a1','t1','{}','c',NULL,NULL,'e')")
    con.commit()
    before_tasks = con.execute("SELECT * FROM tasks").fetchall()
    before_appr = con.execute("SELECT * FROM approvals").fetchall()
    con.close()
    db = Database(path)  # migrates v5 -> v6
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='v'").fetchone()[0] == "6"
        assert db.execute("SELECT * FROM tasks").fetchall() == \
            before_tasks
        assert db.execute("SELECT * FROM approvals").fetchall() == \
            before_appr
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='work_items'"
            ).fetchone() is not None
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='runs'"
            ).fetchone() is not None
    finally:
        db.close()


# -- enqueue / retrieval -------------------------------------------------------

def test_enqueue_and_get_legs(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, legs=LEGS2, from_leg=1)
        assert item["state"] == "QUEUED" and item["owner"] is None
        assert item["attempts"] == 0 and item["task_id"] is None
        same = wq.get(db, item["work_id"])
        assert same == item
        assert wq.get(db, "nope") is None
        assert [i["work_id"] for i in wq.list_items(db)] == \
            [item["work_id"]]
        assert wq.list_items(db, "QUEUED") == [item]
        assert wq.list_items(db, "COMPLETED") == []
    finally:
        db.close()


def test_enqueue_prose_and_malformed_refuse(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, prose=PROSE2)
        assert item["input_prose"] == PROSE2
        with pytest.raises(WorkRefused):
            wq.enqueue(db)
        with pytest.raises(WorkRefused):
            wq.enqueue(db, prose="   ")
        with pytest.raises(WorkRefused):
            wq.enqueue(db, legs=[{"road": "teleport"}])
        with pytest.raises(WorkRefused):
            wq.enqueue(db, legs=LEGS2, from_leg=9)
        with pytest.raises(WorkRefused):
            wq.enqueue(db, legs=LEGS2, from_leg=0)
        with pytest.raises(WorkRefused):
            wq.enqueue(db, prose=PROSE2, ttl_s=-1)
        with pytest.raises(WorkRefused):
            wq.list_items(db, "NOPE")
    finally:
        db.close()


# -- claim / settle ------------------------------------------------------------

def test_atomic_claim_complete_cycle(tmp_path):
    db = db_at(tmp_path)
    try:
        first = wq.enqueue(db, legs=LEGS2)
        second = wq.enqueue(db, legs=LEGS2)
        got = wq.claim_next(db, "worker-a")
        assert got is not None and got["work_id"] == first["work_id"]
        assert got["state"] == "CLAIMED" and got["owner"] == "worker-a"
        assert got["attempts"] == 1 and got["lease_until"] > 0
        runs = wq.runs_for(db, first["work_id"])
        assert len(runs) == 1 and runs[0]["owner"] == "worker-a"
        assert runs[0]["outcome"] is None  # still running
        nxt = wq.claim_next(db, "worker-b")
        assert nxt is not None and nxt["work_id"] == second["work_id"]
        assert wq.claim_next(db, "worker-c") is None  # queue drained
        done = wq.complete(db, first["work_id"], "worker-a",
                           task_id="task-1", detail="ok")
        assert done["state"] == "COMPLETED"
        assert done["task_id"] == "task-1"
        runs = wq.runs_for(db, first["work_id"])
        assert runs[0]["outcome"] == "COMPLETED"
        assert runs[0]["ended_at"] is not None
    finally:
        db.close()


def test_fail_and_cancel(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, legs=LEGS2)
        got = wq.claim_next(db, "w")
        out = wq.fail(db, item["work_id"], "w", "leg blew up")
        assert out["state"] == "FAILED" and out["detail"] == "leg blew up"
        assert wq.runs_for(db, item["work_id"])[0]["outcome"] == "FAILED"
        other = wq.enqueue(db, legs=LEGS2)
        out = wq.cancel(db, other["work_id"], detail="no longer needed")
        assert out["state"] == "CANCELLED"
        assert wq.runs_for(db, other["work_id"]) == []  # never claimed
        third = wq.enqueue(db, legs=LEGS2)
        wq.claim_next(db, "w")
        out = wq.cancel(db, third["work_id"], "w")
        assert out["state"] == "CANCELLED"
        with pytest.raises(WorkRefused):
            wq.complete(db, third["work_id"], "w")  # terminal: no rerun
        with pytest.raises(WorkRefused):
            wq.cancel(db, third["work_id"], "w")
        with pytest.raises(WorkRefused):
            wq.complete(db, "nope", "w")
        with pytest.raises(WorkRefused):
            wq.fail(db, other["work_id"], "stranger")  # wrong holder
    finally:
        db.close()


# -- stale claims ---------------------------------------------------------------

def test_live_claim_cannot_be_stolen(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, legs=LEGS2)
        wq.claim_next(db, "holder", ttl_s=3600)
        assert wq.reclaim_stale(db, "thief") is None
        assert wq.get(db, item["work_id"])["owner"] == "holder"
        assert wq.claim_next(db, "thief") is None
    finally:
        db.close()


def _expire(db, work_id):
    db.execute("UPDATE work_items SET lease_until=1 WHERE work_id=?",
               (work_id,))
    db.commit()


def test_stale_reclaim_marks_old_run(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, legs=LEGS2)
        wq.claim_next(db, "dead-worker", ttl_s=3600)
        _expire(db, item["work_id"])
        got = wq.reclaim_stale(db, "rescuer")
        assert got is not None and got["owner"] == "rescuer"
        assert got["attempts"] == 2
        runs = wq.runs_for(db, item["work_id"])
        assert [(r["owner"], r["outcome"]) for r in runs] == \
            [("dead-worker", "STALE"), ("rescuer", None)]
        done = wq.complete(db, item["work_id"], "rescuer",
                           task_id="task-9")
        assert done["state"] == "COMPLETED"
    finally:
        db.close()


def test_completed_work_cannot_be_reclaimed(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, legs=LEGS2)
        wq.claim_next(db, "w")
        wq.complete(db, item["work_id"], "w", task_id="t")
        _expire(db, item["work_id"])  # expiry must not matter
        assert wq.reclaim_stale(db, "thief") is None
        assert wq.get(db, item["work_id"])["state"] == "COMPLETED"
        with pytest.raises(WorkRefused):
            wq.complete(db, item["work_id"], "w", task_id="t2")
    finally:
        db.close()


def test_crash_restart_simulation(tmp_path):
    db_path = tmp_path / "crash.db"
    db = Database(db_path)
    item = wq.enqueue(db, legs=LEGS2)
    wq.claim_next(db, "doomed", ttl_s=3600)
    db.close()  # death: connection dropped mid-claim, nothing settled
    db2 = Database(db_path)  # restart: same file, fresh handle
    try:
        assert db2.execute(
            "SELECT value FROM meta WHERE key='v'").fetchone()[0] == "6"
        again = wq.get(db2, item["work_id"])
        assert again["state"] == "CLAIMED" and again["owner"] == "doomed"
        assert wq.reclaim_stale(db2, "fresh") is None  # lease still live
        _expire(db2, item["work_id"])
        got = wq.reclaim_stale(db2, "fresh")
        assert got is not None and got["owner"] == "fresh"
        done = wq.complete(db2, item["work_id"], "fresh",
                           task_id="task-r")
        assert done["state"] == "COMPLETED"
        assert wq.reclaim_stale(db2, "late") is None
    finally:
        db2.close()


# -- materialize (execution seam, no daemon) --------------------------------------

def test_materialize_legs_and_prose(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, legs=LEGS2, from_leg=2)
        out = wq.materialize(item)
        assert out["from_leg"] == 2 and len(out["legs"]) == 2
        prose_item = wq.enqueue(db, prose=PROSE2)
        out = wq.materialize(prose_item)
        assert [leg["road"] for leg in out["legs"]] == ["observe",
                                                       "observe"]
        assert out["from_leg"] == 1
        with pytest.raises(WorkRefused):
            wq.materialize({"work_id": "x", "from_leg": 0})
        with pytest.raises(WorkRefused):
            wq.materialize({"work_id": "x"})  # neither legs nor prose
        with pytest.raises(WorkRefused):
            wq.materialize({"work_id": "x", "legs_json": "{"})
        bad = dict(prose_item)
        bad["from_leg"] = 9
        with pytest.raises(WorkRefused):
            wq.materialize(bad)
    finally:
        db.close()


# -- V1 task persistence unchanged on v6 --------------------------------------------

def test_v1_task_flows_unchanged_on_v6(tmp_path):
    db = db_at(tmp_path)
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create("Do the thing",
                                allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        reg.checkout(t.task_id, "cli")
        reg.set_status(t.task_id, Status.RUNNING)
        reloaded = task_store.load_task(db, t.task_id)
        assert reloaded.goal == "Do the thing"  # write-once (V1-D1)
        assert reloaded.status == Status.RUNNING
        steps, _ = task_store.get_usage(db, t.task_id)
        assert steps == 0
    finally:
        db.close()


# -- real two-process competing claim -------------------------------------------------

CLAIM_HELPER = """
import sys, time
sys.path.insert(0, r"{src}")
from lakra.control.store import Database
from lakra.control import work_queue as wq
db = Database(r"{db}")
mode, owner = sys.argv[1], sys.argv[2]
if mode == "hold":
    got = wq.claim_next(db, owner)
    print("HELD" if got else "MISS", flush=True)
    time.sleep(120)
else:
    got = wq.claim_next(db, owner)
    print("GOT" if got else "MISS", flush=True)
"""


def test_two_processes_one_claim_wins(tmp_path):
    db_path = tmp_path / "race.db"
    db = db_at(tmp_path, "race.db")
    try:
        item = wq.enqueue(db, legs=LEGS2)
    finally:
        db.close()
    helper = tmp_path / "claimer.py"
    helper.write_text(CLAIM_HELPER.format(
        src=str(ROOT / "src"), db=str(db_path)), encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, str(helper), "hold", "proc-A"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 60
        held = False
        while time.time() < deadline:
            db = Database(db_path)
            try:
                cur = wq.get(db, item["work_id"])
            finally:
                db.close()
            if cur["state"] == "CLAIMED":
                held = True
                break
            if holder.poll() is not None:
                break
            time.sleep(1)
        assert held, "holder process never claimed"
        assert holder.poll() is None  # A still alive, holding
        rival = subprocess.run(
            [sys.executable, str(helper), "try", "proc-B"],
            capture_output=True, text=True, timeout=120)
        assert "MISS" in rival.stdout, rival.stdout
        db = Database(db_path)
        try:
            cur = wq.get(db, item["work_id"])
            assert cur["owner"] == "proc-A" and cur["attempts"] == 1
            runs = wq.runs_for(db, item["work_id"])
            assert len(runs) == 1 and runs[0]["owner"] == "proc-A"
            assert runs[0]["outcome"] is None
        finally:
            db.close()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=60)
        if holder.poll() is None:
            holder.kill()
