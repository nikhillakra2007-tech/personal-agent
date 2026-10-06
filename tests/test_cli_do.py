"""Slice-36: thin browser CLI tests.

Shaper unit tests prove flags -> run_task() goal mapping (exact key
sets, usage errors, mixed-road refusal) with no browser. Three live
subprocess smokes prove the CLI process drives follow DONE, holds a
denied form submit, and rejects malformed invocations. No src/
changes; lakra_run.py / approve.py untouched."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lakra_do
from lakra_do import UsageError, build_goal, parse_args

LOOP_INDEX = (Path(__file__).parent / "fixtures" / "loop-index.html").as_uri()
FORM_PAGE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()

SLOTS = {"name": {"text": "Ada"},
         "city": {"text": "Lagos"},
         "zip": {"text": "10001"},
         "news": {"checked": True},
         "country": {"select": "ng"}}


def opts(**kw):
    base = {"allow_domains": []}
    base.update(kw)
    return base


# -- shaper: exact key sets ------------------------------------------------------

def test_shaper_follow_exact():
    goal = build_goal(opts(road="follow", url="file:///l.html",
                           text="Follow the Beta record",
                           expect="detail record: Beta"))
    assert goal == {"list_url": "file:///l.html",
                    "goal_text": "Follow the Beta record",
                    "body_expect": "detail record: Beta"}


def test_shaper_loop_exact_ints():
    goal = build_goal(opts(road="loop", url="file:///l.html",
                           text="Follow every batch record",
                           expect="detail record",
                           max_items="2", max_iters="3"))
    assert goal == {"list_url": "file:///l.html",
                    "goal_text": "Follow every batch record",
                    "body_expect": "detail record",
                    "max_items": 2, "max_iters": 3}
    assert type(goal["max_items"]) is int


def test_shaper_form_exact():
    goal = build_goal(opts(road="form", url="file:///f.html",
                           slots_json=json.dumps(SLOTS),
                           submit="#m-submit"))
    assert goal == {"form_url": "file:///f.html",
                    "goal_slots": SLOTS,
                    "submit_selector": "#m-submit"}


# -- shaper: missing / malformed ---------------------------------------------------

def test_shaper_missing_and_bad_road():
    with pytest.raises(UsageError):
        build_goal(opts(url="file:///l.html"))
    with pytest.raises(UsageError):
        build_goal(opts(road="follow", text="x", expect="y"))
    with pytest.raises(UsageError):
        build_goal(opts(road="follow", url="file:///l.html", text="x"))
    with pytest.raises(UsageError):
        build_goal(opts(road="loop", url="file:///l.html", text="x",
                         expect="y", max_items="many", max_iters="3"))
    with pytest.raises(UsageError):
        build_goal("not-a-dict")
    with pytest.raises(UsageError):
        parse_args(["--bogus"])
    with pytest.raises(UsageError):
        parse_args(["--url"])


def test_shaper_bad_slots_json():
    with pytest.raises(UsageError):
        build_goal(opts(road="form", url="file:///f.html",
                         slots_json="{nope", submit="#s"))
    with pytest.raises(UsageError):
        build_goal(opts(road="form", url="file:///f.html",
                         slots_json='["name"]', submit="#s"))
    with pytest.raises(UsageError):
        build_goal(opts(road="form", url="file:///f.html",
                         slots_json="{}", submit="#s"))


def test_shaper_mixed_flags_refused():
    with pytest.raises(UsageError):
        build_goal(opts(road="follow", url="u", text="t", expect="e",
                         max_items="1"))
    with pytest.raises(UsageError):
        build_goal(opts(road="loop", url="u", text="t", expect="e",
                         max_items="1", max_iters="1",
                         slots_json=json.dumps(SLOTS)))
    with pytest.raises(UsageError):
        build_goal(opts(road="form", url="u",
                         slots_json=json.dumps(SLOTS), submit="#s",
                         text="t"))


def test_shaper_allow_domain_repeatable():
    parsed = parse_args(["--road", "follow", "--url", "u",
                         "--allow-domain", "file:",
                         "--allow-domain", "example.com"])
    assert parsed["allow_domains"] == ["file:", "example.com"]


def test_monitor_for_defaults_and_bad_values():
    from lakra_do import monitor_for
    mon = monitor_for({"allow_domains": []})
    assert (mon.ram_floor_mb, mon.cpu_ceiling) == (512, 90.0)
    mon = monitor_for({"allow_domains": [], "ram_floor_mb": "0",
                       "cpu_ceiling": "100"})
    assert (mon.ram_floor_mb, mon.cpu_ceiling) == (0, 100.0)
    with pytest.raises(UsageError):
        monitor_for({"allow_domains": [], "ram_floor_mb": "lots"})
    with pytest.raises(UsageError):
        monitor_for({"allow_domains": [], "cpu_ceiling": "high"})


# -- live subprocess smokes --------------------------------------------------------

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


def test_cli_follow_done_exit_0(tmp_path):
    proc = run_cli(tmp_path, "--road", "follow", "--url", LOOP_INDEX,
                   "--text", "Follow the Beta record",
                   "--expect", "detail record: Beta", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert "task: COMPLETED" in proc.stdout


def test_cli_form_deny_exit_2(tmp_path):
    proc = run_cli(tmp_path, "--road", "form", "--url", FORM_PAGE,
                   "--slots-json", json.dumps(SLOTS),
                   "--submit", "#m-submit", "--no")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout
    assert "denied" in proc.stdout


def test_cli_no_road_exit_1_usage(tmp_path):
    proc = run_cli(tmp_path, "--url", LOOP_INDEX)
    assert proc.returncode == 1
    assert "usage error" in proc.stdout


def test_parse_headed_flag_and_headless_default():
    assert parse_args(["--headed"])["headed"] is True
    assert "headed" not in parse_args([])
    assert lakra_do.headless_for({"allow_domains": []}) is True
    assert lakra_do.headless_for({"allow_domains": [],
                                 "headed": True}) is False
