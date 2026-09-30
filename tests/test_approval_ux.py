"""Approval UX tests: listing, TTL fail-closed, one-shot tokens, no bypass."""

import pytest

from lakra.control.approvals import ApprovalError, Approvals
from lakra.control.audit import AuditLog
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.tasks import Status, Task
from lakra.execution.filesystem import FileSystemExecutor
from lakra.execution.registry import ToolRegistry
from lakra.execution.router import ToolRouter


def setup(**kw):
    kw.setdefault("allowed_tools", ["fs"])
    kw.setdefault("allowed_domains", [])
    kw.setdefault("allowed_paths", ["C:/ws"])
    r = TaskRegistry()
    t = r.add(Task.create("approve me", **kw))
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    return r, Approvals(r), t


def action_for(t, target="C:/elsewhere/x", effect="bounded-mutation"):
    return Action(kind="fs.list", target=target, effect=effect,
                  task_id=t.task_id)


def test_pending_listing_shape():
    r, ap, t = setup()
    ap.request(t.task_id, action_for(t))
    items = ap.pending()
    assert len(items) == 1
    item = items[0]
    assert item["goal"] == "approve me"
    assert item["kind"] == "fs.list" and item["effect"] == "bounded-mutation"
    assert item["expires_at"] and item["task_id"] == t.task_id


def test_ttl_sweep_fails_closed():
    r, ap, t = setup()
    ap.request(t.task_id, action_for(t), ttl_s=-1)  # already expired
    expired = ap.sweep()
    assert len(expired) == 1
    assert r.get(t.task_id).status == Status.CANCELLED
    assert ap.pending() == []


def test_no_approve_all_mechanism():
    for name in ("approve_all", "approve_task", "approve_everything",
                 "auto_approve"):
        assert not hasattr(Approvals, name)


def _approved(r, ap, t):
    apv = ap.request(t.task_id, action_for(t))
    return ap.decide(apv.approval_id, True)


def test_token_mint_redeem_burn_once():
    r, ap, t = setup()
    decided = _approved(r, ap, t)
    token = ap.mint_token(decided.approval_id)
    assert token.task_id == t.task_id and not token.used
    ap.redeem(token.token_id, action_for(t))  # validates, no burn
    assert ap._tokens[token.token_id].used is False
    ap.burn(token.token_id)
    assert ap._tokens[token.token_id].used is True
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id, action_for(t))  # reuse rejected


def test_token_rejects_wrong_task_and_modified_action():
    r, ap, t = setup()
    other = r.add(Task.create("other", allowed_tools=["fs"],
                              allowed_domains=[], allowed_paths=["C:/ws"]))
    token = ap.mint_token(_approved(r, ap, t).approval_id)
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id, action_for(other))  # task mismatch
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id,
                  action_for(t, target="C:/elsewhere/y"))  # target mismatch
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id, Action(
            kind="fs.read", target="C:/elsewhere/x",
            effect="bounded-mutation", task_id=t.task_id))  # kind mismatch


def test_token_rejects_verdict_flip():
    r, ap, t = setup()
    token = ap.mint_token(_approved(r, ap, t).approval_id)
    # World changes between approval and presentation: the exact target is
    # blocklisted, so the live recheck flips ASK -> BLOCK -> redeem rejects.
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id, action_for(t),
                  deny_targets=frozenset({"C:/elsewhere/x"}))
    assert ap._tokens[token.token_id].used is False  # unburned


def test_token_unknown_and_unapproved():
    _, ap, _ = setup()
    with pytest.raises(ApprovalError):
        ap.mint_token("nope")
    with pytest.raises(ApprovalError):
        ap.redeem("nope", action_for(Task.create("g")))


def test_expired_token_rejected():
    r, ap, t = setup()
    token = ap.mint_token(_approved(r, ap, t).approval_id, ttl_s=-1)
    with pytest.raises(ApprovalError):
        ap.redeem(token.token_id, action_for(t))


def _routed(tmp_path):
    r = TaskRegistry()
    audit = AuditLog(tmp_path / "a.jsonl")
    approvals = Approvals(r)
    tools = ToolRegistry()
    tools.register("fs.read", FileSystemExecutor(tmp_path / "ws"))
    router = ToolRouter(registry=r, tools=tools, approvals=approvals,
                        audit=audit, scheduler=Scheduler(r))
    t = r.add(Task.create("token run", allowed_tools=["fs"],
                          allowed_domains=[], allowed_paths=["C:/ws"]))
    r.checkout(t.task_id, "exec")
    r.set_status(t.task_id, Status.RUNNING)
    return router, approvals, audit, t


def test_token_executes_once_through_router(tmp_path):
    router, approvals, audit, t = _routed(tmp_path)
    act = Action(kind="fs.read", target="C:/elsewhere/n.txt",
                 effect="bounded-mutation", task_id=t.task_id)
    parked = router.route(t.task_id, act)
    assert not parked.ok and "PARKED" in (parked.error or "")
    approval_id = audit.replay()[-1]["payload"]["approval_id"]
    token = approvals.mint_token(
        approvals.decide(approval_id, True).approval_id)
    # Identical action WITHOUT token parks again (no bypass, no progress).
    again = router.route(t.task_id, act)
    assert not again.ok and "PARKED" in (again.error or "")
    # Settle the accidental second approval so the task is RUNNING again.
    approvals.decide(audit.replay()[-1]["payload"]["approval_id"], True)
    res = router.route(t.task_id, act, approval_token=token.token_id)
    assert not res.ok  # file doesn't exist: executor failed honestly...
    # ...but policy authorized it: burn happens only on ok=True.
    assert approvals._tokens[token.token_id].used is False


class _Stub:
    def __init__(self):
        self.calls = 0

    def execute(self, action, task):
        from lakra.execution.registry import ExecuteResult
        self.calls += 1
        return ExecuteResult(ok=True, output="stub-did-it")


def test_token_burns_only_on_success(tmp_path):
    router, approvals, audit, t = _routed(tmp_path)
    stub = _Stub()
    router.tools.register("publish", stub)
    act = Action(kind="publish", target="blog-post", effect="consequential",
                 task_id=t.task_id)
    parked = router.route(t.task_id, act)
    assert not parked.ok and "PARKED" in (parked.error or "")
    assert stub.calls == 0
    approval_id = audit.replay()[-1]["payload"]["approval_id"]
    token = approvals.mint_token(
        approvals.decide(approval_id, True).approval_id)
    res = router.route(t.task_id, act, approval_token=token.token_id)
    assert res.ok and stub.calls == 1
    assert approvals._tokens[token.token_id].used is True
    assert t.history[-1].verdict == "ASK+approved"
    # Reuse: rejected, no execution, no fresh approval spawned.
    res2 = router.route(t.task_id, act, approval_token=token.token_id)
    assert not res2.ok and "TOKEN REJECTED" in (res2.error or "")
    assert stub.calls == 1
    assert approvals.pending() == []
