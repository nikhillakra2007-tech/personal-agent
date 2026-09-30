"""Slice-08 persistence tests: reload, cross-process, crash, faults."""

import sqlite3

import pytest

from lakra.control.approvals import ApprovalError, Approvals
from lakra.control.audit import AuditLog
from lakra.control.policy import Action
from lakra.control.recovery import boot, find_uncertain
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.store import CODE_VERSION, Database, SchemaError
from lakra.control.tasks import Status, Task


def db_at(tmp_path, name="t.db"):
    return Database(tmp_path / name)


def make_task(r, **kw):
    kw.setdefault("allowed_tools", ["fs"])
    kw.setdefault("allowed_domains", [])
    kw.setdefault("allowed_paths", ["C:/ws"])
    return r.add(Task.create("persist me", **kw))


def act(t, target="C:/elsewhere/x"):
    return Action(kind="fs.list", target=target, effect="bounded-mutation",
                  task_id=t.task_id)


# -- tasks ---------------------------------------------------------------------
def test_task_roundtrip_and_reload(tmp_path):
    d1 = db_at(tmp_path)
    r1 = TaskRegistry(store=d1)
    t = make_task(r1, priority=3)
    r1.checkout(t.task_id, "exec")
    r1.set_status(t.task_id, Status.RUNNING)
    d1.close()

    d2 = db_at(tmp_path)
    r2 = TaskRegistry(store=d2)
    assert len(r2.load_all()) == 1
    back = r2.get(t.task_id)
    assert (back.goal, back.priority, back.status, back.owner) == \
        ("persist me", 3, Status.RUNNING, "exec")
    assert back.budget.max_steps == 50
    d2.close()


def test_stale_owners_released_on_boot(tmp_path):
    d1 = db_at(tmp_path)
    r1 = TaskRegistry(store=d1)
    t = make_task(r1)
    r1.checkout(t.task_id, "dead-process")
    d1.close()

    d2 = db_at(tmp_path)
    r2 = TaskRegistry(store=d2)
    r2.load_all()
    assert r2.release_stale_owners() == 1
    assert r2.get(t.task_id).owner is None
    d2.close()


# -- approvals across processes --------------------------------------------------
def test_approve_deny_across_processes(tmp_path):
    path = tmp_path / "x.db"
    a = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    r1 = a.registry
    t = make_task(r1)
    r1.checkout(t.task_id, "exec")
    r1.set_status(t.task_id, Status.RUNNING)
    apv = a.request(t.task_id, act(t))

    b = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    b.registry.load_all()
    items = b.pending()
    assert len(items) == 1 and items[0]["goal"] == "persist me"
    decided = b.decide(apv.approval_id, True)
    assert decided.decided == "approved"
    # Process A observes via fresh read.
    assert a._db_load_approval(apv.approval_id).decided == "approved"


def test_concurrent_decide_race_one_winner(tmp_path):
    path = tmp_path / "x.db"
    mk = lambda: Approvals(TaskRegistry(store=Database(path)),  # noqa: E731
                           store=Database(path))
    a, b = mk(), mk()
    t = make_task(a.registry)
    a.registry.checkout(t.task_id, "exec")
    a.registry.set_status(t.task_id, Status.RUNNING)
    apv = a.request(t.task_id, act(t))
    b.registry.load_all()
    b.decide(apv.approval_id, True)
    with pytest.raises(ApprovalError):
        a.decide(apv.approval_id, False)  # loses the CAS race


def test_ttl_expiry_across_restart(tmp_path):
    path = tmp_path / "x.db"
    a = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    t = make_task(a.registry)
    a.registry.checkout(t.task_id, "exec")
    a.registry.set_status(t.task_id, Status.RUNNING)
    a.request(t.task_id, act(t), ttl_s=-1)
    b = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    b.registry.load_all()
    assert b.sweep() != []
    assert b.registry.get(t.task_id).status == Status.CANCELLED


# -- tokens across restart ---------------------------------------------------------
def test_token_single_use_across_restart(tmp_path):
    path = tmp_path / "x.db"
    a = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    t = make_task(a.registry)
    a.registry.checkout(t.task_id, "exec")
    a.registry.set_status(t.task_id, Status.RUNNING)
    token = a.mint_token(a.decide(
        a.request(t.task_id, act(t)).approval_id, True).approval_id)

    b = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    b.registry.load_all()
    b.load()
    got = b.redeem(token.token_id, act(t))
    assert got.approval_id == token.approval_id
    b.burn(token.token_id)

    c = Approvals(TaskRegistry(store=Database(path)), store=Database(path))
    c.registry.load_all()
    c.load()
    with pytest.raises(ApprovalError):
        c.redeem(token.token_id, act(t))  # burned: stays dead


def test_token_wrong_action_rejected(tmp_path):
    d = db_at(tmp_path)
    ap = Approvals(TaskRegistry(store=d), store=d)
    r = ap.registry
    t = make_task(r)
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    token = ap.mint_token(ap.decide(
        ap.request(t.task_id, act(t)).approval_id, True).approval_id)
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id, act(t, target="C:/elsewhere/OTHER"))


# -- crash rules ----------------------------------------------------------------------
def test_uncertain_scan_pairs_started_without_completed():
    events = [
        {"type": "EXECUTION_STARTED", "task_id": "t",
         "payload": {"kind": "publish"}},
        {"type": "EXECUTION_STARTED", "task_id": "t",
         "payload": {"kind": "fs.read"}},
        {"type": "EXECUTION_COMPLETED", "task_id": "t",
         "payload": {"kind": "fs.read"}},
    ]
    assert find_uncertain(events) == [("t", "publish")]


def test_boot_burns_uncertain_token(tmp_path):
    d = db_at(tmp_path)
    r, ap = TaskRegistry(store=d), Approvals(TaskRegistry(store=d), store=d)
    ap.registry = r
    t = make_task(r)
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    token = ap.mint_token(ap.decide(
        ap.request(t.task_id, act(t)).approval_id, True).approval_id)
    log = AuditLog(tmp_path / "crash.jsonl")
    log.log("APPROVAL_CONSUMED", t.task_id,
            {"kind": "fs.list", "approval_id": token.approval_id})
    log.log("EXECUTION_STARTED", t.task_id, {"kind": "fs.list"})
    # ...crash: no COMPLETED. Reboot into fresh objects, same files.
    d2 = db_at(tmp_path)
    r2 = TaskRegistry(store=d2)
    ap2 = Approvals(r2, store=d2)
    sched = Scheduler(r2)
    log2 = AuditLog(tmp_path / "crash.jsonl")
    report = boot(d2, r2, ap2, sched, log2)
    assert report["tokens_burned"] == [token.token_id]
    assert len(report["uncertain"]) == 1
    with pytest.raises(ApprovalError):
        ap2.redeem(token.token_id, act(t))  # burned: no duplicate execution


def test_boot_minted_unexecuted_token_survives(tmp_path):
    d = db_at(tmp_path)
    r = TaskRegistry(store=d)
    ap = Approvals(r, store=d)
    t = make_task(r)
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    token = ap.mint_token(ap.decide(
        ap.request(t.task_id, act(t)).approval_id, True).approval_id)
    d2 = db_at(tmp_path)
    r2 = TaskRegistry(store=d2)
    ap2 = Approvals(r2, store=d2)
    r2.load_all()
    ap2.load()
    got = ap2.redeem(token.token_id, act(t))  # no STARTED: still usable
    assert got.token_id == token.token_id


# -- faults ------------------------------------------------------------------------------
def test_rollback_on_fault(tmp_path):
    d = db_at(tmp_path)
    r = TaskRegistry(store=d)
    t = make_task(r)
    with pytest.raises(sqlite3.IntegrityError):
        d.execute("INSERT INTO tasks(task_id) VALUES ('x')")
    d.rollback()
    assert r.get(t.task_id).goal == "persist me"
    assert d.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_corrupt_db_fails_closed(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite")
    with pytest.raises(SchemaError):
        Database(bad)


def test_version_mismatch_fails_closed(tmp_path):
    d = db_at(tmp_path)
    d.execute("UPDATE meta SET value='999' WHERE key='v'")
    d.commit()
    d.close()
    with pytest.raises(SchemaError):
        db_at(tmp_path)


def test_fresh_db_is_versioned(tmp_path):
    d = db_at(tmp_path)
    row = d.execute("SELECT value FROM meta WHERE key='v'").fetchone()
    assert row[0] == str(CODE_VERSION)
    d.close()


# -- usage ---------------------------------------------------------------------------------
def test_usage_counters_persist(tmp_path):
    from lakra.control import task_store
    d = db_at(tmp_path)
    r = TaskRegistry(store=d)
    t = make_task(r)
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    sched = Scheduler(r, store=d)
    sched.enqueue(t.task_id)
    assert sched.next_turn() is not None
    assert task_store.get_usage(d, t.task_id)[0] == 1
    from lakra.control.budgets import TokenLedger
    ledger = TokenLedger(100).attach(t.task_id, d)
    ledger.charge(40)
    d.close()
    d2 = db_at(tmp_path)
    assert task_store.get_usage(d2, t.task_id) == (1, 40)
    assert TokenLedger(100).attach(t.task_id, d2).used_tokens == 40
    d2.close()
