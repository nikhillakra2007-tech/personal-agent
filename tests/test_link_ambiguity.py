"""V1-D2: exact-duplicate link labels must not collapse.

collect_links() dedupes on (text, href): byte-identical repeats keep
the first, but the same visible label on different targets stays
distinct so ground_link() sees every candidate and refuses the tie
instead of silently following the first. Unique-link, no-match, and
submit/javascript/fragment exclusion behavior is unchanged; loop
plurality still counts distinct links.

Unit tests use a minimal fake anchor inventory (no browser); live
tests drive the real follow road on a fresh fixture page.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from lakra.control.analyzer import UnknownGoalError, ground_link
from lakra.execution.browser.observer import collect_links

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
TWIN_URL = (FIXTURES / "twin-links.html").as_uri()


class _Anchor:
    def __init__(self, text, href):
        self._text = text
        self._href = href

    def get_attribute(self, name):
        assert name == "href"
        return self._href

    def inner_text(self):
        return self._text


class _Anchors:
    def __init__(self, pairs):
        self._anchors = [_Anchor(t, h) for t, h in pairs]

    def count(self):
        return len(self._anchors)

    def nth(self, i):
        return self._anchors[i]


class _Page:
    def __init__(self, pairs):
        self._pairs = pairs

    def locator(self, sel):
        assert sel == "a[href]"
        return _Anchors(self._pairs)


# -- inventory: duplicates stay distinct ----------------------------------------

def test_unique_links_all_returned():
    out = collect_links(_Page([("East Dock manifest", "e.html"),
                               ("West Dock ledger", "w.html")]))
    assert out == (("East Dock manifest", "e.html"),
                   ("West Dock ledger", "w.html"))


def test_exact_duplicate_labels_stay_distinct():
    out = collect_links(_Page([("Harbor log", "east.html"),
                               ("Harbor log", "west.html")]))
    assert out == (("Harbor log", "east.html"),
                   ("Harbor log", "west.html"))


def test_byte_identical_repeats_still_collapse():
    out = collect_links(_Page([("Harbor log", "east.html"),
                               ("Harbor log", "east.html"),
                               ("Gamma record", "g.html")]))
    assert out == (("Harbor log", "east.html"),
                   ("Gamma record", "g.html"))


def test_exclusions_unchanged():
    out = collect_links(_Page([("Good link", "g.html"),
                               ("Frag", "#top"),
                               ("Script", "javascript:void(0)"),
                               ("", "empty.html"),
                               ("No target", "")]))
    assert out == (("Good link", "g.html"),)


# -- grounding: existing tie contract now reachable ------------------------------

def test_ground_unique_winner():
    assert ground_link("East Dock manifest",
                       [("East Dock manifest", "e.html"),
                        ("West Dock ledger", "w.html")]) == \
        "East Dock manifest"


def test_ground_exact_duplicates_refuse():
    with pytest.raises(UnknownGoalError):
        ground_link("Harbor log", [("Harbor log", "east.html"),
                                   ("Harbor log", "west.html")])


def test_ground_no_match_refuses_unchanged():
    with pytest.raises(UnknownGoalError):
        ground_link("North Pier light",
                    [("East Dock manifest", "e.html")])


# -- live follow road on a fresh fixture page -------------------------------------

def _run_cli(tmp_path, *argv):
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


def _task_state(tmp_path):
    import json
    import os
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / "cli.db")
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--audit", str(tmp_path / "audit-do.jsonl"),
         "--profile-dir", str(tmp_path / "prof-s"),
         "--shots-dir", str(tmp_path / "shots-s"),
         "--ram-floor-mb", "0", "--cpu-ceiling", "100",
         "--list", "--state", "all", "--format", "json"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    return json.loads(out.stdout)["tasks"]


def _audit_types(tmp_path):
    import json
    types = {}
    try:
        lines = (tmp_path / "audit-do.jsonl").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return types
    for line in lines:
        t = json.loads(line).get("type")
        types[t] = types.get(t, 0) + 1
    return types


def test_live_unique_link_follows(tmp_path):
    proc = _run_cli(tmp_path, "--road", "follow", "--url", TWIN_URL,
                    "--text", "Gamma record",
                    "--expect", "detail record: Gamma", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert _task_state(tmp_path)[0]["status"] == "COMPLETED"


def test_live_duplicate_label_refuses(tmp_path):
    proc = _run_cli(tmp_path, "--road", "follow", "--url", TWIN_URL,
                    "--text", "Harbor log",
                    "--expect", "detail record", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ambiguous" in proc.stdout
    assert _task_state(tmp_path)[0]["status"] == "RUNNING"
    # Pre-plan refusal: exactly one shaped outcome, nothing executed.
    assert _audit_types(tmp_path) == {"PLAN_OUTCOME": 1}


def test_live_no_match_refuses(tmp_path):
    proc = _run_cli(tmp_path, "--road", "follow", "--url", TWIN_URL,
                    "--text", "North Pier light",
                    "--expect", "detail record", "--yes")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _audit_types(tmp_path) == {"PLAN_OUTCOME": 1}
