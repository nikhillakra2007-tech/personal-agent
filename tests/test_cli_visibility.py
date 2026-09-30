"""Slice-39: read-only task visibility.

--list/--status compose existing readers (task_store, approvals
pending, resume_info, AuditLog.replay) into human answers for NFR-3.
Pure reads: DB/audit bytes identical afterwards, no browser launched,
no state decided. No src/ changes; approve.py / lakra_run.py and all
existing lakra_do flows untouched."""

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
    EVENT_KEYS,
    event_to_json,
    render_list,
    render_list_json,
    render_status,
    render_status_json,
    resume_to_json,
    run_list,
    run_status,
    short_goal,
    summarize_event,
    task_to_json,
)
from lakra.control import task_store
from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.store import Database
from lakra.control.tasks import Status, Task

LOOP_INDEX = (Path(__file__).parent / "fixtures" / "loop-index.html").as_uri()


def make_task(db_path, goal="Do the thing", status=Status.RUNNING,
              owner="cli"):
    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        t = reg.add(Task.create(goal, allowed_tools=["browser"],
                                allowed_domains=["file:"],
                                allowed_paths=[]))
        if owner is not None:
            reg.checkout(t.task_id, owner)
        if status != Status.QUEUED:
            reg.set_status(t.task_id, Status.RUNNING)
            if status != Status.RUNNING:
                reg.set_status(t.task_id, status)
        return t.task_id
    finally:
        db.close()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


# -- renderers ---------------------------------------------------------------------

def test_short_goal_truncates():
    assert short_goal("  hi   there ") == "hi there"
    long_goal = "x" * 200
    assert short_goal(long_goal) == "x" * 77 + "..."
    assert len(short_goal(long_goal)) == 80


def test_summarize_event_allowlists_keys():
    e = {"ts": "T", "type": "PLAN_OUTCOME",
         "payload": {"plan_id": "p", "status": "DONE", "steps_done": 2,
                     "hints": {"selector": "#s"}, "fields": ["f"],
                     "token_id": "secret", "detail": "y" * 100}}
    out = summarize_event(e)
    assert out.startswith("T PLAN_OUTCOME")
    for safe in ("plan_id=p", "status=DONE", "steps_done=2"):
        assert safe in out
    for unsafe in ("hints", "fields", "secret", "#s"):
        assert unsafe not in out
    assert out.endswith("...")


def test_render_list_orders_and_empty():
    assert render_list([]) == "no tasks"


def test_state_validation():
    assert run_list({"allow_domains": [], "state": "bogus"}) == 1
    assert run_list({"allow_domains": [], "state": "completed",
                     "list": True}, db_path=":memory:") == 0


def test_last_validation(tmp_path):
    tid = make_task(tmp_path / "v.db")
    assert run_status({"allow_domains": [], "status": tid, "last": "0"},
                      tmp_path / "a.jsonl",
                      db_path=tmp_path / "v.db") == 1
    assert run_status({"allow_domains": [], "status": tid,
                       "last": "many"}, tmp_path / "a.jsonl",
                      db_path=tmp_path / "v.db") == 1


# -- read-only proof ------------------------------------------------------------------

def test_visibility_writes_nothing(tmp_path):
    db_path = tmp_path / "ro.db"
    audit_path = tmp_path / "ro.jsonl"
    tid = make_task(db_path, goal="Submit the thing")
    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        reg.load_all()
        app = Approvals(reg, store=db)
        app.request(tid, Action(kind="browser.submit", target="#s",
                                effect="consequential", task_id=tid))
    finally:
        db.close()
    audit = AuditLog(audit_path)
    audit.log("PLAN_CREATED", tid, {"plan_id": "p", "steps": 1})
    before_db, before_audit = file_hash(db_path), file_hash(audit_path)
    assert run_list({"allow_domains": []}, db_path=db_path) == 0
    assert run_list({"allow_domains": [], "state": "all"},
                    db_path=db_path) == 0
    assert run_status({"allow_domains": [], "status": tid}, audit_path,
                      db_path=db_path) == 0
    assert run_status({"allow_domains": [], "status": "nope"}, audit_path,
                      db_path=db_path) == 1
    assert file_hash(db_path) == before_db
    assert file_hash(audit_path) == before_audit


# -- composition -------------------------------------------------------------------------

def test_status_narrates_parked_then_resumable(tmp_path, capsys):
    db_path = tmp_path / "c.db"
    audit_path = tmp_path / "c.jsonl"
    tid = make_task(db_path, goal="Submit the thing")
    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        reg.load_all()
        app = Approvals(reg, store=db)
        apv = app.request(tid, Action(
            kind="browser.submit", target="#s", effect="consequential",
            task_id=tid))
        from lakra.control import plan_store
        from lakra.control.planner import Planner
        task = reg.get(tid)
        plan = Planner().plan(task, {"url": "file:///f.html",
                                     "selector": "#s", "text": "x",
                                     "submit_selector": "#s"})
        plan_store.save_plan(db, plan, {})
    finally:
        db.close()
    assert run_status({"allow_domains": [], "status": tid}, audit_path,
                      db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "WAITING_APPROVAL" in out
    assert apv.approval_id in out  # pending id named
    assert "not resumable: " in out and "undecided approval" in out

    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        reg.load_all()
        Approvals(reg, store=db).decide(apv.approval_id, True)
    finally:
        db.close()
    assert run_status({"allow_domains": [], "status": tid}, audit_path,
                      db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "pending approvals: none" in out


def test_list_filters(capsys, tmp_path):
    db_path = tmp_path / "f.db"
    make_task(db_path, goal="running job", status=Status.RUNNING)
    make_task(db_path, goal="done job", status=Status.COMPLETED)
    assert run_list({"allow_domains": []}, db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "running job" in out and "done job" not in out
    assert run_list({"allow_domains": [], "state": "COMPLETED"},
                    db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "done job" in out and "running job" not in out
    assert run_list({"allow_domains": [], "state": "all"},
                    db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "running job" in out and "done job" in out


# -- live smokes (subprocess) -----------------------------------------------------------------

def run_cli(tmp_path, *argv, db="cli.db"):
    import os
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / db)
    cmd = [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
           "--audit", str(tmp_path / "audit-do.jsonl"),
           "--profile-dir", str(tmp_path / "prof"),
           "--shots-dir", str(tmp_path / "shots"),
           "--ram-floor-mb", "0", "--cpu-ceiling", "100", *argv]
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(ROOT), env=env, timeout=240)


def test_cli_done_visible_via_list_and_status(tmp_path):
    done = run_cli(tmp_path, "--road", "follow", "--url", LOOP_INDEX,
                   "--text", "Follow the Beta record",
                   "--expect", "detail record: Beta", "--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    listed = run_cli(tmp_path, "--list", "--state", "COMPLETED")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "COMPLETED" in listed.stdout
    assert "Beta record" in listed.stdout
    tid = listed.stdout.split()[0]
    assert len(tid) == 32  # full id printed: feeds --status/--resume
    shown = run_cli(tmp_path, "--status", tid)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert "COMPLETED" in shown.stdout
    assert "not resumable: " in shown.stdout  # terminal history immutable


def test_cli_status_unknown_id(tmp_path):
    out = run_cli(tmp_path, "--status", "nope")
    assert out.returncode == 1
    assert "unknown task" in out.stdout


# -- slice-40: machine-readable visibility ------------------------------------------

TASK_JSON_KEYS = {"task_id", "goal", "status", "owner", "allowed_tools",
                  "allowed_domains", "budget", "created_at", "updated_at"}


def test_json_schemas_closed(tmp_path, capsys):
    db_path = tmp_path / "j.db"
    audit_path = tmp_path / "j.jsonl"
    tid = make_task(db_path, goal="Submit the thing")
    assert run_list({"allow_domains": [], "format": "json"},
                    db_path=db_path) == 0
    listed = json.loads(capsys.readouterr().out)
    assert set(listed) == {"tasks"}
    assert len(listed["tasks"]) == 1
    assert set(listed["tasks"][0]) == TASK_JSON_KEYS
    assert listed["tasks"][0]["task_id"] == tid  # full id, feeds --resume
    assert set(listed["tasks"][0]["budget"]) == {"max_steps",
                                                 "max_tokens_cents",
                                                 "max_minutes"}
    assert run_status({"allow_domains": [], "status": tid,
                       "format": "json"}, audit_path,
                      db_path=db_path) == 0
    shown = json.loads(capsys.readouterr().out)
    assert set(shown) == {"task", "resume", "pending_approvals",
                          "audit_tail"}
    assert set(shown["task"]) == TASK_JSON_KEYS
    assert shown["resume"]["resumable"] is False  # fresh task, no unit
    assert "reason" in shown["resume"]
    assert shown["pending_approvals"] == []
    assert shown["audit_tail"] == []


def test_json_blocked_shapes(tmp_path, capsys):
    db_path = tmp_path / "jb.db"
    audit_path = tmp_path / "jb.jsonl"
    tid = make_task(db_path, goal="Submit the thing")
    db = Database(db_path)
    try:
        reg = TaskRegistry(store=db)
        reg.load_all()
        app = Approvals(reg, store=db)
        apv = app.request(tid, Action(
            kind="browser.submit", target="#s", effect="consequential",
            task_id=tid))
        from lakra.control import plan_store
        from lakra.control.planner import Planner
        plan = Planner().plan(reg.get(tid), {"url": "file:///f.html",
                                             "selector": "#s", "text": "x",
                                             "submit_selector": "#s"})
        plan_store.save_plan(db, plan, {})
    finally:
        db.close()
    assert run_status({"allow_domains": [], "status": tid,
                       "format": "json"}, audit_path,
                      db_path=db_path) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["resume"] == {"resumable": False,
                               "reason": shown["resume"]["reason"]}
    assert "undecided approval" in shown["resume"]["reason"]
    assert shown["pending_approvals"] == [apv.approval_id]


def test_json_parity_with_text(tmp_path, capsys):
    db_path = tmp_path / "p.db"
    audit_path = tmp_path / "p.jsonl"
    tid = make_task(db_path, goal="Parity job")
    assert run_list({"allow_domains": []}, db_path=db_path) == 0
    text = capsys.readouterr().out
    assert run_list({"allow_domains": [], "format": "json"},
                    db_path=db_path) == 0
    data = json.loads(capsys.readouterr().out)
    assert [t["task_id"] for t in data["tasks"]] == [tid]
    assert tid[:8] in text and "Parity job" in text
    assert run_status({"allow_domains": [], "status": tid}, audit_path,
                      db_path=db_path) == 0
    text = capsys.readouterr().out
    assert run_status({"allow_domains": [], "status": tid,
                       "format": "json"}, audit_path,
                      db_path=db_path) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task"]["task_id"] == tid
    assert ("COMPLETED" in text) == (data["task"]["status"]
                                     == "COMPLETED")


def test_json_redaction():
    evil = {"ts": "T", "type": "PLAN_OUTCOME",
            "payload": {"plan_id": "p", "status": "STOPPED",
                        "token_id": "tok-secret", "hints": {"a": "b"},
                        "fields": [{"selector": "#s", "text": "pw"}],
                        "slots": {"x": 1}, "text": "fillme",
                        "detail": "d" * 100, "steps_done": 3,
                        "nested": {"a": 1}}}
    out = event_to_json(evil)
    assert set(out) == {"ts", "type", "payload"}
    for leaked in ("token_id", "hints", "fields", "slots", "text",
                   "nested", "tok-secret", "fillme", "#s"):
        assert leaked not in json.dumps(out)
    assert out["payload"]["plan_id"] == "p"
    assert out["payload"]["steps_done"] == 3  # scalars keep types
    assert out["payload"]["detail"].endswith("...")
    blob = json.dumps(resume_to_json({
        "road": "single", "plan_id": "p", "plan_status": "EXECUTING",
        "first_open": 0, "steps": 2, "url": "file:///f.html",
        "detail": "plan p from step 0 of 2",
        "pending_approvals": [], "uncertain": [],
        "token_id": "tok-secret", "hints": {"a": "b"}}))
    assert "token" not in blob and "hint" not in blob


def test_json_validation(capsys):
    assert run_list({"allow_domains": [], "format": "yaml"}) == 1
    assert run_status({"allow_domains": [], "status": "x",
                       "format": "yaml"}, "nowhere.jsonl") == 1
    out = capsys.readouterr().out
    assert "usage error: --format must be one of text|json" in out
    assert lakra_do.main(["--format", "json", "--road", "follow",
                          "--url", "u", "--text", "t",
                          "--expect", "e"]) == 1
    out = capsys.readouterr().out
    assert "--format applies only to --list/--status" in out


def test_json_unknown_id_shape(capsys, tmp_path):
    assert run_status({"allow_domains": [], "status": "nope",
                       "format": "json"}, tmp_path / "a.jsonl",
                      db_path=tmp_path / "u.db") == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"error": "unknown task nope"}


def test_json_writes_nothing(tmp_path):
    db_path = tmp_path / "rj.db"
    audit_path = tmp_path / "rj.jsonl"
    tid = make_task(db_path)
    audit = AuditLog(audit_path)
    audit.log("PLAN_CREATED", tid, {"plan_id": "p", "steps": 1})
    before_db, before_audit = file_hash(db_path), file_hash(audit_path)
    assert run_list({"allow_domains": [], "format": "json"},
                    db_path=db_path) == 0
    assert run_status({"allow_domains": [], "status": tid,
                       "format": "json"}, audit_path,
                      db_path=db_path) == 0
    assert file_hash(db_path) == before_db
    assert file_hash(audit_path) == before_audit


def test_cli_json_done_visible(tmp_path):
    done = run_cli(tmp_path, "--road", "follow", "--url", LOOP_INDEX,
                   "--text", "Follow the Beta record",
                   "--expect", "detail record: Beta", "--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    listed = run_cli(tmp_path, "--list", "--state", "COMPLETED",
                     "--format", "json")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    data = json.loads(listed.stdout)
    assert len(data["tasks"]) == 1
    tid = data["tasks"][0]["task_id"]
    shown = run_cli(tmp_path, "--status", tid, "--format", "json")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    data = json.loads(shown.stdout)
    assert data["task"]["status"] == "COMPLETED"
    assert data["resume"]["resumable"] is False
    assert "immutable" in data["resume"]["reason"]
