"""Observer tests: snapshot fidelity, redaction, caps, router gating."""

from pathlib import Path

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.tasks import Status, Task
from lakra.execution.browser.observer import (
    CREDENTIAL_MARKER,
    BrowserObserver,
    capture_snapshot,
)
from lakra.execution.browser.sessions import BrowserSessions
from lakra.execution.registry import ToolRegistry
from lakra.execution.router import ToolRouter

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


def test_snapshot_content_and_redaction(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        page = s.new_page(FIXTURE)
        snap = capture_snapshot(page, tmp_path / "shots")
        assert "courses.html" in snap.url
        assert snap.title == "My Courses \u2014 Demo Portal"
        assert "Linear Algebra" in snap.a11y_tree
        assert "pending" in snap.a11y_tree
        assert CREDENTIAL_MARKER in snap.a11y_tree
        assert "s3cr3t-nope" not in snap.a11y_tree
        assert snap.redactions_applied >= 1
        shot = Path(snap.screenshot_path)
        assert shot.exists() and shot.stat().st_size > 0
        assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_snapshot_without_shot_dir_skips_screenshot(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        snap = capture_snapshot(s.new_page(FIXTURE), take_shot=False)
        assert snap.screenshot_path == ""
        assert "Thermodynamics" in snap.a11y_tree


def test_snapshot_link_inventory(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        res = obs.execute(Action(kind="browser.navigate", target=FIXTURE,
                                 effect="reversible", task_id="t"),
                          Task.create("g"))
        assert res.ok
        assert '"details" -> course-detail.html' in res.output
        # Fragment hrefs omitted (asserted on exact nav targets: the
        # Controls section legitimately contains "-> #id" selectors).
        assert '"Dashboard" -> #' not in res.output
        assert '"Courses" -> #' not in res.output
        assert "javascript:" not in res.output


def test_snapshot_table_inventory(tmp_path):
    detail = (Path(__file__).parent / "fixtures" / "course-detail.html")
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        res = obs.execute(Action(kind="browser.navigate",
                                 target=detail.as_uri(),
                                 effect="reversible", task_id="t"),
                          Task.create("g"))
        assert res.ok
        assert "Tables (1):" in res.output
        assert "Table 1 (3 rows):" in res.output
        assert "| *Title* | *Price* |" in res.output
        assert "| Vectors | 42 |" in res.output
        assert "| Matrices | 37 |" in res.output


def test_snapshot_no_tables_renders_empty(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        res = obs.execute(Action(kind="browser.navigate", target=FIXTURE,
                                 effect="reversible", task_id="t"),
                          Task.create("g"))
        assert res.ok
        assert "Tables (0):" in res.output


def test_table_inventory_caps(tmp_path):
    # Caps are asserted on the Snapshot object itself: the execute()
    # transport truncates output at MAX_OUTPUT_CHARS, which would clip
    # the proof on a 16-table page (by design — inventory survives,
    # verbosity does not).
    long_cell = "x" * 200
    tables = (f"<table><tr><td>{long_cell}</td>"
              + "<td>" + "y " * 60 + "</td></tr></table>")
    tables += "".join(
        f"<table><tr><th>H{i}</th></tr>"
        + "".join(f"<tr><td>r{r}c0</td><td>r{r}c1</td></tr>"
                  for r in range(25))
        + "</table>"
        for i in range(15))
    page = tmp_path / "many-tables.html"
    page.write_text(f"<html><body>{tables}</body></html>",
                    encoding="utf-8")
    with BrowserSessions(tmp_path / "p") as s:
        snap = capture_snapshot(s.new_page(page.as_uri()),
                                tmp_path / "shots", take_shot=False)
        assert len(snap.tables) == 10  # MAX_TABLES bound
        total, rows = snap.tables[0]
        assert (total, len(rows)) == (1, 1)
        assert rows[0][0] == "x" * 80  # MAX_TABLE_CELL_CHARS bound
        assert len(rows[0][0] + rows[0][1]) <= 160
        total, rows = snap.tables[1]
        assert (total, len(rows)) == (26, 20)  # MAX_TABLE_ROWS bound
        assert rows[0] == ("*H0*",)  # first big table follows the long one


def test_snapshot_control_inventory(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        res = obs.execute(Action(kind="browser.navigate", target=FIXTURE,
                                 effect="reversible", task_id="t"),
                          Task.create("g"))
        assert res.ok
        assert '[text] "Full name" -> #m-name' in res.output
        assert '[check] "News" -> #p-news' in res.output
        assert '[select] "Country" -> #p-country' in res.output
        assert '[text] "Home address" -> #p-home' in res.output
        # Omitted outright: password field, unlabeled + id-less inputs.
        assert "#pwd" not in res.output
        assert "#p-nolabel" not in res.output
        assert "Orphan" not in res.output


def test_observer_executor_requires_navigate_first(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        res = obs.execute(Action(kind="browser.snapshot", target="",
                                 effect="read", task_id="t"),
                          Task.create("g"))
        assert not res.ok and "navigate first" in res.error


def test_observer_rejects_unknown_kind(tmp_path):
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        res = obs.execute(Action(kind="browser.click", target="x",
                                 effect="reversible", task_id="t"),
                          Task.create("g"))
        assert not res.ok


def test_router_gates_observe_kinds(tmp_path):
    registry = TaskRegistry()
    audit = AuditLog(tmp_path / "a.jsonl")
    router = ToolRouter(registry, ToolRegistry(), Approvals(registry), audit,
                        Scheduler(registry))
    with BrowserSessions(tmp_path / "p") as s:
        obs = BrowserObserver(s, tmp_path / "shots")
        for kind in ("browser.navigate", "browser.snapshot",
                     "browser.screenshot"):
            router.tools.register(kind, obs)
        task = registry.add(Task.create(
            "list demo courses", allowed_tools=["browser"],
            allowed_domains=["file:"], allowed_paths=[str(tmp_path)]))
        registry.checkout(task.task_id, "exec")
        registry.set_status(task.task_id, Status.RUNNING)
        res = router.route(task.task_id, Action(
            kind="browser.navigate", target=FIXTURE, effect="reversible",
            task_id=task.task_id))
        assert res.ok and "Linear Algebra" in res.output
        assert "s3cr3t-nope" not in res.output
        # Evil domain parks at policy: no browser traffic for it.
        parked = router.route(task.task_id, Action(
            kind="browser.navigate", target="https://evil.example/courses",
            effect="reversible", task_id=task.task_id))
        assert not parked.ok and "PARKED" in parked.error
