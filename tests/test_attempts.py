"""Attempt ledger tests: suppression, lineage, abandon, round-trip."""

import pytest

from lakra.control.attempts import AttemptLedger, DuplicateAttempt
from lakra.control.store import Database


def test_begin_close_roundtrip_memory():
    ledger = AttemptLedger()
    aid = ledger.begin("t", "fs.read", "f", "read")
    assert aid and ledger.open_count() == 1
    ledger.close(aid, True)
    assert ledger.open_count() == 0


def test_open_duplicate_suppressed_memory():
    ledger = AttemptLedger()
    ledger.begin("t", "fs.read", "f", "read")
    with pytest.raises(DuplicateAttempt):
        ledger.begin("t", "fs.read", "f", "read")
    assert ledger.open_count() == 1


def test_distinct_tuples_unaffected():
    ledger = AttemptLedger()
    ledger.begin("t", "fs.read", "f1", "read")
    ledger.begin("t", "fs.read", "f2", "read")
    ledger.begin("t2", "fs.read", "f1", "read")
    assert ledger.open_count() == 3


def test_closed_may_run_again():
    ledger = AttemptLedger()
    aid = ledger.begin("t", "fs.read", "f", "read")
    ledger.close(aid, False)  # legitimate failure may retry fresh
    aid2 = ledger.begin("t", "fs.read", "f", "read")
    assert aid2 != aid


def test_db_backed_suppression_and_abandon(tmp_path):
    db = Database(tmp_path / "a.db")
    ledger = AttemptLedger(store=db)
    aid = ledger.begin("t", "k", "x", "read")
    with pytest.raises(DuplicateAttempt):
        ledger.begin("t", "k", "x", "read")
    abandoned = ledger.abandon_stale()
    assert abandoned == [aid]
    assert ledger.open_count() == 0
    # Abandoned rows stay dead: re-begin works (new lineage), old id gone.
    aid2 = ledger.begin("t", "k", "x", "read")
    assert aid2 != aid
    db.close()


def test_retry_lineage_executes_without_suppression(tmp_path):
    """Controller retries are linked attempts, not duplicates: both run."""
    from lakra.control.approvals import Approvals
    from lakra.control.audit import AuditLog
    from lakra.control.policy import Action
    from lakra.control.registry import TaskRegistry
    from lakra.control.scheduler import Scheduler
    from lakra.control.tasks import Status, Task
    from lakra.execution.browser.controller import (
        BrowserController,
        BrowserStep,
    )
    from lakra.execution.browser.verification import Predicate
    from lakra.execution.registry import ExecuteResult, ToolRegistry
    from lakra.execution.router import ToolRouter

    class FakeLoc:
        def count(self):
            return 1

        def first(self):
            return self

        def is_visible(self):
            return True

        def inner_text(self):
            return "hello"

    class FakePage:
        url = "u"

        def locator(self, sel):
            return FakeLoc()

    class FakeActions:
        def __init__(self, page):
            self._page = page

        @property
        def page(self):
            return self._page

    class Flaky:
        def __init__(self):
            self.calls = 0

        def execute(self, action, task):
            self.calls += 1
            if self.calls == 1:
                return ExecuteResult(ok=False, error="transient")
            return ExecuteResult(ok=True, output="hello")

    registry = TaskRegistry()
    audit = AuditLog(tmp_path / "a.jsonl")
    tools = ToolRegistry()
    flaky = Flaky()
    tools.register("test.flaky", flaky)
    router = ToolRouter(registry, tools, Approvals(registry), audit, None)
    ctrl = BrowserController(router, FakeActions(FakePage()), audit)
    task = registry.add(Task.create("lineage", allowed_tools=["test"],
                                    allowed_domains=[], allowed_paths=[]))
    registry.checkout(task.task_id, "e")
    registry.set_status(task.task_id, Status.RUNNING)
    step = BrowserStep(
        action=Action(kind="test.flaky", target="x", effect="read",
                      task_id=task.task_id),
        expect=Predicate(kind="text_contains", target="hello"),
        max_retries=1)
    res = ctrl.run_step(task.task_id, step)
    assert (res.ok, res.verified, res.outcome) == (True, True, "CONTINUE")
    assert flaky.calls == 2
    starts = [e for e in audit.replay() if e["type"] == "EXECUTION_STARTED"]
    assert len(starts) == 2  # retry ran as its own attempt, not suppressed
    assert not [e for e in audit.replay()
                if e["type"] == "DUPLICATE_SUPPRESSED"]


def test_db_close_persists_and_reloads(tmp_path):
    path = tmp_path / "a.db"
    ledger = AttemptLedger(store=Database(path))
    aid = ledger.begin("t", "k", "x", "read", plan_id="p1",
                       parent_attempt_id="par")
    row = ledger.store.execute(
        "SELECT task_id, plan_id, parent_attempt_id FROM attempts"
        " WHERE attempt_id=?", (aid,)).fetchone()
    assert row == ("t", "p1", "par")
    ledger.store.close()
    ledger2 = AttemptLedger(store=Database(path))
    with pytest.raises(DuplicateAttempt):
        ledger2.begin("t", "k", "x", "read")  # still open across restart
    ledger2.store.close()
