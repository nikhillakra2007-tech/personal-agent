"""V2-05: sandboxed download/upload under explicit L3 gates.

browser.download and browser.upload join browser.submit in L3_KINDS:
every transfer parks (ACTION_REQUIRES_APPROVAL), consumes one token,
executes once, verifies, and settles — the existing approval
machinery, no second system. Destinations/sources confine under one
transfer sandbox (realpath-prefix: no absolute-outside, no ../, no
symlink escape, no silent overwrite, size-bounded, time-bounded).

Roads compose goal -> open -> ground (existing inventories:
ground_link for download triggers, new ground_upload for file
inputs) -> directly-built plan through TaskLoop.run_plan (the
LoopRunner._establish precedent: no planner/analyzer/template
moves). Stub rigs prove refusal mapping + gate behavior; live rigs
prove real transfers on fresh locally-authored fixtures; CLI tests
prove end-to-end approve/deny, daemon/queue integration, kill
recovery, and two-process approve/deny.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import work_queue as wq  # noqa: E402
from lakra.control.analyzer import ground_link, ground_upload  # noqa: E402
from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.chain import (  # noqa: E402
    leg_goal_text,
    run_chain,
    validate_chain,
)
from lakra.control.guards import Guards  # noqa: E402
from lakra.control.loop import ScriptedDecider, TaskLoop  # noqa: E402
from lakra.control.loops import (  # noqa: E402
    DOWNLOAD_KEYS,
    UPLOAD_KEYS,
    _secret_basename,
    _transfer_confine,
    run_download_task,
    run_task,
    run_upload_task,
)
from lakra.control.planner import UnknownGoalError, validate_hints  # noqa: E402
from lakra.control.policy import Action, evaluate  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import (  # noqa: E402
    MAX_TRANSFER_BYTES,
    BrowserActions,
)
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import (  # noqa: E402
    BrowserObserver,
    collect_controls,
    collect_links,
)
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.browser.verification import (  # noqa: E402
    Predicate,
    check,
)
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.resources.monitor import SystemSnapshot  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DL_PAGE = (FIXTURES / "transfer-download.html").as_uri()
DL_AMBIG = (FIXTURES / "transfer-download-ambiguous.html").as_uri()
UP_PAGE = (FIXTURES / "transfer-upload.html").as_uri()
UP_AMBIG = (FIXTURES / "transfer-upload-ambiguous.html").as_uri()

DL_TRIGGER = "Download monthly report"
DL_CONTENT = "monthly report payload alpha"
DL_DEST = "monthly-report.txt"
SRC_NAME = "cargo.txt"
SRC_CONTENT = "cargo manifest beta"
SRC_EXPECT = f"received {SRC_NAME} ({len(SRC_CONTENT.encode())} bytes)"

DOWNLOAD_GOAL = {"download_url": DL_PAGE, "download_text": DL_TRIGGER,
                 "dest_path": DL_DEST,
                 "expect_text": "Monthly report shelf"}


def _upload_goal(**kw):
    goal = {"upload_url": UP_PAGE, "upload_text": "Expense receipt",
            "src_path": SRC_NAME, "expect_text": SRC_EXPECT}
    goal.update(kw)
    return goal


# -- policy --------------------------------------------------------------------

def test_transfer_kinds_are_l3():
    task = Task.create("Download x", allowed_tools=["browser"],
                       allowed_domains=["file:"], allowed_paths=[])
    for kind in ("browser.download", "browser.upload"):
        assert evaluate(Action(kind=kind, target="a",
                               effect="consequential",
                               task_id="t"), task) == "ASK"
        # L3 by kind, not by claimed effect: even "read" parks.
        assert evaluate(Action(kind=kind, target="a", effect="read",
                               task_id="t"), task) == "ASK"


# -- grounding -------------------------------------------------------------------

def test_ground_upload_unique_no_match_ambiguous():
    inv = [("Expense receipt", "file", "#f-receipt"),
           ("Full name", "text", "#m-name")]
    assert ground_upload("Expense receipt", inv) == "#f-receipt"
    assert ground_upload("receipt expense form", inv) == "#f-receipt"
    with pytest.raises(UnknownGoalError):
        ground_upload("qzx nothing", inv)
    twins = [("Expense receipt north", "file", "#f-a"),
             ("Expense receipt south", "file", "#f-b")]
    with pytest.raises(UnknownGoalError):
        ground_upload("Expense receipt", twins)
    assert ground_upload("Expense receipt north", twins) == "#f-a"
    with pytest.raises(UnknownGoalError):
        ground_upload("Full name", [("Full name", "text", "#m-name")])
    with pytest.raises(UnknownGoalError):
        ground_upload("   ", inv)
    with pytest.raises(UnknownGoalError):
        ground_upload("operator password list", inv)
    with pytest.raises(UnknownGoalError):
        ground_upload("receipt", [])
    with pytest.raises(UnknownGoalError):
        ground_upload("receipt", [("x", "file", "no-hash")])


def test_ground_fields_ignores_file_entries():
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.analyzer import ground_fields
    inv = [("Full name", "text", "#m-name"),
           ("Expense receipt", "file", "#f-receipt")]
    fields = ground_fields({"name": {"text": "Ada"}}, inv)
    assert fields == [{"selector": "#m-name", "text": "Ada"}]


def test_secret_basename_and_hint_keys():
    assert _secret_basename("backup-passwords.txt") is True
    assert _secret_basename("vault-secret.csv") is True
    assert _secret_basename("report.txt") is False
    assert _secret_basename("secretary-notes.txt") is True  # fail closed
    assert validate_hints({"url": "file:///x", "expect_text": "ok",
                           "dest_path": "r.txt"})["dest_path"] == "r.txt"
    assert validate_hints({"url": "file:///x", "expect_text": "ok",
                           "src_path": "r.txt"})["src_path"] == "r.txt"
    with pytest.raises(UnknownGoalError):
        validate_hints({"url": "file:///x", "expect_text": "my password"})


# -- confinement --------------------------------------------------------------------

def test_confine_contract(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    assert _transfer_confine("a/b.txt", root).name == "b.txt"
    assert _transfer_confine("../evil.txt", root) is None
    assert _transfer_confine("a/../../evil.txt", root) is None
    assert _transfer_confine(str(tmp_path / "outside.txt"), root) is None
    assert _transfer_confine(str(root / "in.txt"), root) is not None
    assert _transfer_confine("x.txt", None) is None


def test_confine_symlink_escape_where_supported(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (tmp_path / "real").mkdir()
    link = root / "link"
    try:
        link.symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privilege on this host")
    assert _transfer_confine("link/evil.txt", root) is None


def test_file_nonempty_predicate(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    full = root / "f.bin"
    assert check(Predicate("file_nonempty", "f.bin"), None,
                 transfer_root=root) is False
    full.write_bytes(b"")
    assert check(Predicate("file_nonempty", "f.bin"), None,
                 transfer_root=root) is False
    full.write_bytes(b"payload")
    assert check(Predicate("file_nonempty", "f.bin"), None,
                 transfer_root=root) is True
    assert check(Predicate("file_nonempty", "../f.bin"), None,
                 transfer_root=root) is False
    assert check(Predicate("file_nonempty", "f.bin"), None) is False
    assert check(Predicate("file_nonempty", "f.bin"), None,
                 transfer_root=None) is False


def test_no_page_evaluate_in_transfer_paths():
    # Call-syntax scan (docstrings may name the ban; only invocations
    # count).
    for rel in ("execution/browser/actions.py",
                "execution/browser/verification.py",
                "execution/browser/observer.py",
                "execution/browser/controller.py",
                "control/loops.py",
                "control/tables.py"):
        src = (ROOT / "src" / "lakra" / rel).read_text(encoding="utf-8")
        assert ".evaluate(" not in src, rel


# -- stub rig (refusals, no browser) ------------------------------------------------------

class _Audit:
    def __init__(self):
        self.events = []

    def log(self, etype, task_id, payload):
        self.events.append((etype, task_id, payload))


def _stub_loop():
    return SimpleNamespace(runner=SimpleNamespace(audit=_Audit()))


def _task(goal="Download monthly report"):
    return SimpleNamespace(task_id="t-xfer", goal=goal)


def test_download_preplan_refusals(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "taken.txt").write_bytes(b"x")
    inv = [("Download monthly report", "file:///r")]
    loop = _stub_loop()
    kw = dict(transfer_root=root)

    def run(goal, links=None):
        return run_download_task(
            loop, lambda u: None,
            lambda: list(links if links is not None else inv),
            _task(), goal, SimpleNamespace(), **kw)

    bad = run({"download_url": DL_PAGE})
    assert bad.status == "STOPPED" and "missing" in bad.detail
    assert bad.plan_id == "-"
    for dest in ("../evil.txt", "/abs/evil.txt", "sub/../../evil.txt"):
        r = run({"download_url": DL_PAGE, "download_text": DL_TRIGGER,
                 "dest_path": dest, "expect_text": "Monthly"})
        assert r.status == "STOPPED" and "sandbox" in r.detail, dest
    r = run({"download_url": DL_PAGE, "download_text": DL_TRIGGER,
             "dest_path": "taken.txt", "expect_text": "Monthly"})
    assert r.status == "STOPPED" and "already exists" in r.detail
    twins = [("Download monthly report", "a"),
             ("Download monthly report", "b")]
    r = run({"download_url": DL_PAGE, "download_text": DL_TRIGGER,
             "dest_path": "new.txt", "expect_text": "Monthly"}, twins)
    assert r.status == "STOPPED" and "ambiguous" in r.detail
    r = run({"download_url": DL_PAGE, "download_text": "qzx nothing",
             "dest_path": "new.txt", "expect_text": "Monthly"})
    assert r.status == "STOPPED" and "no groundable" in r.detail
    r = run_download_task(
        loop, lambda u: None, lambda: inv, _task(),
        {"download_url": DL_PAGE, "download_text": DL_TRIGGER,
         "dest_path": "new.txt", "expect_text": "Monthly"},
        SimpleNamespace(), transfer_root=None)
    assert r.status == "STOPPED"  # executor would also refuse rootless
    assert getattr(r, "findings", None) is None
    assert loop.runner.audit.events
    assert all(e[0] == "PLAN_OUTCOME" for e in loop.runner.audit.events)


def test_upload_preplan_refusals(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "ok.txt").write_bytes(b"data")
    (root / "empty.txt").write_bytes(b"")
    (root / "backup-passwords.txt").write_bytes(b"data")
    (root / "sub").mkdir()
    big = root / "big.bin"
    big.write_bytes(b"x" * (MAX_TRANSFER_BYTES + 1))
    inv = [("Expense receipt", "file", "#f-receipt")]
    loop = _stub_loop()

    def run(goal, controls=None, root_override="unset"):
        kw = {} if root_override == "unset" else {
            "transfer_root": root_override}
        if root_override == "unset":
            kw = {"transfer_root": root}
        return run_upload_task(
            loop, lambda u: None,
            lambda: list(controls if controls is not None else inv),
            _task("Upload receipt"), goal, SimpleNamespace(), **kw)

    base = {"upload_url": UP_PAGE, "upload_text": "Expense receipt",
            "src_path": "ok.txt", "expect_text": "received"}
    bad = run({"upload_url": UP_PAGE})
    assert bad.status == "STOPPED" and "missing" in bad.detail
    for src in ("../evil.txt", "/abs/evil.txt"):
        r = run({**base, "src_path": src})
        assert r.status == "STOPPED" and "sandbox" in r.detail, src
    r = run({**base, "src_path": "missing.txt"})
    assert r.status == "STOPPED" and "not an uploadable file" in r.detail
    r = run({**base, "src_path": "sub"})
    assert r.status == "STOPPED" and "not an uploadable file" in r.detail
    r = run({**base, "src_path": "big.bin"})
    assert r.status == "STOPPED" and "exceeds" in r.detail
    r = run({**base, "src_path": "backup-passwords.txt"})
    assert r.status == "STOPPED" and "secret-shaped" in r.detail
    twins = [("Expense receipt north", "file", "#f-a"),
             ("Expense receipt south", "file", "#f-b")]
    r = run({**base, "upload_text": "Expense receipt"}, twins)
    assert r.status == "STOPPED" and "ambiguous" in r.detail
    r = run({**base, "upload_text": "qzx nothing"})
    assert r.status == "STOPPED" and "no groundable file input" \
        in r.detail
    r = run({**base}, [])
    assert r.status == "STOPPED"
    r = run({**base}, root_override=None)
    assert r.status == "STOPPED"
    assert getattr(r, "findings", None) is None


def test_dispatcher_routes_transfers_and_mixed_refuses():
    from lakra.control.runner import RunResult
    seen = {}

    def _run_plan(plan, hints, decider):
        seen["kinds"] = [(s.action.kind, s.action.effect,
                          s.expect.kind, s.max_retries)
                         for s in plan.steps]
        seen["hints"] = hints
        return RunResult(plan_id=plan.plan_id, status="DONE",
                         steps_done=len(plan.steps), detail="ok")

    loop = SimpleNamespace(runner=SimpleNamespace(audit=_Audit()),
                           run_plan=_run_plan)
    dl = {"download_url": "file:///d.html",
          "download_text": "Get report", "dest_path": "r.bin",
          "expect_text": "Shelf"}
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        res = run_task(loop, None, lambda u: None,
                       lambda: [("Get report", "file:///r")], lambda: [],
                       _task(), dict(dl), SimpleNamespace(),
                       transfer_root=td)
        assert res.status == "DONE"
        assert seen["kinds"] == [
            ("browser.navigate", "reversible", "url_is", 2),
            ("browser.snapshot", "read", "text_contains", 1),
            ("browser.download", "consequential", "file_nonempty", 0),
            ("browser.snapshot", "read", "text_contains", 1)]
        assert seen["hints"] == {"url": "file:///d.html",
                                 "expect_text": "Shelf",
                                 "dest_path": "r.bin"}
        # Stub run_plan never wrote the file: size read fails closed.
        assert res.findings == [{"dest_path": "r.bin", "bytes": -1}]
        up = {"upload_url": "file:///u.html",
              "upload_text": "Receipt", "src_path": "c.txt",
              "expect_text": "received"}
        Path(td, "c.txt").write_bytes(b"data")
        res = run_task(
            loop, None, lambda u: None, lambda: [],
            lambda: [("Receipt", "file", "#f-r")], _task("Upload r"),
            dict(up), SimpleNamespace(), transfer_root=td)
        assert res.status == "DONE"
        assert seen["kinds"] == [
            ("browser.navigate", "reversible", "url_is", 2),
            ("browser.snapshot", "read", "text_contains", 1),
            ("browser.upload", "consequential", "element_exists", 0),
            ("browser.snapshot", "read", "text_contains", 1)]
        assert res.findings == [{"src_path": "c.txt",
                                 "selector": "#f-r"}]
        for extra in ({"goal_slots": {"a": {"text": "b"}}},
                      {"download_url": "file:///d.html",
                       "upload_url": "file:///u.html",
                       "download_text": "a", "upload_text": "b",
                       "dest_path": "d", "src_path": "s",
                       "expect_text": "e"}):
            mixed = dict(dl)
            mixed.update(extra)
            r = run_task(loop, None, lambda u: None, lambda: [],
                         lambda: [], _task(), mixed, SimpleNamespace(),
                         transfer_root=td)
            assert r.status == "STOPPED" and "mixed road keys" \
                in r.detail, extra


def test_chain_vocab_for_transfers():
    dl = {"road": "download", "download_url": "file:///d.html",
          "download_text": "Get it", "dest_path": "r.bin",
          "expect_text": "Shelf"}
    up = {"road": "upload", "upload_url": "file:///u.html",
          "upload_text": "Receipt", "src_path": "c.txt",
          "expect_text": "received"}
    ob = {"road": "observe", "url": "file:///d.html",
          "expect_text": "Shelf"}
    assert validate_chain({"legs": [dl, ob]})[0]["road"] == "download"
    assert validate_chain({"legs": [up, ob]})[0]["road"] == "upload"
    assert leg_goal_text(dl).startswith("Download")
    assert leg_goal_text(up).startswith("Upload")
    with pytest.raises(Exception):
        validate_chain({"legs": [dict(dl, road="teleport"), ob]})


# -- live rig (real browser, in-process) ----------------------------------------------------

class HealthyMonitor:
    def sample(self, force=False):
        from lakra.resources.monitor import SystemSnapshot as SS
        import time as _t
        return SS(ts=_t.time(), ram_total_mb=16384,
                  ram_available_mb=8192, cpu_percent=5.0,
                  lakra_rss_mb=50.0, browser_rss_mb=0.0,
                  gpu_available=False)

    def under_pressure(self, **kw):
        return False, "ok"


def _live_ns(tmp_path, goal, accept_downloads, transfers):
    db = Database(tmp_path / "live.db")
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(tmp_path / "prof",
                               accept_downloads=accept_downloads)
    sessions.launch()
    hands = BrowserActions(sessions, transfer_root=transfers)
    obs = BrowserObserver(sessions, tmp_path / "shots")
    for kind in ("browser.navigate", "browser.snapshot",
                 "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait", "browser.submit",
                 "browser.check", "browser.select",
                 "browser.download", "browser.upload"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs,
                             transfer_root=transfers)
    guards = Guards(scheduler=sched, monitor=HealthyMonitor())
    runner = Runner(
        ctrl, sched, audit,
        observe=lambda: hands.page.locator("body").inner_text(),
        store=db, guards=guards)
    taskloop = TaskLoop(runner, approvals, audit)
    t = registry.add(Task.create(
        goal, allowed_tools=["browser"],
        allowed_domains=["file:"], allowed_paths=[]))
    registry.checkout(t.task_id, "e")
    registry.set_status(t.task_id, Status.RUNNING)
    sched.enqueue(t.task_id)
    return {"db": db, "registry": registry, "audit": audit,
            "taskloop": taskloop, "hands": hands, "sessions": sessions,
            "task": t}


def _audit_types(ns):
    return [e["type"] for e in ns["audit"].replay()]


def test_live_control_inventory_has_file_kind(tmp_path):
    from lakra.execution.browser.sessions import BrowserSessions as BS
    with BS(tmp_path / "p") as s:
        page = s.new_page(UP_PAGE)
        inv = collect_controls(page)
        assert ("Expense receipt", "file", "#f-receipt") in inv
        page2 = s.new_page(
            (FIXTURES / "courses.html").as_uri())
        inv2 = collect_controls(page2)
        kinds = [k for _, k, _ in inv2]
        assert all(k in ("text", "check", "select", "file")
                   for k in kinds)
        # Credential fields stay excluded (the password input has a
        # label but must never inventory).
        assert all(sel != "#pwd" for _, _, sel in inv2)


def test_live_download_approve(tmp_path):
    transfers = tmp_path / "xfer"
    transfers.mkdir()
    ns = _live_ns(tmp_path, "Download monthly report", True, transfers)
    try:
        hands = ns["hands"]
        decider = ScriptedDecider(True)
        res = run_download_task(
            ns["taskloop"], hands.open,
            lambda: list(collect_links(hands.page)), ns["task"],
            dict(DOWNLOAD_GOAL), decider, transfer_root=transfers)
        assert (res.status, res.steps_done) == ("DONE", 4)
        assert len(decider.seen) == 1  # the L3 gate asked exactly once
        saved = transfers / DL_DEST
        assert saved.read_bytes() == DL_CONTENT.encode()
        assert res.findings[0]["dest_path"] == DL_DEST
        assert res.findings[0]["bytes"] == len(DL_CONTENT.encode())
        types = _audit_types(ns)
        assert types.count("APPROVAL_CONSUMED") == 1
        assert types.count("APPROVED_STEP_EXECUTED") == 1
        assert "ACTION_REQUIRES_APPROVAL" in types
        kinds = [e["payload"].get("kind", "") for e in ns["audit"].replay()
                 if e["type"] == "EXECUTION_STARTED"]
        assert kinds == ["browser.navigate", "browser.snapshot",
                         "browser.download", "browser.snapshot"]
        db = ns["db"]
        assert db.execute("SELECT COUNT(*) FROM tokens WHERE used=1") \
            .fetchone()[0] == 1
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.COMPLETED
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_download_deny_holds(tmp_path):
    transfers = tmp_path / "xfer"
    transfers.mkdir()
    ns = _live_ns(tmp_path, "Download monthly report", True, transfers)
    try:
        hands = ns["hands"]
        res = run_download_task(
            ns["taskloop"], hands.open,
            lambda: list(collect_links(hands.page)), ns["task"],
            dict(DOWNLOAD_GOAL), ScriptedDecider(False),
            transfer_root=transfers)
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.CANCELLED
        assert not (transfers / DL_DEST).exists()  # zero download
        assert getattr(res, "findings", None) is None
        mine = [e for e in ns["audit"].replay()
                if e["task_id"] == ns["task"].task_id]
        assert not [e for e in mine
                    if e["type"] == "APPROVAL_CONSUMED"]
        # The L0 establish steps completed; the GATED action never
        # executed.
        assert not [e for e in mine
                    if e["type"] == "EXECUTION_COMPLETED"
                    and e["payload"].get("kind") == "browser.download"]
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_download_disabled_session_fails_closed(tmp_path):
    transfers = tmp_path / "xfer"
    transfers.mkdir()
    ns = _live_ns(tmp_path, "Download monthly report", False, transfers)
    try:
        hands = ns["hands"]
        res = run_download_task(
            ns["taskloop"], hands.open,
            lambda: list(collect_links(hands.page)), ns["task"],
            dict(DOWNLOAD_GOAL), ScriptedDecider(True),
            transfer_root=transfers)
        assert res.status == "STOPPED"
        assert ns["registry"].get(ns["task"].task_id).status != \
            Status.COMPLETED
        assert not (transfers / DL_DEST).exists()
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_upload_approve(tmp_path):
    transfers = tmp_path / "xfer"
    transfers.mkdir()
    (transfers / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    ns = _live_ns(tmp_path, "Upload expense receipt", False, transfers)
    try:
        hands = ns["hands"]
        decider = ScriptedDecider(True)
        res = run_upload_task(
            ns["taskloop"], hands.open,
            lambda: list(collect_controls(hands.page)), ns["task"],
            _upload_goal(), decider, transfer_root=transfers)
        assert (res.status, res.steps_done) == ("DONE", 4)
        assert len(decider.seen) == 1
        assert hands.page.locator("#upload-status").inner_text() == \
            SRC_EXPECT
        assert res.findings == [{"src_path": SRC_NAME,
                                 "selector": "#f-receipt"}]
        types = _audit_types(ns)
        assert types.count("APPROVAL_CONSUMED") == 1
        assert "ACTION_REQUIRES_APPROVAL" in types
        kinds = [e["payload"].get("kind", "") for e in ns["audit"].replay()
                 if e["type"] == "EXECUTION_STARTED"]
        assert kinds == ["browser.navigate", "browser.snapshot",
                         "browser.upload", "browser.snapshot"]
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.COMPLETED
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_upload_deny_holds(tmp_path):
    transfers = tmp_path / "xfer"
    transfers.mkdir()
    (transfers / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    ns = _live_ns(tmp_path, "Upload expense receipt", False, transfers)
    try:
        hands = ns["hands"]
        res = run_upload_task(
            ns["taskloop"], hands.open,
            lambda: list(collect_controls(hands.page)), ns["task"],
            _upload_goal(), ScriptedDecider(False),
            transfer_root=transfers)
        assert res.status == "STOPPED" and "denied" in res.detail
        assert ns["registry"].get(ns["task"].task_id).status == \
            Status.CANCELLED
        assert hands.page.locator("#upload-status").inner_text() == \
            "nothing uploaded"  # zero upload
    finally:
        ns["sessions"].close()
        ns["db"].close()


def test_live_upload_wrong_expectation_not_completed(tmp_path):
    # set_input_files() succeeding is never task success alone: the
    # page-acceptance predicate must also pass on fresh state.
    transfers = tmp_path / "xfer"
    transfers.mkdir()
    (transfers / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    ns = _live_ns(tmp_path, "Upload expense receipt", False, transfers)
    try:
        hands = ns["hands"]
        res = run_upload_task(
            ns["taskloop"], hands.open,
            lambda: list(collect_controls(hands.page)), ns["task"],
            _upload_goal(expect_text="received nope.txt"),
            ScriptedDecider(True), transfer_root=transfers)
        assert res.status == "STOPPED"
        assert ns["registry"].get(ns["task"].task_id).status != \
            Status.COMPLETED
        assert getattr(res, "findings", None) is None
    finally:
        ns["sessions"].close()
        ns["db"].close()


# -- CLI flag matrix (no browser) ------------------------------------------------------

def test_cli_flag_matrix():
    sys.path.insert(0, str(ROOT / "scripts"))
    import lakra_do
    dl = lakra_do.build_goal(lakra_do.parse_args(
        ["--road", "download", "--url", "file:///d.html",
         "--download", "Get it", "--dest", "r.bin",
         "--expect", "Shelf"]))
    assert dl == {"download_url": "file:///d.html",
                  "download_text": "Get it", "dest_path": "r.bin",
                  "expect_text": "Shelf"}
    up = lakra_do.build_goal(lakra_do.parse_args(
        ["--road", "upload", "--url", "file:///u.html",
         "--upload", "Receipt", "--src", "c.txt",
         "--expect", "received"]))
    assert up == {"upload_url": "file:///u.html",
                  "upload_text": "Receipt", "src_path": "c.txt",
                  "expect_text": "received"}
    bad = [
        ["--road", "download", "--url", "file:///d.html", "--dest",
         "r.bin", "--expect", "Shelf"],  # missing --download
        ["--road", "download", "--url", "file:///d.html", "--download",
         "Get it", "--expect", "Shelf"],  # missing --dest
        ["--road", "upload", "--url", "file:///u.html", "--upload",
         "Receipt", "--expect", "received"],  # missing --src
        ["--road", "observe", "--url", "file:///d.html", "--text",
         "t", "--expect", "e", "--download", "Get it"],
        ["--road", "download", "--url", "file:///d.html", "--download",
         "Get it", "--dest", "r.bin", "--expect", "Shelf", "--src",
         "c.txt"],
        ["--road", "table", "--url", "file:///d.html", "--table",
         "courses", "--expect", "e", "--dest", "r.bin"],
    ]
    for argv in bad:
        with pytest.raises(lakra_do.UsageError):
            lakra_do.build_goal(lakra_do.parse_args(argv))
    assert lakra_do.default_task_goal(
        "download", {"download": "Get it"}).startswith("Download")
    assert lakra_do.default_task_goal(
        "upload", {"upload": "Receipt"}).startswith("Upload")


# -- live CLI -------------------------------------------------------------------------------

def _env(tmp_path, db="cli.db"):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / db)
    return env


def _base(tmp_path, prof):
    (tmp_path / "transfers").mkdir(exist_ok=True)
    return [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(tmp_path / "audit-do.jsonl"),
            "--profile-dir", str(tmp_path / prof),
            "--shots-dir", str(tmp_path / "shots"),
            "--transfers-dir", str(tmp_path / "transfers"),
            "--ram-floor-mb", "0", "--cpu-ceiling", "100"]


def _line_json(stdout, prefix):
    for line in stdout.splitlines():
        if line.startswith(prefix + ": "):
            return json.loads(line[len(prefix) + 2:])
    return None


def test_cli_download_approve(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "dl1") + ["--road", "download", "--url", DL_PAGE,
                                  "--download", DL_TRIGGER, "--dest",
                                  DL_DEST, "--expect",
                                  "Monthly report shelf", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    data = _line_json(proc.stdout, "download")
    assert data["dest_path"] == DL_DEST
    assert (tmp_path / "transfers" / DL_DEST).read_bytes() == \
        DL_CONTENT.encode()


def test_cli_download_deny(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "dl0") + ["--road", "download", "--url", DL_PAGE,
                                  "--download", DL_TRIGGER, "--dest",
                                  DL_DEST, "--expect",
                                  "Monthly report shelf", "--no"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "denied" in proc.stdout
    assert not (tmp_path / "transfers" / DL_DEST).exists()
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    d = D(tmp_path / "cli.db")
    try:
        assert d.execute(
            "SELECT status FROM tasks").fetchall() == [("CANCELLED",)]
        assert d.execute(
            "SELECT COUNT(*) FROM tokens WHERE used=1").fetchone()[0] \
            == 0
    finally:
        d.close()


def test_cli_download_invalid_dest_refuses(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "dlb") + ["--road", "download", "--url", DL_PAGE,
                                  "--download", DL_TRIGGER, "--dest",
                                  "../evil.txt", "--expect",
                                  "Monthly report shelf", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout
    assert not (tmp_path / "evil.txt").exists()
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    d = D(tmp_path / "cli.db")
    try:
        assert d.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] \
            == 0  # refused before any approval
        assert d.execute(
            "SELECT COUNT(*) FROM tokens WHERE used=1").fetchone()[0] \
            == 0
    finally:
        d.close()


def test_cli_upload_approve(tmp_path):
    (tmp_path / "transfers").mkdir(exist_ok=True)
    (tmp_path / "transfers" / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "up1") + ["--road", "upload", "--url", UP_PAGE,
                                  "--upload", "Expense receipt", "--src",
                                  SRC_NAME, "--expect", SRC_EXPECT,
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    data = _line_json(proc.stdout, "upload")
    assert data == {"src_path": SRC_NAME, "selector": "#f-receipt"}


def test_cli_upload_deny(tmp_path):
    (tmp_path / "transfers").mkdir(exist_ok=True)
    (tmp_path / "transfers" / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "up0") + ["--road", "upload", "--url", UP_PAGE,
                                  "--upload", "Expense receipt", "--src",
                                  SRC_NAME, "--expect", SRC_EXPECT, "--no"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "denied" in proc.stdout


def test_cli_upload_outside_sandbox_refuses(tmp_path):
    env = _env(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"data")
    proc = subprocess.run(
        _base(tmp_path, "upb") + ["--road", "upload", "--url", UP_PAGE,
                                  "--upload", "Expense receipt", "--src",
                                  str(outside), "--expect", SRC_EXPECT,
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    d = D(tmp_path / "cli.db")
    try:
        assert d.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] \
            == 0
    finally:
        d.close()


def test_cli_upload_ambiguous_refuses(tmp_path):
    (tmp_path / "transfers").mkdir(exist_ok=True)
    (tmp_path / "transfers" / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "upa") + ["--road", "upload", "--url", UP_AMBIG,
                                  "--upload", "Expense receipt", "--src",
                                  SRC_NAME, "--expect", "received",
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "status: STOPPED" in proc.stdout


def test_cli_chain_download_upload(tmp_path):
    (tmp_path / "transfers").mkdir(exist_ok=True)
    (tmp_path / "transfers" / SRC_NAME).write_bytes(SRC_CONTENT.encode())
    chain = {"legs": [
        {"road": "download", "download_url": DL_PAGE,
         "download_text": DL_TRIGGER, "dest_path": DL_DEST,
         "expect_text": "Monthly report shelf"},
        {"road": "upload", "upload_url": UP_PAGE,
         "upload_text": "Expense receipt", "src_path": SRC_NAME,
         "expect_text": SRC_EXPECT}]}
    chain_file = tmp_path / "xfer-chain.json"
    chain_file.write_text(json.dumps(chain), encoding="utf-8")
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "chx") + ["--chain-file", str(chain_file),
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert (tmp_path / "transfers" / DL_DEST).read_bytes() == \
        DL_CONTENT.encode()
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    d = D(tmp_path / "cli.db")
    try:
        # One L3 gate per consequential leg: two approvals, two tokens.
        assert d.execute(
            "SELECT COUNT(*) FROM tokens WHERE used=1").fetchone()[0] \
            == 2
    finally:
        d.close()


def test_cli_chain_download_deny_stops(tmp_path):
    (tmp_path / "transfers").mkdir(exist_ok=True)
    chain = {"legs": [
        {"road": "download", "download_url": DL_PAGE,
         "download_text": DL_TRIGGER, "dest_path": DL_DEST,
         "expect_text": "Monthly report shelf"},
        {"road": "observe", "url": DL_PAGE,
         "expect_text": "Monthly report shelf"}]}
    chain_file = tmp_path / "xfer-deny.json"
    chain_file.write_text(json.dumps(chain), encoding="utf-8")
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "chd") + ["--chain-file", str(chain_file),
                                  "--no"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert not (tmp_path / "transfers" / DL_DEST).exists()


def _wait_park(audit_path, timeout_s=150):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            lines = Path(audit_path).read_text(
                encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in reversed(lines):
            if '"ACTION_REQUIRES_APPROVAL"' in line \
                    and '"browser.download"' in line:
                return True
        time.sleep(2)
    return False


def test_cli_daemon_download_queue(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    legs = [{"road": "download", "download_url": DL_PAGE,
             "download_text": DL_TRIGGER, "dest_path": DL_DEST,
             "expect_text": "Monthly report shelf"},
            {"road": "observe", "url": DL_PAGE,
             "expect_text": "Monthly report shelf"}]
    d = D(tmp_path / "cli.db")
    try:
        wid = wq.enqueue(d, legs=legs)["work_id"]
    finally:
        d.close()
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path, "dq") + ["--daemon", "--once", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "processed=1 settled=1" in proc.stdout
    assert (tmp_path / "transfers" / DL_DEST).read_bytes() == \
        DL_CONTENT.encode()
    d = D(tmp_path / "cli.db")
    try:
        item = wq.get(d, wid)
        assert item["state"] == "COMPLETED" and item["task_id"]
        assert d.execute("SELECT status FROM tasks").fetchall() == \
            [("COMPLETED",)]
        kinds = [line for line in
                 (tmp_path / "audit-do.jsonl").read_text(
                     encoding="utf-8").splitlines()
                 if '"APPROVAL_CONSUMED"' in line]
        assert len(kinds) == 1  # daemon never bypassed L3
    finally:
        d.close()


def test_cli_daemon_download_kill_recovers_once(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    legs = [{"road": "download", "download_url": DL_PAGE,
             "download_text": DL_TRIGGER, "dest_path": DL_DEST,
             "expect_text": "Monthly report shelf"},
            {"road": "observe", "url": DL_PAGE,
             "expect_text": "Monthly report shelf"}]
    d = D(tmp_path / "cli.db")
    try:
        wid = wq.enqueue(d, legs=legs)["work_id"]
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
            "daemon never parked at the download gate"
        worker.terminate()  # death holding claim + parked approval
        worker.wait(timeout=60)
    finally:
        if worker.poll() is None:
            worker.kill()
    d = D(tmp_path / "cli.db")
    try:
        cur = wq.get(d, wid)
        assert cur["state"] == "CLAIMED"  # live claim, not stolen yet
        assert wq.reclaim_stale(d, "intruder") is None
        d.execute("UPDATE work_items SET lease_until=1 WHERE"
                  " work_id=?", (wid,))
        d.commit()
        assert not (tmp_path / "transfers" / DL_DEST).exists()
    finally:
        d.close()
    # The stale parked approval cannot execute anything: decide it,
    # then restart with fresh consent for the new attempt.
    stale = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "approve.py"), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert "approved" in stale.stdout, stale.stdout
    retry = subprocess.run(
        _base(tmp_path, "re") + ["--daemon", "--once", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    d = D(tmp_path / "cli.db")
    try:
        cur = wq.get(d, wid)
        assert cur["state"] == "COMPLETED"
        assert cur["task_id"]
        tasks = d.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        used = d.execute("SELECT COUNT(*) FROM tokens WHERE used=1"
                         ).fetchone()[0]
        assert (tmp_path / "transfers" / DL_DEST).read_bytes() == \
            DL_CONTENT.encode()
    finally:
        d.close()
    assert tasks == 1  # one bound task, never duplicated
    assert used == 1  # stale approval never executed; fresh token once


def test_cli_daemon_download_two_process_approve(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    legs = [{"road": "download", "download_url": DL_PAGE,
             "download_text": DL_TRIGGER, "dest_path": DL_DEST,
             "expect_text": "Monthly report shelf"},
            {"road": "observe", "url": DL_PAGE,
             "expect_text": "Monthly report shelf"}]
    d = D(tmp_path / "cli.db")
    try:
        wid = wq.enqueue(d, legs=legs)["work_id"]
    finally:
        d.close()
    env = _env(tmp_path)
    worker = subprocess.Popen(
        _base(tmp_path, "appr2") + ["--daemon", "--once", "--poll",
                                    "180"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env)
    try:
        assert _wait_park(tmp_path / "audit-do.jsonl"), \
            "daemon never parked at the download gate"
        approved = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "approve.py"),
             "--yes"],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            timeout=120)
        assert "approved" in approved.stdout, approved.stdout
        out, _ = worker.communicate(timeout=300)
    finally:
        if worker.poll() is None:
            worker.kill()
    assert worker.returncode == 0, out
    assert "processed=1 settled=1" in out, out
    assert (tmp_path / "transfers" / DL_DEST).read_bytes() == \
        DL_CONTENT.encode()  # exactly one download
    d = D(tmp_path / "cli.db")
    try:
        assert wq.get(d, wid)["state"] == "COMPLETED"
        assert d.execute("SELECT status FROM tasks").fetchall() == \
            [("COMPLETED",)]
        assert d.execute(
            "SELECT COUNT(*) FROM tokens WHERE used=1").fetchone()[0] \
            == 1
        assert len(wq.runs_for(d, wid)) == 1
    finally:
        d.close()


def test_cli_daemon_download_two_process_deny(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    legs = [{"road": "download", "download_url": DL_PAGE,
             "download_text": DL_TRIGGER, "dest_path": DL_DEST,
             "expect_text": "Monthly report shelf"},
            {"road": "observe", "url": DL_PAGE,
             "expect_text": "Monthly report shelf"}]
    d = D(tmp_path / "cli.db")
    try:
        wid = wq.enqueue(d, legs=legs)["work_id"]
    finally:
        d.close()
    env = _env(tmp_path)
    worker = subprocess.Popen(
        _base(tmp_path, "deny2") + ["--daemon", "--once", "--poll",
                                    "180"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env)
    try:
        assert _wait_park(tmp_path / "audit-do.jsonl"), \
            "daemon never parked at the download gate"
        denied = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "approve.py"),
             "--no"],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            timeout=120)
        assert "denied" in denied.stdout, denied.stdout
        out, _ = worker.communicate(timeout=300)
    finally:
        if worker.poll() is None:
            worker.kill()
    assert "settled=1" in out, out
    assert not (tmp_path / "transfers" / DL_DEST).exists()  # zero exec
    d = D(tmp_path / "cli.db")
    try:
        assert wq.get(d, wid)["state"] == "CANCELLED"
        assert d.execute("SELECT status FROM tasks").fetchall() == \
            [("CANCELLED",)]
        assert d.execute(
            "SELECT COUNT(*) FROM tokens WHERE used=1").fetchone()[0] \
            == 0
    finally:
        d.close()
