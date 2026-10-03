"""V2-04: read-only table extraction road.

table inventory (collect_tables, unchanged) -> deterministic grounding
(tables.ground_table: unique winner or refusal) -> read-only
extraction (tables.extract_table: headers/rows/order/empties/shape,
JSON-serializable) -> expected-content verification inside the
extracted cells (never "a table exists") -> a directly-built
navigate + snapshot plan through TaskLoop.run_plan (the
LoopRunner._establish precedent: no planner/analyzer/template moves,
so no routing can hijack or be hijacked).

Live tests run the real --road table CLI on fresh locally-authored
fixtures (tables-*.html), including ambiguous/no-match/mismatch/
credential refusals, the JSON output line, chain-file legs, and the
queue -> daemon -> table-road proof. NL table shaping stays deferred:
shape_goal/decompose never emit table keys (asserted, unchanged).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import work_queue as wq  # noqa: E402
from lakra.control.loops import (  # noqa: E402
    TABLE_KEYS,
    run_table_task,
    run_task,
)
from lakra.control.planner import UnknownGoalError, validate_hints  # noqa: E402
from lakra.control.tables import (  # noqa: E402
    extract_table,
    ground_table,
    table_holds_secret,
    verify_expected,
)

FIXTURES = Path(__file__).parent / "fixtures"
COURSES = (FIXTURES / "tables-courses.html").as_uri()
TWO = (FIXTURES / "tables-two.html").as_uri()
AMBIG = (FIXTURES / "tables-ambiguous.html").as_uri()
SIMILAR = (FIXTURES / "tables-similar.html").as_uri()
EMPTY = (FIXTURES / "tables-empty.html").as_uri()
CREDS = (FIXTURES / "tables-credentials.html").as_uri()

# Hand-built inventory in the exact collect_tables shape:
# one (total_rows, rows) entry per table, header rows *starred*.
COURSES_INV = [(4, (("*Course*", "*Seats*", "*Room*"),
                    ("Linear Algebra", "30", "Hall A"),
                    ("Thermodynamics", "25", "Hall B"),
                    ("Cartography", "", "Hall C")))]
TWO_INV = [(2, (("*Course*", "*Seats*"),
                ("Linear Algebra", "30"),
                ("Thermodynamics", "25"))),
           (2, (("*Room*", "*Capacity*"),
                ("Hall A", "120"),
                ("Hall B", "80")))]


class _Audit:
    def __init__(self):
        self.events = []

    def log(self, etype, task_id, payload):
        self.events.append((etype, task_id, payload))


def _loop():
    return SimpleNamespace(runner=SimpleNamespace(audit=_Audit()))


def _task(tid="t-table"):
    return SimpleNamespace(task_id=tid, goal="Extract table courses")


# -- grounding ---------------------------------------------------------------

def test_ground_unique_single_table():
    assert ground_table("course seats room catalog", COURSES_INV) == 0


def test_ground_unique_of_two():
    assert ground_table("room capacity hall", TWO_INV) == 1
    assert ground_table("course seats algebra", TWO_INV) == 0


def test_ground_no_match_refuses():
    with pytest.raises(UnknownGoalError):
        ground_table("qzx nothing matches", COURSES_INV)


def test_ground_ambiguous_refuses():
    amb = [(1, (("Alpha Beta",),)), (1, (("Alpha Beta",),))]
    with pytest.raises(UnknownGoalError):
        ground_table("alpha beta", amb)


def test_ground_malformed_refuses():
    with pytest.raises(UnknownGoalError):
        ground_table("course", [])
    with pytest.raises(UnknownGoalError):
        ground_table("course", None)
    with pytest.raises(UnknownGoalError):
        ground_table("course", [(1, (None,))])
    with pytest.raises(UnknownGoalError):
        ground_table("course", [(1, ((object(),),))])
    with pytest.raises(UnknownGoalError):
        ground_table("   ", COURSES_INV)
    with pytest.raises(UnknownGoalError):
        ground_table("the and of", COURSES_INV)
    with pytest.raises(UnknownGoalError):
        ground_table("operator password list", COURSES_INV)


# -- extraction ----------------------------------------------------------------

def test_extract_headers_rows_shape():
    out = extract_table(COURSES_INV, 0)
    assert out["table_index"] == 0 and out["total_tables"] == 1
    assert out["headers"] == ["Course", "Seats", "Room"]
    assert out["rows"][0] == ["Course", "Seats", "Room"]
    assert out["rows"][1] == ["Linear Algebra", "30", "Hall A"]
    assert out["shape"] == [4, 3]
    json.dumps(out)  # serializable


def test_extract_empty_cells_preserved():
    out = extract_table(COURSES_INV, 0)
    assert out["rows"][3] == ["Cartography", "", "Hall C"]
    assert out["rows"][3][1] == ""


def test_extract_row_ordering():
    out = extract_table(TWO_INV, 1)
    assert [r[0] for r in out["rows"]] == ["Room", "Hall A", "Hall B"]
    assert out["headers"] == ["Room", "Capacity"]


def test_extract_no_header_row():
    inv = [(2, (("Ada", "36"), ("Bob", "41")))]
    out = extract_table(inv, 0)
    assert out["headers"] == [] and len(out["rows"]) == 2


def test_extract_bad_index_refuses():
    with pytest.raises(UnknownGoalError):
        extract_table(COURSES_INV, 5)
    with pytest.raises(UnknownGoalError):
        extract_table(COURSES_INV, -1)


def test_extract_serialization_round_trip():
    out = extract_table(TWO_INV, 1)
    assert json.loads(json.dumps(out, sort_keys=True)) == out


# -- verification ---------------------------------------------------------------

def test_verify_expected_present():
    verify_expected(extract_table(COURSES_INV, 0), "Linear Algebra")
    verify_expected(extract_table(COURSES_INV, 0), "Hall")
    verify_expected(extract_table(COURSES_INV, 0), "Course")


def test_verify_expected_missing_refuses():
    with pytest.raises(UnknownGoalError):
        verify_expected(extract_table(COURSES_INV, 0), "Zebra")
    # Present on NO table here: must not read as verified.
    with pytest.raises(UnknownGoalError):
        verify_expected(extract_table(TWO_INV, 1), "Thermodynamics")


def test_verify_malformed_expectation_refuses():
    with pytest.raises(UnknownGoalError):
        verify_expected(extract_table(COURSES_INV, 0), "   ")
    with pytest.raises(UnknownGoalError):
        verify_expected(extract_table(COURSES_INV, 0), "hunter2 password")


# -- credential safety ------------------------------------------------------------

def test_secret_scan_and_secret_content_refuse():
    assert table_holds_secret(extract_table(COURSES_INV, 0)) is False
    creds = [(2, (("*Service*", "*Login*"),
                   ("ledger", "clerk"),
                   ("vault", "hunter2 password")))]
    assert table_holds_secret(extract_table(creds, 0)) is True
    loop = _loop()
    res = run_table_task(
        loop, lambda u: None, lambda: creds, _task(),
        {"table_url": "file:///x.html", "table_text": "service login",
         "expect_text": "clerk"}, SimpleNamespace())
    assert res.status == "STOPPED" and "credential" in res.detail
    assert getattr(res, "findings", None) is None
    assert loop.runner.audit.events[0][0] == "PLAN_OUTCOME"


# -- composed entry pre-plan refusals (no browser) ----------------------------------

def test_road_malformed_goal_and_missing_seam():
    loop = _loop()
    bad = run_table_task(loop, lambda u: None, lambda: COURSES_INV,
                         _task(), {"table_url": "file:///x.html"},
                         SimpleNamespace())
    assert bad.status == "STOPPED" and "missing" in bad.detail
    assert bad.plan_id == "-"
    no_seam = run_table_task(loop, lambda u: None, None, _task(),
                             {"table_url": "file:///x.html",
                              "table_text": "course seats",
                              "expect_text": "Ada"}, SimpleNamespace())
    assert no_seam.status == "STOPPED" and "seam" in no_seam.detail
    secret = run_table_task(loop, lambda u: None, lambda: COURSES_INV,
                            _task(),
                            {"table_url": "file:///x.html",
                             "table_text": "operator password list",
                             "expect_text": "Ada"}, SimpleNamespace())
    assert secret.status == "STOPPED"


def test_road_grounding_refusals_carry_no_findings():
    loop = _loop()
    opened = []
    res = run_table_task(loop, opened.append, lambda: TWO_INV, _task(),
                         {"table_url": "file:///t.html",
                          "table_text": "course room",
                          "expect_text": "Ada"}, SimpleNamespace())
    # "course room" scores 1-1 on the two tables: ambiguity refuses.
    assert res.status == "STOPPED" and "ambiguous" in res.detail
    assert opened == ["file:///t.html"]  # page opened, nothing executed
    assert getattr(res, "findings", None) is None


# -- dispatcher --------------------------------------------------------------------

def test_dispatcher_routes_table_keys():
    seen = {}

    def _run_plan(plan, hints, decider):
        seen["hints"] = hints
        steps = [(s.action.kind, s.action.effect,
                  s.expect.kind) for s in plan.steps]
        assert steps == [("browser.navigate", "reversible", "url_is"),
                         ("browser.snapshot", "read", "text_contains")]
        from lakra.control.runner import RunResult
        return RunResult(plan_id=plan.plan_id, status="DONE",
                         steps_done=2, detail="ok")

    loop = SimpleNamespace(runner=SimpleNamespace(audit=_Audit()),
                           run_plan=_run_plan)
    goal = {"table_url": "file:///t.html", "table_text": "course seats",
            "expect_text": "Linear Algebra"}
    res = run_task(loop, None, lambda u: None, lambda: [],
                   lambda: [], _task(), dict(goal), SimpleNamespace(),
                   read_tables=lambda: COURSES_INV)
    assert res.status == "DONE"
    assert seen["hints"] == {"url": "file:///t.html",
                             "expect_text": "Linear Algebra",
                             "table_text": "course seats"}
    assert res.findings[0]["headers"] == ["Course", "Seats", "Room"]
    assert TABLE_KEYS == ("table_url", "table_text", "expect_text")


def test_dispatcher_mixed_table_keys_refuse():
    loop = _loop()
    res = run_task(loop, None, lambda u: None, lambda: [], lambda: [],
                   _task(),
                   {"table_url": "file:///t.html",
                    "table_text": "courses", "expect_text": "Ada",
                    "goal_slots": {"a": {"text": "b"}}},
                   SimpleNamespace(), read_tables=lambda: COURSES_INV)
    assert res.status == "STOPPED" and "mixed road keys" in res.detail
    res = run_task(loop, None, lambda u: None, lambda: [], lambda: [],
                   _task(),
                   {"url": "file:///t.html", "table_text": "courses",
                    "expect_text": "Ada"},
                   SimpleNamespace(), read_tables=lambda: COURSES_INV)
    assert res.status == "STOPPED" and "mixed road keys" in res.detail


def test_table_hints_validate():
    assert validate_hints({"url": "file:///t.html",
                           "expect_text": "Ada",
                           "table_text": "courses"})["table_text"] \
        == "courses"
    with pytest.raises(UnknownGoalError):
        validate_hints({"table_text": "my password list"})


# -- NL shaping stays deferred ----------------------------------------------------------

def test_nl_shaping_never_emits_table_keys():
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.decompose import road_of_shaped
    from lakra.control.shaping import shape_goal
    for prose in (
            "Extract the courses table at file:///x.html with Records",
            "Show me the courses table at file:///x.html with Records",
            "Read the Hall table at file:///x.html and show Hall A"):
        try:
            shaped = shape_goal(prose)
        except UnknownGoalError:
            continue  # refusal-first is also a non-table outcome
        assert "table_url" not in shaped and "table_text" not in shaped
        assert road_of_shaped(shaped) in ("observe", "follow", "click",
                                          "search")


# -- live CLI ----------------------------------------------------------------------------

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


def _table_json(stdout):
    for line in stdout.splitlines():
        if line.startswith("table: "):
            return json.loads(line[len("table: "):])
    return None


def test_cli_table_single_completed(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "t1") + ["--road", "table", "--url", COURSES,
                                 "--table", "course seats room catalog",
                                 "--expect", "Linear Algebra", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    data = _table_json(proc.stdout)
    assert data is not None, proc.stdout
    assert data["headers"] == ["Course", "Seats", "Room"]
    assert data["rows"][1] == ["Linear Algebra", "30", "Hall A"]
    assert data["rows"][3] == ["Cartography", "", "Hall C"]
    assert data["rows"][4] == ["Number Theory", "18", "Hall A"]
    assert data["shape"] == [5, 3]
    # Read-only proof on the audit trail: no consequential browser
    # action, no approval gate, on this task's events.
    lines = (tmp_path / "audit-do.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert lines, "audit must record the read-only run"
    assert not any('"ACTION_REQUIRES_APPROVAL"' in l for l in lines)
    for kind in ("browser.click", "browser.type", "browser.submit",
                 "browser.check", "browser.select", "browser.press",
                 "browser.scroll", "browser.wait"):
        assert not any(kind in l and "EXECUTION_STARTED" in l
                       for l in lines), kind
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    d = D(tmp_path / "cli.db")
    try:
        assert d.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] \
            == 0
        assert d.execute("SELECT COUNT(*) FROM tokens WHERE used=1") \
            .fetchone()[0] == 0
    finally:
        d.close()


def test_cli_table_two_unique_second(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "t2") + ["--road", "table", "--url", TWO,
                                 "--table", "room capacity hall",
                                 "--expect", "Hall B", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = _table_json(proc.stdout)
    assert data["table_index"] == 1 and data["total_tables"] == 2
    assert data["headers"] == ["Room", "Capacity"]


def test_cli_table_ambiguous_refuses(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "ta") + ["--road", "table", "--url", AMBIG,
                                 "--table", "course seats algebra",
                                 "--expect", "Linear Algebra", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout
    assert "table: " not in proc.stdout  # refusals carry no findings


def test_cli_table_no_match_refuses(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "tn") + ["--road", "table", "--url", COURSES,
                                 "--table", "qzx nothing matches",
                                 "--expect", "Linear Algebra", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout


def test_cli_table_mismatch_not_completed(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "tm") + ["--road", "table", "--url", COURSES,
                                 "--table", "course seats room catalog",
                                 "--expect", "Zebra", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout
    assert "table: " not in proc.stdout


def test_cli_table_empty_cells(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "te") + ["--road", "table", "--url", EMPTY,
                                 "--table", "member role shift roster",
                                 "--expect", "steward", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = _table_json(proc.stdout)
    assert data["rows"][1] == ["Ada", "", "morning"]
    assert data["rows"][2] == ["", "steward", ""]


def test_cli_table_similar_no_wrong_selection(tmp_path):
    env = _env(tmp_path)
    # Uniquely the middle table (biology/chemistry vocabulary).
    proc = subprocess.run(
        _base(tmp_path, "ts") + ["--road", "table", "--url", SIMILAR,
                                 "--table", "biology chemistry laboratory",
                                 "--expect", "Chemistry Laboratory",
                                 "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = _table_json(proc.stdout)
    assert data["table_index"] == 1
    # Content from a LOOKALIKE table must not verify against it.
    proc = subprocess.run(
        _base(tmp_path, "ts2") + ["--road", "table", "--url", SIMILAR,
                                  "--table", "biology chemistry laboratory",
                                  "--expect", "Calculus Survey", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout


def test_cli_table_credential_refusal(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "tc") + ["--road", "table", "--url", CREDS,
                                 "--table", "service login export",
                                 "--expect", "clerk", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout
    assert "table: " not in proc.stdout
    assert "hunter2" not in proc.stdout  # never exposed via output


def test_cli_table_flag_matrix(tmp_path):
    env = _env(tmp_path)
    base = _base(tmp_path, "tf")
    # Missing --table / --expect / --url each refuse before launch.
    for argv in (["--road", "table", "--url", COURSES,
                  "--expect", "Ada"],
                 ["--road", "table", "--url", COURSES,
                  "--table", "courses"],
                 ["--road", "table", "--table", "courses",
                  "--expect", "Ada"],
                 ["--road", "table", "--url", COURSES,
                  "--table", "courses", "--expect", "Ada",
                  "--text", "x"],
                 ["--road", "observe", "--url", COURSES, "--text", "t",
                  "--expect", "e", "--table", "courses"]):
        proc = subprocess.run(base + argv + ["--yes"], capture_output=True,
                              text=True, cwd=str(ROOT), env=env,
                              timeout=120)
        assert proc.returncode == 1, (argv, proc.stdout + proc.stderr)
        assert "usage error" in proc.stdout, (argv, proc.stdout)


def test_cli_chain_table_legs(tmp_path):
    chain = {"legs": [
        {"road": "table", "table_url": COURSES,
         "table_text": "course seats room catalog",
         "expect_text": "Thermodynamics"},
        {"road": "observe", "url": COURSES,
         "expect_text": "Autumn course catalog"}]}
    chain_file = tmp_path / "table-chain.json"
    chain_file.write_text(json.dumps(chain), encoding="utf-8")
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "tch") + ["--chain-file", str(chain_file),
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout


def test_cli_daemon_table_queue(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    from lakra.control import work_queue as q
    legs = [{"road": "table", "table_url": COURSES,
             "table_text": "course seats room catalog",
             "expect_text": "Number Theory"},
            {"road": "observe", "url": COURSES,
             "expect_text": "Autumn course catalog"}]
    d = D(tmp_path / "cli.db")
    try:
        wid = q.enqueue(d, legs=legs)["work_id"]
        assert q.materialize(q.get(d, wid))["legs"][0]["road"] \
            == "table"
    finally:
        d.close()
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "td") + ["--daemon", "--once", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "processed=1 settled=1" in proc.stdout
    d = D(tmp_path / "cli.db")
    try:
        item = q.get(d, wid)
        assert item["state"] == "COMPLETED" and item["task_id"]
        tasks = d.execute("SELECT status FROM tasks").fetchall()
        assert tasks == [("COMPLETED",)]
        assert len(q.runs_for(d, wid)) == 1
    finally:
        d.close()
