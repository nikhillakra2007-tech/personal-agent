"""Slice-41 V1 demo + closeout driver (self-checking runbook runner).

Executes docs/demo-v1.md end to end on fixtures with an isolated
workdir and asserts every act (exit codes, output markers, audit
replay equality). Harness only: it shells out to the frozen CLI and
approve.py, implements no product logic, and changes no repo state
outside its workdir (printed at start; kept for inspection).

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\demo_v1.py [--workdir PATH]

Exit 0 iff every act PASSes; aborts on the first failed assertion.
Expected runtime: under ~10 minutes on the reference host.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

LOOP_INDEX = (ROOT / "tests" / "fixtures" / "loop-index.html").as_uri()
FORM_PAGE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
BASELINE_TESTS = 514

FORM_SLOTS = {"name": {"text": "Ada"}, "city": {"text": "Lagos"},
              "zip": {"text": "10001"}, "news": {"checked": True},
              "country": {"select": "ng"}}

RESULTS: list = []


def check(act, name, cond, detail=""):
    RESULTS.append((act, name, bool(cond), detail))
    print(f"[{act}] {'PASS' if cond else 'FAIL'}: {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        print(f"[{act}] ABORTING demo on first failure.")
        raise SystemExit(1)


def cli_base(workdir, prof):
    return [PY, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(workdir / "audit.jsonl"),
            "--profile-dir", str(workdir / prof),
            "--shots-dir", str(workdir / (prof + "-shots")),
            "--ram-floor-mb", "0", "--cpu-ceiling", "100"]


def run_cli(workdir, prof, *argv, timeout=240):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(workdir / "demo.db")
    return subprocess.run(cli_base(workdir, prof) + list(argv),
                          capture_output=True, text=True, cwd=str(ROOT),
                          env=env, timeout=timeout)


def run_approve(workdir, flag, timeout=120):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(workdir / "demo.db")
    return subprocess.run(
        [PY, str(ROOT / "scripts" / "approve.py"), flag],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=timeout)


def task_id_with_status(workdir, status):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(workdir / "demo.db")
    proc = subprocess.run(
        cli_base(workdir, "prof-q") + ["--list", "--format", "json"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tasks = json.loads(proc.stdout)["tasks"]
    return [t["task_id"] for t in tasks if t["status"] == status]


def count_parks(workdir):
    try:
        lines = (workdir / "audit.jsonl").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines if "ACTION_REQUIRES_APPROVAL" in line)


def wait_for_park(workdir, prior, timeout_s=150):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if count_parks(workdir) > prior:
            return True
        time.sleep(2)
    return False


def read_events(workdir):
    events = []
    for line in (workdir / "audit.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def main(argv):
    workdir = Path(argv[0]) if argv else Path(
        tempfile.mkdtemp(prefix="lakra-demo-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"demo workdir: {workdir}")

    # -- Act 0: setup baseline -------------------------------------------
    proc = subprocess.run(
        [PY, "-m", "compileall", "src", "tests", "scripts"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    check("act0", "compileall clean", proc.returncode == 0,
          proc.stderr[-500:] if proc.returncode else "")
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--co"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    check("act0", f"baseline collects {BASELINE_TESTS} tests",
          f"{BASELINE_TESTS} tests collected" in proc.stdout,
          proc.stdout[-300:] if proc.returncode else
          [l for l in proc.stdout.splitlines() if "collected" in l])

    # -- Act 1: follow DONE ------------------------------------------------
    proc = run_cli(workdir, "prof-a1", "--road", "follow",
                   "--url", LOOP_INDEX, "--text", "Follow the Beta record",
                   "--expect", "detail record: Beta", "--yes")
    check("act1", "follow exit 0 DONE/4",
          proc.returncode == 0 and "status: DONE" in proc.stdout
          and "steps: 4" in proc.stdout, proc.stdout + proc.stderr)

    # -- Act 2: form parks; visibility narrates -----------------------------
    parked = subprocess.Popen(
        cli_base(workdir, "prof-a2") + [
            "--road", "form", "--url", FORM_PAGE, "--slots-json",
            json.dumps(FORM_SLOTS), "--submit", "#m-submit",
            "--poll", "150"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT),
        env={**os.environ, "LAKRA_DB": str(workdir / "demo.db")})
    prior_parks = count_parks(workdir)
    try:
        check("act2", "form parks at the L3 gate",
              wait_for_park(workdir, prior_parks), "no ACTION_REQUIRES_APPROVAL seen")
        listed = run_cli(workdir, "prof-q", "--list")
        check("act2", "list shows WAITING_APPROVAL",
              listed.returncode == 0
              and "WAITING_APPROVAL" in listed.stdout,
              listed.stdout + listed.stderr)
        waiting = task_id_with_status(workdir, "WAITING_APPROVAL")
        check("act2", "exactly one parked task", len(waiting) == 1,
              str(waiting))
        shown = run_cli(workdir, "prof-q", "--status", waiting[0],
                         "--format", "json")
        detail = json.loads(shown.stdout)
        check("act2", "JSON status names pending, not resumable",
              shown.returncode == 0
              and detail["resume"]["resumable"] is False
              and len(detail["pending_approvals"]) == 1,
              shown.stdout + shown.stderr)
    finally:
        parked.terminate()
        try:
            parked.wait(timeout=60)
        except subprocess.TimeoutExpired:
            parked.kill()
    form_tid = waiting[0]

    # -- Act 3a: approve, resume, DONE ---------------------------------------
    approved = run_approve(workdir, "--yes")
    check("act3", "approve.py approves cross-process",
          "approved" in approved.stdout, approved.stdout)
    resumed = run_cli(workdir, "prof-a3", "--resume", form_tid, "--yes")
    check("act3", "resume completes to DONE/7 submitted",
          resumed.returncode == 0 and "status: DONE" in resumed.stdout
          and "steps: 7" in resumed.stdout,
          resumed.stdout + resumed.stderr)

    # -- Act 3b: deny twin -----------------------------------------------------
    parked = subprocess.Popen(
        cli_base(workdir, "prof-b2") + [
            "--road", "form", "--url", FORM_PAGE, "--slots-json",
            json.dumps({"name": {"text": "Ada"}}),
            "--submit", "#m-submit", "--poll", "150"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT),
        env={**os.environ, "LAKRA_DB": str(workdir / "demo.db")})
    prior_parks = count_parks(workdir)
    try:
        check("act3", "second form parks", wait_for_park(workdir,
                                                         prior_parks),
              "no second ACTION_REQUIRES_APPROVAL seen")
        deny_tid = task_id_with_status(workdir, "WAITING_APPROVAL")
        check("act3", "exactly one parked task", len(deny_tid) == 1,
              str(deny_tid))
        deny_tid = deny_tid[0]
        early = run_cli(workdir, "prof-q", "--resume", deny_tid, "--yes")
        check("act3", "resume-while-pending refuses naming the id",
              early.returncode == 1
              and "undecided approval" in early.stdout,
              early.stdout + early.stderr)
    finally:
        parked.terminate()
        try:
            parked.wait(timeout=60)
        except subprocess.TimeoutExpired:
            parked.kill()
    denied = run_approve(workdir, "--no")
    check("act3", "approve.py denies cross-process",
          "denied" in denied.stdout, denied.stdout)
    held = run_cli(workdir, "prof-b3", "--resume", deny_tid, "--yes")
    check("act3", "denied task is terminally cancelled (immutable)",
          held.returncode == 1 and "history immutable" in held.stdout,
          held.stdout + held.stderr)
    cancelled = run_cli(workdir, "prof-q", "--list", "--state",
                        "CANCELLED")
    check("act3", "cancelled task listed as terminal",
          cancelled.returncode == 0 and deny_tid in cancelled.stdout,
          cancelled.stdout + cancelled.stderr)
    # Denial AT the gate (fresh run, decider says no): the exit-2 path.
    refused = run_cli(workdir, "prof-b4", "--road", "form", "--url",
                      FORM_PAGE, "--slots-json",
                      json.dumps({"name": {"text": "Ada"}}),
                      "--submit", "#m-submit", "--no")
    check("act3", "live gate denial holds the submit",
          refused.returncode == 2 and "denied" in refused.stdout,
          refused.stdout + refused.stderr)

    # -- Act 3c: guard pause twin (AC#4 mechanism) -------------------------------
    env = dict(os.environ)
    env["LAKRA_DB"] = str(workdir / "demo.db")
    base = [PY, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(workdir / "audit.jsonl")]
    paused = subprocess.run(
        base + ["--profile-dir", str(workdir / "prof-c"),
                "--shots-dir", str(workdir / "prof-c-shots"),
                "--road", "loop", "--url", LOOP_INDEX,
                "--text", "Follow every batch record",
                "--expect", "detail record",
                "--max-items", "3", "--max-iters", "3", "--yes",
                "--ram-floor-mb", "999999"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=240)
    check("act3", "pressure pauses the loop",
          paused.returncode == 1 and "status: PAUSED" in paused.stdout,
          paused.stdout + paused.stderr)
    loop_tid = task_id_with_status(workdir, "PAUSED")
    check("act3", "exactly one paused task", len(loop_tid) == 1,
          str(loop_tid))
    relieved = subprocess.run(
        base + ["--profile-dir", str(workdir / "prof-c2"),
                "--shots-dir", str(workdir / "prof-c2-shots"),
                "--ram-floor-mb", "512",
                "--resume", loop_tid[0], "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=240)
    check("act3", "relief resumes to DONE with all findings",
          relieved.returncode == 0 and "status: DONE" in relieved.stdout
          and "Alpha record" in relieved.stdout
          and "Gamma record" in relieved.stdout,
          relieved.stdout + relieved.stderr)

    # -- Act 4: audit replay -------------------------------------------------------
    live = read_events(workdir)
    check("act4", "audit log non-empty", len(live) > 50, str(len(live)))
    check("act4", "fresh re-read equals live trail",
          read_events(workdir) == live)
    types = [e["type"] for e in live]
    for required in ("PLAN_CREATED", "ACTION_REQUIRES_APPROVAL",
                     "APPROVAL_CONSUMED", "APPROVED_STEP_EXECUTED",
                     "PLAN_OUTCOME", "LOOP_START", "LOOP_END",
                     "LOOP_ITERATION"):
        check("act4", f"audit contains {required}", required in types)
    listed = run_cli(workdir, "prof-q", "--list", "--state", "all")
    check("act4", "terminal states visible",
          listed.returncode == 0 and "COMPLETED" in listed.stdout
          and "CANCELLED" in listed.stdout,
          listed.stdout + listed.stderr)

    print(f"\nDEMO PASS: {len(RESULTS)} checks across 5 acts.")
    print(f"workdir kept for inspection: {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
