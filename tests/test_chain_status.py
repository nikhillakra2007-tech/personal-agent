"""Slice-50: chain-leg visibility through --status (read-only).

chain_summary() derives per-leg status from existing persisted state
only: the task goal ("Chain of N legs"), the task-filtered audit
trail, and persisted plan hints. No writes, no new events, no new
state, no execution. --status renders a chain section (text + JSON)
for chain tasks; non-chain output is byte-identical.

Unit tests hand-build audit + plan rows (fast, deterministic edge
coverage). Integration tests drive real stub run_chain runs. CLI
smokes run real chains and query --status afterwards.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lakra_do
from lakra_do import (
    chain_summary,
    render_status,
    render_status_json,
    run_list,
    run_status,
)
from lakra.control import plan_store, task_store
from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.planner import Plan
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task

FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = (FIXTURES / "loop-index.html").as_uri()
CLICK_URL = (FIXTURES / "click.html").as_uri()


def make_chain_task(db_path, goal="Chain of 3 legs",
                    status=Status.RUNNING):
    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create(goal, allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        reg.checkout(t.task_id, "cli")
        if status != Status.QUEUED:
            reg.set_status(t.task_id, Status.RUNNING)
            if status != Status.RUNNING:
                reg.set_status(t.task_id, status)
        return t.task_id
    finally:
        db.close()


def save_plan(db_path, task_id, plan_id, hints):
    db = Database(db_path)
    try:
        plan_store.save_plan(
            db, Plan(plan_id=plan_id, task_id=task_id, goal="g",
                     steps=[], created_at="t", status="DRAFT"), hints)
    finally:
        db.close()


def log(audit_path, etype, task_id, payload):
    AuditLog(audit_path).log(etype, task_id, payload)


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


OBSERVE_HINTS = {"url": "file:///l.html", "expect_text": "Records"}
CLICK_HINTS = {"url": "file:///c.html", "selector": "#c-continue",
               "expect_text": "Welcome"}
FORM_HINTS = {"url": "file:///f.html",
              "fields": [{"selector": "#f", "text": "Ada"}],
              "submit_selector": "#s"}


def done_chain_state(tmp_path):
    """Hand-built COMPLETED 3-leg chain: observe/click/observe + lifecycle."""
    db_path = tmp_path / "c.db"
    audit_path = tmp_path / "c.jsonl"
    tid = make_chain_task(db_path)
    save_plan(db_path, tid, "p1", OBSERVE_HINTS)
    save_plan(db_path, tid, "p2", CLICK_HINTS)
    save_plan(db_path, tid, "p3", OBSERVE_HINTS)
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p1", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p1", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p2", "steps": 1})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p2", "status": "DONE", "steps_done": 1,
         "detail": "ok"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p3", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p3", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    log(audit_path, "TASK_LIFECYCLE", tid,
        {"chain_id": "abc123", "task_status": "COMPLETED"})
    return db_path, audit_path, tid


# -- chain_summary unit ----------------------------------------------------------

def test_summary_none_for_non_chain_task(tmp_path):
    db_path = tmp_path / "n.db"
    audit_path = tmp_path / "n.jsonl"
    tid = make_chain_task(db_path, goal="Do the thing")
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        assert chain_summary(db, task, []) is None
    finally:
        db.close()


def test_summary_none_for_malformed_chain_goals(tmp_path):
    db_path = tmp_path / "m.db"
    db = Database(db_path)
    try:
        for bad in ("Chain of legs", "Chain of 0 legs", "Chain of x legs",
                    "chain of 3 legs", "Chain of 3 legs "):
            t = Task.create(bad, allowed_tools=["browser"],
                            allowed_domains=["file:"],
                            allowed_paths=[])
            assert chain_summary(db, t, []) is None, bad
    finally:
        db.close()


def test_summary_done_chain(tmp_path):
    db_path, audit_path, tid = done_chain_state(tmp_path)
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        events = AuditLog(audit_path).replay()
        out = chain_summary(db, task, events)
    finally:
        db.close()
    assert out["chain_id"] == "abc123"
    assert out["total_legs"] == 3
    assert out["legs_done"] == 3
    assert out["legs"] == [
        {"index": 1, "road": "observe", "status": "DONE"},
        {"index": 2, "road": "click", "status": "DONE"},
        {"index": 3, "road": "observe", "status": "DONE"},
    ]


def test_summary_stopped_chain_unknown_chain_id(tmp_path):
    db_path = tmp_path / "s.db"
    audit_path = tmp_path / "s.jsonl"
    tid = make_chain_task(db_path, status=Status.RUNNING)
    save_plan(db_path, tid, "p1", OBSERVE_HINTS)
    save_plan(db_path, tid, "p2", CLICK_HINTS)
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p1", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p1", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p2", "steps": 1})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p2", "status": "STOPPED", "steps_done": 0,
         "detail": "no groundable click target"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["chain_id"] is None
    assert out["total_legs"] == 3
    assert out["legs_done"] == 1
    assert out["legs"] == [
        {"index": 1, "road": "observe", "status": "DONE"},
        {"index": 2, "road": "click", "status": "STOPPED"},
    ]


def test_summary_denied_form_leg(tmp_path):
    db_path = tmp_path / "d.db"
    audit_path = tmp_path / "d.jsonl"
    tid = make_chain_task(db_path, status=Status.CANCELLED)
    save_plan(db_path, tid, "p1", CLICK_HINTS)
    save_plan(db_path, tid, "p2", FORM_HINTS)
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p1", "steps": 1})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p1", "status": "DONE", "steps_done": 1,
         "detail": "ok"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p2", "steps": 3})
    log(audit_path, "ACTION_REQUIRES_APPROVAL", tid,
        {"kind": "browser.submit"})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p2", "status": "STOPPED", "steps_done": 2,
         "detail": "human denied approval"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["legs"][1] == {"index": 2, "road": "form",
                              "status": "STOPPED"}
    assert out["legs_done"] == 1


def test_summary_invalid_chain_has_no_legs(tmp_path):
    db_path = tmp_path / "i.db"
    audit_path = tmp_path / "i.jsonl"
    tid = make_chain_task(db_path, goal="Chain of 5 legs")
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "-", "status": "STOPPED", "steps_done": 0,
         "detail": "invalid chain (chain needs 2..4 legs, got 5)"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["total_legs"] == 5
    assert out["legs"] == [] and out["legs_done"] == 0
    assert out["chain_id"] is None


def test_summary_preplan_leg_refusal_unknown_road(tmp_path):
    db_path = tmp_path / "r.db"
    audit_path = tmp_path / "r.jsonl"
    tid = make_chain_task(db_path)
    save_plan(db_path, tid, "p1", OBSERVE_HINTS)
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p1", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p1", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "-", "status": "STOPPED", "steps_done": 0,
         "detail": "no groundable click target (empty clicks)"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["legs"] == [
        {"index": 1, "road": "observe", "status": "DONE"},
        {"index": 2, "road": "unknown", "status": "STOPPED"},
    ]


def test_summary_skips_superseded_replans(tmp_path):
    db_path = tmp_path / "p.db"
    audit_path = tmp_path / "p.jsonl"
    tid = make_chain_task(db_path, goal="Chain of 2 legs")
    save_plan(db_path, tid, "p1", OBSERVE_HINTS)
    save_plan(db_path, tid, "p2", OBSERVE_HINTS)
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p1", "steps": 2})
    log(audit_path, "PLAN_SUPERSEDED", tid,
        {"old_plan": "p1", "new_plan": "p2", "at_step": 0,
         "snapshot_chars": 10})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p2", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p2", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["legs"] == [
        {"index": 1, "road": "observe", "status": "DONE"}]


def test_summary_missing_plan_row_is_unknown_road(tmp_path):
    db_path = tmp_path / "u.db"
    audit_path = tmp_path / "u.jsonl"
    tid = make_chain_task(db_path, goal="Chain of 2 legs")
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "ghost", "steps": 1})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "ghost", "status": "DONE", "steps_done": 1,
         "detail": "ok"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["legs"] == [
        {"index": 1, "road": "unknown", "status": "DONE"}]


def test_summary_from_leg_rerun_overwrites_slot(tmp_path):
    # Run 1 stops at leg 2; rerun from leg 2 completes: latest wins.
    db_path = tmp_path / "f.db"
    audit_path = tmp_path / "f.jsonl"
    tid = make_chain_task(db_path)
    for pid, hints in (("p1", OBSERVE_HINTS), ("p2", CLICK_HINTS),
                       ("p2b", CLICK_HINTS), ("p3", OBSERVE_HINTS)):
        save_plan(db_path, tid, pid, hints)
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p1", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p1", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p2", "steps": 1})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p2", "status": "STOPPED", "steps_done": 0,
         "detail": "no groundable click target"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p2b", "steps": 1})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p2b", "status": "DONE", "steps_done": 1,
         "detail": "ok"})
    log(audit_path, "PLAN_CREATED", tid, {"plan_id": "p3", "steps": 2})
    log(audit_path, "PLAN_OUTCOME", tid,
        {"plan_id": "p3", "status": "DONE", "steps_done": 2,
         "detail": "ok"})
    log(audit_path, "TASK_LIFECYCLE", tid,
        {"chain_id": "rerun1", "task_status": "COMPLETED"})
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        out = chain_summary(db, task, AuditLog(audit_path).replay())
    finally:
        db.close()
    assert out["legs"] == [
        {"index": 1, "road": "observe", "status": "DONE"},
        {"index": 2, "road": "click", "status": "DONE"},
        {"index": 3, "road": "observe", "status": "DONE"},
    ]
    assert out["legs_done"] == 3 and out["chain_id"] == "rerun1"


# -- renderers -------------------------------------------------------------------

def test_render_status_chain_text_and_json(tmp_path):
    db_path, audit_path, tid = done_chain_state(tmp_path)
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        events = AuditLog(audit_path).replay()
        chain = chain_summary(db, task, events)
        text = render_status(task, "not resumable: test",
                             [], events, chain)
        assert "chain: abc123" in text
        assert "legs: 3/3 done" in text
        assert "1. observe - DONE" in text
        assert "2. click - DONE" in text
        assert "3. observe - DONE" in text
        parsed = json.loads(render_status_json(task, "not resumable",
                                               [], events, chain))
        assert parsed["chain"] == {
            "chain_id": "abc123", "total_legs": 3, "legs_done": 3,
            "legs": [{"index": 1, "road": "observe", "status": "DONE"},
                     {"index": 2, "road": "click", "status": "DONE"},
                     {"index": 3, "road": "observe", "status": "DONE"}]}
        # Deterministic: identical bytes on repeat render.
        assert render_status_json(task, "not resumable", [], events,
                                  chain) == render_status_json(
            task, "not resumable", [], events, chain)
    finally:
        db.close()


def test_render_status_non_chain_unchanged(tmp_path):
    db_path = tmp_path / "nc.db"
    tid = make_chain_task(db_path, goal="Do the thing")
    db = Database(db_path)
    try:
        task = task_store.load_task(db, tid)
        text = render_status(task, "not resumable: x", [], [])
        assert "chain:" not in text and "legs:" not in text
        parsed = json.loads(render_status_json(task, "not resumable",
                                               [], []))
        assert "chain" not in parsed
    finally:
        db.close()


def test_status_writes_nothing(tmp_path):
    db_path = tmp_path / "ro.db"
    audit_path = tmp_path / "ro.jsonl"
    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create("Chain of 2 legs",
                                allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        reg.checkout(t.task_id, "cli")
        reg.set_status(t.task_id, Status.RUNNING)
    finally:
        db.close()
    audit = AuditLog(audit_path)
    audit.log("PLAN_CREATED", t.task_id, {"plan_id": "p1", "steps": 2})
    audit.log("PLAN_OUTCOME", t.task_id,
              {"plan_id": "p1", "status": "DONE", "steps_done": 2,
               "detail": "ok"})
    before_db, before_audit = file_hash(db_path), file_hash(audit_path)
    assert run_status({"allow_domains": [], "status": t.task_id},
                      audit_path, db_path=db_path) == 0
    assert run_status({"allow_domains": [], "status": t.task_id,
                       "format": "json"}, audit_path,
                      db_path=db_path) == 0
    assert file_hash(db_path) == before_db
    assert file_hash(audit_path) == before_audit
    db = Database(db_path)
    try:
        assert task_store.load_task(db, t.task_id).status == \
            Status.RUNNING
    finally:
        db.close()


# -- CLI smoke ---------------------------------------------------------------------

def run_cli(tmp_path, *argv):
    import os
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / "cli.db")
    cmd = [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
           "--audit", str(tmp_path / "audit-do.jsonl"),
           "--profile-dir", str(tmp_path / "prof"),
           "--shots-dir", str(tmp_path / "shots"),
           "--ram-floor-mb", "0", "--cpu-ceiling", "100", *argv]
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(ROOT), env=env, timeout=240)


def write_chain(tmp_path, name, legs):
    path = tmp_path / name
    path.write_text(json.dumps({"legs": legs}), encoding="utf-8")
    return str(path)


def chain_task_id(tmp_path):
    out = run_cli(tmp_path, "--list", "--state", "all", "--format",
                  "json")
    assert out.returncode == 0, out.stdout + out.stderr
    tasks = json.loads(out.stdout)["tasks"]
    assert len(tasks) == 1
    return tasks[0]["task_id"]


def test_cli_chain_status_text_and_json(tmp_path):
    chain = write_chain(tmp_path, "chain.json", [
        {"road": "observe", "url": LIST_URL, "expect_text": "Records"},
        {"road": "click", "click_url": CLICK_URL,
         "click_text": "Continue", "expect_text": "Welcome"},
        {"road": "observe", "url": CLICK_URL,
         "expect_text": "Welcome"}])
    proc = run_cli(tmp_path, "--chain-file", chain, "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tid = chain_task_id(tmp_path)
    proc = run_cli(tmp_path, "--status", tid)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "chain:" in proc.stdout
    assert "legs: 3/3 done" in proc.stdout
    assert "2. click - DONE" in proc.stdout
    proc = run_cli(tmp_path, "--status", tid, "--format", "json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    parsed = json.loads(proc.stdout)
    assert parsed["chain"]["total_legs"] == 3
    assert parsed["chain"]["legs_done"] == 3
    assert [leg["road"] for leg in parsed["chain"]["legs"]] == \
        ["observe", "click", "observe"]
    assert parsed["chain"]["chain_id"]
    assert all(leg["status"] == "DONE"
               for leg in parsed["chain"]["legs"])


def test_cli_stopped_chain_status(tmp_path):
    chain = write_chain(tmp_path, "chain.json", [
        {"road": "observe", "url": LIST_URL, "expect_text": "Records"},
        {"road": "click", "click_url": CLICK_URL,
         "click_text": "Launch rocket", "expect_text": "Space"}])
    proc = run_cli(tmp_path, "--chain-file", chain, "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    tid = chain_task_id(tmp_path)
    proc = run_cli(tmp_path, "--status", tid)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "legs: 1/2 done" in proc.stdout
    assert "1. observe - DONE" in proc.stdout
    # The click leg refused pre-plan (no plan row exists), so its
    # road is honestly unknown rather than invented.
    assert "2. unknown - STOPPED" in proc.stdout
