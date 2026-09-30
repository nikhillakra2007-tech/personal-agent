"""Cross-process approval regression tests (two real OS processes).

Process A runs lakra_do.py --road form ... --poll (live waiter);
Process B runs approve.py (external decider). Synchronization is on
DB/audit STATE (poll for ACTION_REQUIRES_APPROVAL + pending row),
never on sleep timings. No mocks, no in-process shortcuts: separate
interpreters, one shared database file, same as production use.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FORM = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
SLOTS = {"name": {"text": "Ada"}, "city": {"text": "Lagos"},
         "zip": {"text": "10001"}, "news": {"checked": True},
         "country": {"select": "ng"}}
GUARD_OFF = ["--ram-floor-mb", "0", "--cpu-ceiling", "100"]


def base(tmp_path, prof):
    return [PY, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(tmp_path / "a.jsonl"),
            "--profile-dir", str(tmp_path / prof),
            "--shots-dir", str(tmp_path / (prof + "-shots"))] + GUARD_OFF


def env(tmp_path):
    e = dict(os.environ)
    e["LAKRA_DB"] = str(tmp_path / "x.db")
    return e


def count_parks(tmp_path):
    try:
        lines = (tmp_path / "a.jsonl").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines if "ACTION_REQUIRES_APPROVAL" in line)


def wait_for_park(tmp_path, prior, timeout_s=150):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if count_parks(tmp_path) > prior:
            return True
        time.sleep(2)
    return False


def db_state(tmp_path):
    con = sqlite3.connect(str(tmp_path / "x.db"))
    try:
        return (
            con.execute(
                "SELECT task_id,status FROM tasks").fetchall(),
            con.execute(
                "SELECT approval_id,task_id,decided FROM approvals"
            ).fetchall(),
            con.execute(
                "SELECT token_id,approval_id,used FROM tokens").fetchall())
    finally:
        con.close()


def audit_types(tmp_path, type_):
    out = []
    for line in (tmp_path / "a.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            if e["type"] == type_:
                out.append(e)
    return out


def start_waiter(tmp_path, prof="prof"):
    prior = count_parks(tmp_path)
    proc = subprocess.Popen(
        base(tmp_path, prof) + [
            "--road", "form", "--url", FORM, "--slots-json",
            json.dumps(SLOTS), "--submit", "#m-submit", "--poll", "150"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env(tmp_path))
    assert wait_for_park(tmp_path, prior), "waiter never parked"
    return proc


def decide(tmp_path, flag):
    return subprocess.run(
        [PY, str(ROOT / "scripts" / "approve.py"), flag],
        capture_output=True, text=True, cwd=str(ROOT),
        env=env(tmp_path), timeout=120)


def test_cross_process_approve_completes(tmp_path):
    proc = start_waiter(tmp_path)
    assert proc.poll() is None, "waiter must stay alive for adoption"
    _, approvals, _ = db_state(tmp_path)
    live = [a for a in approvals if a[2] is None]
    assert len(live) == 1
    approval_id, task_id, _ = live[0]
    assert audit_types(tmp_path, "APPROVED_STEP_EXECUTED") == [], \
        "submit executed before approval"
    out = decide(tmp_path, "--yes")
    assert "approved" in out.stdout, out.stdout
    assert proc.wait(timeout=180) == 0, proc.stdout.read()
    tasks, approvals, tokens = db_state(tmp_path)
    assert dict(tasks)[task_id] == "COMPLETED"
    assert [a for a in approvals if a[0] == approval_id][0][2] == "approved"
    assert [t for t in tokens if t[1] == approval_id][0][2] == 1
    assert not [a for a in approvals if a[2] is None]
    assert len(audit_types(tmp_path, "APPROVAL_CONSUMED")) == 1
    assert len(audit_types(tmp_path, "APPROVED_STEP_EXECUTED")) == 1


def test_cross_process_deny_holds_submit(tmp_path):
    proc = start_waiter(tmp_path, prof="profd")
    assert proc.poll() is None
    out = decide(tmp_path, "--no")
    assert "denied" in out.stdout, out.stdout
    assert proc.wait(timeout=180) == 2, proc.stdout.read()
    tasks, approvals, _ = db_state(tmp_path)
    assert list(dict(tasks).values()) == ["CANCELLED"]
    assert audit_types(tmp_path, "APPROVAL_CONSUMED") == []
    assert audit_types(tmp_path, "APPROVED_STEP_EXECUTED") == []
    assert not [a for a in approvals if a[2] is None]


def test_cross_process_kill_then_approve_resume(tmp_path):
    proc = start_waiter(tmp_path, prof="profk")
    assert proc.poll() is None
    proc.terminate()
    proc.wait(timeout=60)
    out = decide(tmp_path, "--yes")
    assert "approved" in out.stdout, out.stdout
    tasks, _, _ = db_state(tmp_path)
    tids = [t for t in tasks if t[1] == "RUNNING"]
    assert len(tids) == 1
    res = subprocess.run(
        base(tmp_path, "profkr") + ["--resume", tids[0][0], "--yes"],
        capture_output=True, text=True, cwd=str(ROOT),
        env=env(tmp_path), timeout=240)
    assert res.returncode == 0 and "status: DONE" in res.stdout, \
        res.stdout + res.stderr


def test_approve_empty_reports_cleanly(tmp_path):
    out = decide(tmp_path, "--yes")
    assert "no pending approvals" in out.stdout
    assert out.returncode == 0
