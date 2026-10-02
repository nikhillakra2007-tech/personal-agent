"""V2-03: supervised worker over the persisted queue.

daemon.py is orchestration only (claim -> materialize -> injected
exec_fn -> settle); the CLI wires the existing V2-01 + run_chain
machinery. Unit tests inject fake executors (no browser); live
tests run the real --daemon/--once CLI on fixtures, including
kill-at-park recovery and cross-process approve/deny.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import work_queue as wq
from lakra.control.daemon import process_item, serve
from lakra.control.store import Database
from lakra.control.work_queue import WorkRefused

LEGS2 = [{"road": "observe", "url": "file:///l.html",
          "expect_text": "Records"},
         {"road": "observe", "url": "file:///b.html",
          "expect_text": "detail"}]
PROSE2 = ("Show me the Records page at file:///l.html with Records,"
          " then show me the beta page at file:///b.html"
          " with detail record")

FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = (FIXTURES / "loop-index.html").as_uri()
FORM_URL = (FIXTURES / "courses.html").as_uri()
FORM_SLOTS = {"name": {"text": "Ada"}}


def db_at(tmp_path, name="dq.db"):
    return Database(tmp_path / name)


def queued(db, **kw):
    kw.setdefault("legs", LEGS2)
    return wq.enqueue(db, **kw)


def ok_exec(task_id="task-1", detail="done"):
    def _fn(item, legs, from_leg):
        assert [l["road"] for l in legs] == ["observe", "observe"]
        assert from_leg == item["from_leg"]
        return {"outcome": "COMPLETED", "task_id": task_id,
                "detail": detail}
    return _fn


# -- unit: serve loop ------------------------------------------------------------

def test_empty_queue_exits_cleanly(tmp_path):
    db = db_at(tmp_path)
    try:
        out = serve(db, "w", ok_exec())
        assert out == {"processed": 0, "settled": 0, "deferred": 0,
                       "actions": []}
    finally:
        db.close()


def test_single_item_settles_completed(tmp_path):
    db = db_at(tmp_path)
    try:
        item = queued(db)
        out = serve(db, "w", ok_exec(task_id="t-1"), once=True)
        assert out["processed"] == 1 and out["settled"] == 1
        assert out["actions"][0]["action"] == "completed"
        got = wq.get(db, item["work_id"])
        assert got["state"] == "COMPLETED" and got["task_id"] == "t-1"
    finally:
        db.close()


def test_once_processes_at_most_one(tmp_path):
    db = db_at(tmp_path)
    try:
        queued(db)
        queued(db)
        out = serve(db, "w", ok_exec(), once=True)
        assert out["processed"] == 1
        assert len(wq.list_items(db, "QUEUED")) == 1
    finally:
        db.close()


def test_fifo_order_and_linkage(tmp_path):
    db = db_at(tmp_path)
    try:
        ids = [queued(db)["work_id"] for _ in range(3)]
        seen = []

        def _fn(item, legs, from_leg):
            seen.append(item["work_id"])
            return {"outcome": "COMPLETED",
                    "task_id": "task-" + item["work_id"][:4],
                    "detail": ""}
        out = serve(db, "w", _fn)
        assert out["processed"] == 3 and out["settled"] == 3
        assert seen == ids
        for wid in ids:
            got = wq.get(db, wid)
            assert got["state"] == "COMPLETED"
            assert got["task_id"] == "task-" + wid[:4]
            assert len(wq.runs_for(db, wid)) == 1
    finally:
        db.close()


def test_completed_never_rerun(tmp_path):
    db = db_at(tmp_path)
    try:
        item = queued(db)
        assert serve(db, "w", ok_exec())["processed"] == 1
        calls = []
        out = serve(db, "w", lambda *a: calls.append(a) or
                    {"outcome": "COMPLETED", "task_id": "t"})
        assert out["processed"] == 0 and calls == []
    finally:
        db.close()


def test_failed_cancelled_deferred_mapping(tmp_path):
    db = db_at(tmp_path)
    try:
        f = queued(db)
        c = queued(db)
        d = queued(db)
        outcomes = iter(["FAILED", "CANCELLED", "DEFERRED"])

        def _fn(item, legs, from_leg):
            return {"outcome": next(outcomes), "task_id": "t",
                    "detail": "why"}
        out = serve(db, "w", _fn, max_items=3)
        assert [a["action"] for a in out["actions"]] == \
            ["failed", "cancelled", "deferred"]
        assert wq.get(db, f["work_id"])["state"] == "FAILED"
        assert wq.get(db, c["work_id"])["state"] == "CANCELLED"
        assert wq.get(db, d["work_id"])["state"] == "CLAIMED"
        assert out["deferred"] == 1
    finally:
        db.close()


def test_unmaterializable_settles_failed(tmp_path):
    db = db_at(tmp_path)
    try:
        item = wq.enqueue(db, prose=PROSE2)
        db.execute("UPDATE work_items SET legs_json='{bad',"
                   " input_prose=NULL WHERE work_id=?",
                   (item["work_id"],))
        db.commit()
        got = wq.claim_next(db, "w")
        out = process_item(db, "w", got, ok_exec())
        assert out["action"] == "failed"
        assert wq.get(db, item["work_id"])["state"] == "FAILED"
    finally:
        db.close()


def test_stale_recovered_before_fresh_and_live_held(tmp_path):
    db = db_at(tmp_path)
    try:
        fresh = queued(db)
        stale = queued(db)
        wq.claim_next(db, "dead", ttl_s=3600)
        # Reorder: make the stale item older is unnecessary; reclaim
        # path is checked first regardless of FIFO position.
        db.execute("UPDATE work_items SET lease_until=1 WHERE"
                   " state='CLAIMED'")
        db.commit()
        order = []
        out = serve(db, "w", lambda item, legs, fl:
                    order.append(item["work_id"]) or
                    {"outcome": "COMPLETED", "task_id": "t",
                     "detail": ""}, max_items=2)
        assert out["processed"] == 2
        assert order[0] == stale["work_id"]  # recovery first
        assert order[1] == fresh["work_id"]
    finally:
        db.close()


def test_process_death_no_duplicate_task(tmp_path):
    db_path = tmp_path / "crash.db"
    db = Database(db_path)
    item = queued(db)
    wq.claim_next(db, "doomed")
    # Worker created the task and bound it, then died pre-settle.
    wq.bind_task(db, item["work_id"], "doomed", "task-1")
    db.close()
    db2 = Database(db_path)
    try:
        db2.execute("UPDATE work_items SET lease_until=1 WHERE"
                    " work_id=?", (item["work_id"],))
        db2.commit()
        used_tasks = []

        def _fn(item, legs, from_leg):
            # Same bound task reused, never duplicated.
            cur = wq.get(db2, item["work_id"])
            assert cur["task_id"] == "task-1"
            used_tasks.append(cur["task_id"])
            return {"outcome": "COMPLETED", "task_id": "task-1",
                    "detail": ""}
        out = serve(db2, "fresh", _fn)
        assert out["processed"] == 1 and used_tasks == ["task-1"]
    finally:
        db2.close()


def test_interrupt_stops_cleanly(tmp_path):
    db = db_at(tmp_path)
    try:
        for _ in range(3):
            queued(db)
        calls = []

        def _fn(item, legs, from_leg):
            calls.append(item["work_id"])
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return {"outcome": "COMPLETED", "task_id": "t",
                    "detail": ""}
        out = serve(db, "w", _fn)
        assert out.get("stopped") == "interrupted"
        assert out["processed"] == 1  # second call interrupted first
        states = sorted(i["state"] for i in wq.list_items(db))
        assert states == ["CLAIMED", "COMPLETED", "QUEUED"]
    finally:
        db.close()


def test_bad_max_items_refused(tmp_path):
    db = db_at(tmp_path)
    try:
        with pytest.raises(WorkRefused):
            serve(db, "w", ok_exec(), max_items=0)
    finally:
        db.close()


# -- live CLI ---------------------------------------------------------------------

def _env(tmp_path, db="cli.db"):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / db)
    return env


def _base(tmp_path, prof):
    return [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(tmp_path / "audit-do.jsonl"),
            "--profile-dir", str(tmp_path / prof),
            "--shots-dir", str(tmp_path / "shots"),
            "--ram-floor-mb", "0", "--cpu-ceiling", "100"]


def _enqueue(tmp_path, db="cli.db", **kw):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    from lakra.control import work_queue as q
    d = D(tmp_path / db)
    try:
        return q.enqueue(d, **kw)
    finally:
        d.close()


def _tasks(tmp_path, db="cli.db"):
    env = _env(tmp_path, db)
    out = subprocess.run(
        _base(tmp_path, "ls") + ["--list", "--state", "all",
                                 "--format", "json"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    return __import__("json").loads(out.stdout)["tasks"]


def _chain_file(tmp_path, name, legs):
    p = tmp_path / name
    p.write_text(__import__("json").dumps({"legs": legs}),
                 encoding="utf-8")
    return str(p)


OBS2 = [{"road": "observe",
         "url": (FIXTURES / "loop-index.html").as_uri(),
         "expect_text": "Records"},
        {"road": "observe",
         "url": (FIXTURES / "loop-beta.html").as_uri(),
         "expect_text": "detail record: Beta"}]


def test_cli_daemon_once_single_item(tmp_path):
    _enqueue(tmp_path, legs=OBS2)
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "d1") + ["--daemon", "--once", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "processed=1 settled=1" in proc.stdout
    assert _tasks(tmp_path)[0]["status"] == "COMPLETED"


def test_cli_daemon_drains_three_in_order(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    from lakra.control import work_queue as q
    ids = []
    for _ in range(3):
        d = D(tmp_path / "cli.db")
        try:
            ids.append(q.enqueue(d, legs=OBS2)["work_id"])
        finally:
            d.close()
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "d3") + ["--daemon", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "processed=3 settled=3" in proc.stdout
    assert [t["status"] for t in _tasks(tmp_path)] == \
        ["COMPLETED"] * 3
    d = D(tmp_path / "cli.db")
    try:
        states = [q.get(d, wid)["state"] for wid in ids]
        assert states == ["COMPLETED"] * 3
    finally:
        d.close()


def test_cli_daemon_empty_exits_cleanly(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "de") + ["--daemon", "--once", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "processed=0" in proc.stdout


def _wait_park(audit_path, timeout_s=150):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            lines = Path(audit_path).read_text(
                encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in reversed(lines):
            if '"ACTION_REQUIRES_APPROVAL"' in line:
                return True
        time.sleep(2)
    return False


FORM_CHAIN = [{"road": "observe",
               "url": (FIXTURES / "loop-index.html").as_uri(),
               "expect_text": "Records"},
              {"road": "form",
               "form_url": (FIXTURES / "courses.html").as_uri(),
               "goal_slots": FORM_SLOTS,
               "submit_selector": "#m-submit"}]


def test_cli_daemon_kill_at_park_recovers(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    from lakra.control import work_queue as q
    d = D(tmp_path / "cli.db")
    try:
        wid = q.enqueue(d, legs=FORM_CHAIN)["work_id"]
    finally:
        d.close()
    env = _env(tmp_path)
    worker = subprocess.Popen(
        _base(tmp_path, "die") + ["--daemon", "--once", "--poll",
                                  "180"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env)
    try:
        assert _wait_park(tmp_path / "audit-do.jsonl"), \
            "daemon never parked at the L3 gate"
        worker.terminate()  # death holding the claim at the gate
        worker.wait(timeout=60)
    finally:
        if worker.poll() is None:
            worker.kill()
    d = D(tmp_path / "cli.db")
    try:
        cur = q.get(d, wid)
        assert cur["state"] == "CLAIMED"  # live claim, not stolen yet
        assert q.reclaim_stale(d, "intruder") is None
        d.execute("UPDATE work_items SET lease_until=1 WHERE"
                  " work_id=?", (wid,))
        d.commit()
    finally:
        d.close()
    approved = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "approve.py"), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert "approved" in approved.stdout, approved.stdout
    retry = subprocess.run(
        _base(tmp_path, "re") + ["--daemon", "--once", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    d = D(tmp_path / "cli.db")
    try:
        cur = q.get(d, wid)
        assert cur["state"] == "COMPLETED"
        tasks = d.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        used = d.execute("SELECT COUNT(*) FROM tokens WHERE used=1"
                         ).fetchone()[0]
    finally:
        d.close()
    assert tasks == 1  # one bound task, never duplicated
    assert used == 1  # fresh approval consumed exactly once


def test_cli_daemon_deny_settles_cancelled(tmp_path):
    _enqueue(tmp_path, legs=FORM_CHAIN)
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "deny") + ["--daemon", "--once", "--no"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _tasks(tmp_path)[0]["status"] == "CANCELLED"
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    from lakra.control import work_queue as q
    d = D(tmp_path / "cli.db")
    try:
        items = q.list_items(d)
        assert len(items) == 1
        assert items[0]["state"] == "CANCELLED"
        submits = sum(
            1 for line in (tmp_path / "audit-do.jsonl").read_text(
                encoding="utf-8").splitlines()
            if '"browser.submit"' in line
            and "EXECUTION_COMPLETED" in line)
        assert submits == 0  # denial held the submit
    finally:
        d.close()
