"""Slice-11 tests: hints, replan, plan-store immutability, resume."""

import pytest

from lakra.control import plan_store
from lakra.control.planner import (
    Planner,
    UnknownGoalError,
    validate_hints,
)
from lakra.control.store import Database


def test_hints_accept_known_keys():
    hints = {"url": "file:///c.html", "expect_text": "x",
             "achieved": ["did a"]}
    assert validate_hints(dict(hints)) == hints


def test_hints_reject_unknown_keys_shapes_secrets():
    with pytest.raises(UnknownGoalError):
        validate_hints({"model": "x"})
    with pytest.raises(UnknownGoalError):
        validate_hints({"text": "my password is hunter2"})
    with pytest.raises(UnknownGoalError):
        validate_hints({"url": "x" * 5000})
    with pytest.raises(UnknownGoalError):
        validate_hints({"achieved": "not-a-list"})
    # "secretary" is not a secret (word-boundary rule).
    assert validate_hints({"expect_text": "the secretary pool"})[
        "expect_text"] == "the secretary pool"


def test_plan_rejects_bad_hints_upfront():
    from lakra.control.tasks import Task
    t = Task.create("List the courses", allowed_tools=["browser"],
                    allowed_domains=["x.example"])
    with pytest.raises(UnknownGoalError):
        Planner().plan(t, {"url": "u", "expect_text": "x", "zzz": 1})


def test_plan_store_roundtrip_and_outcomes(tmp_path):
    from lakra.control.tasks import Task
    db = Database(tmp_path / "p.db")
    t = Task.create("List the courses", allowed_tools=["browser"],
                    allowed_domains=["x.example"])
    plan = Planner().plan(t, {"url": "file:///c.html",
                              "expect_text": "pending"})
    plan_store.save_plan(db, plan, {"url": "file:///c.html"})
    assert plan_store.set_step_outcome(db, plan.plan_id, 0, "done") is True
    assert plan_store.set_step_outcome(db, plan.plan_id, 0, "done") is False
    loaded, hints, outcomes = plan_store.load_plan(db, plan.plan_id)
    assert [s.action.kind for s in loaded.steps] == ["browser.navigate",
                                                     "browser.snapshot"]
    assert outcomes == ["done", None]
    assert hints["url"] == "file:///c.html"
    db.close()


def test_superseded_history_immutable(tmp_path):
    from lakra.control.tasks import Task
    db = Database(tmp_path / "p.db")
    t = Task.create("List the courses", allowed_tools=["browser"],
                    allowed_domains=["x.example"])
    plan = Planner().plan(t, {"url": "u", "expect_text": "x"})
    plan_store.save_plan(db, plan, {})
    plan_store.set_step_outcome(db, plan.plan_id, 0, "done")
    plan_store.set_plan_status(db, plan.plan_id, "SUPERSEDED")
    with pytest.raises(ValueError):
        plan_store.set_plan_status(db, plan.plan_id, "EXECUTING")
    with pytest.raises(ValueError):
        plan_store.set_plan_status(db, plan.plan_id, "DONE")
    _, _, outcomes = plan_store.load_plan(db, plan.plan_id)
    assert outcomes[0] == "done"  # outcome survived, unrewritten
    db.close()


def test_migration_v1_to_v2(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('v', '1')")
    con.execute("CREATE TABLE tasks(task_id TEXT PRIMARY KEY, goal TEXT)")
    con.commit()
    con.close()
    db = Database(path)  # migrates v1 -> v6, preserves tasks rows
    assert db.execute("SELECT value FROM meta WHERE key='v'").fetchone()[0] \
        == "6"
    assert db.execute("SELECT name FROM sqlite_master WHERE name='plans'"
                      ).fetchone() is not None
    assert db.execute("SELECT name FROM sqlite_master WHERE"
                      " name='failure_records'").fetchone() is not None
    assert db.execute("SELECT name FROM sqlite_master WHERE"
                      " name='attempts'").fetchone() is not None
    assert db.execute("SELECT name FROM sqlite_master WHERE"
                      " name='loops'").fetchone() is not None
    db.close()


# -- live replan + resume (fixture, no network) --------------------------------
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database as _DB  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


def live_ctx(tmp_path, db):
    registry = TaskRegistry(store=db)
    audit = AuditLog(tmp_path / "a.jsonl")
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(tmp_path / "prof")
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, tmp_path / "shots")
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    runner = Runner(ctrl, sched, audit,
                    observe=lambda: hands.page.locator("body").inner_text(),
                    store=db)
    return SimpleNamespace(
        registry=registry, audit=audit, sched=sched, approvals=approvals,
        tools=tools, sessions=sessions, hands=hands, obs=obs, router=router,
        ctrl=ctrl, runner=runner, db=db)


def start_task(ctx, goal, **kw):
    kw.setdefault("allowed_tools", ["browser"])
    kw.setdefault("allowed_domains", ["file:"])
    kw.setdefault("allowed_paths", [])
    task = ctx.registry.add(Task.create(goal, **kw))
    ctx.registry.checkout(task.task_id, "exec")
    ctx.registry.set_status(task.task_id, Status.RUNNING)
    ctx.sched.enqueue(task.task_id)
    return task


def test_replan_persists_new_plan_and_freezes_old(tmp_path):
    from lakra.control.planner import Planner
    ctx = live_ctx(tmp_path, _DB(tmp_path / "r.db"))
    try:
        task = start_task(ctx, "Click the toggle button")
        ctx.hands.open(FIXTURE)  # click template has no navigate step
        hints = {"selector": "#toggle", "expect_text": "never-appears"}
        plan = Planner().plan(task, dict(hints))
        res = ctx.runner.run(plan, hints=hints)
        assert res.status == "STOPPED"
        ids = plan_store.plans_for_task(ctx.db, task.task_id)
        assert len(ids) == 2  # original + one genuine replan
        old, _, old_out = plan_store.load_plan(ctx.db, plan.plan_id)
        assert old.status == "SUPERSEDED"
        new_id = [i for i in ids if i != plan.plan_id][0]
        new, new_hints, _ = plan_store.load_plan(ctx.db, new_id)
        assert new_hints.get("achieved") == []  # step 0 never verified
        assert res.plan_id == new_id  # runner continued the NEW plan
    finally:
        ctx.sessions.close()


def test_resume_requires_fresh_observation(tmp_path):
    from lakra.control.planner import Planner
    ctx = live_ctx(tmp_path, _DB(tmp_path / "r.db"))
    try:
        task = start_task(ctx, "List the demo courses")
        plan = Planner().plan(task, {"url": FIXTURE,
                                     "expect_text": "pending"})
        plan_store.save_plan(ctx.db, plan, {"url": FIXTURE})
        plan_store.set_step_outcome(ctx.db, plan.plan_id, 0, "done")
        with pytest.raises(ValueError):
            ctx.runner.resume(plan.plan_id, None)  # no observe: refused
        res = ctx.runner.resume(
            plan.plan_id,
            lambda: ctx.hands.page.locator("body").inner_text()
            if ctx.hands._page else (_ for _ in ()).throw(
                RuntimeError("no page")))
        # Fresh hands have no page yet: resume must refuse, not assume.
        assert res.status == "STOPPED" and "fresh" in res.detail.lower()
        # Now with a real page open, resume continues at step 1 to DONE.
        # The observer needs its own page: navigate through the router once
        # (the same two-object share the controller itself relies on).
        from lakra.control.policy import Action
        ctx.router.route(task.task_id, Action(
            kind="browser.navigate", target=FIXTURE, effect="reversible",
            task_id=task.task_id))
        ctx.hands.attach(ctx.obs.current_page)
        res = ctx.runner.resume(
            plan.plan_id,
            lambda: ctx.hands.page.locator("body").inner_text())
        assert (res.status, res.steps_done) == ("DONE", 2)
    finally:
        ctx.sessions.close()
