"""Router tests: choke-point semantics on every verdict path."""

import pytest

from lakra.control.approvals import Approvals
from lakra.control.audit import AuditLog
from lakra.control.policy import Action
from lakra.control.registry import TaskRegistry
from lakra.control.scheduler import Scheduler
from lakra.control.tasks import Budget, Status, Task
from lakra.execution.filesystem import FileSystemExecutor
from lakra.execution.registry import ToolRegistry, UnknownToolError
from lakra.execution.router import RouterError, ToolRouter
from lakra.execution.terminal import ShellExecutor


def stack(tmp_path, **kw):
    kw.setdefault("allowed_tools", ["fs", "terminal"])
    kw.setdefault("allowed_domains", ["x.example"])
    kw.setdefault("allowed_paths", [str(tmp_path)])
    registry = TaskRegistry()
    audit = AuditLog(tmp_path / "a.jsonl")
    approvals = Approvals(registry)
    sched = Scheduler(registry)
    tools = ToolRegistry()
    tools.register("fs.read", FileSystemExecutor(tmp_path / "ws"))
    tools.register("fs.write", tools.resolve("fs.read"))
    tools.register("fs.list", tools.resolve("fs.read"))
    tools.register("terminal.run", ShellExecutor(tmp_path / "ws"))
    router = ToolRouter(registry, tools, approvals, audit, sched)
    task = registry.add(Task.create("g", **kw))
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)
    return router, task, tmp_path


def act(kind, target, effect, task_id):
    return Action(kind=kind, target=target, effect=effect, task_id=task_id)


class _Stub:
    """Counts executions; proves BLOCK/ASK paths never reach an executor."""

    def __init__(self):
        self.calls = 0

    def execute(self, action, task):
        self.calls += 1
        from lakra.execution.registry import ExecuteResult
        return ExecuteResult(ok=True, output="stub")


def test_allow_executes_and_records(tmp_path):
    router, task, _ = stack(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "n.txt").write_text("hi")
    res = router.route(task.task_id,
                       act("fs.read", str(ws / "n.txt"), "read", task.task_id))
    assert res.ok and res.output == "hi"
    assert task.history and task.history[-1].verdict == "ALLOW"
    assert task.verification.state == "UNVERIFIED"  # no verifier yet
    types = [e["type"] for e in router.audit.replay()]
    assert {"TOOL_SELECTED", "ACTION_ALLOWED", "EXECUTION_STARTED",
            "EXECUTION_COMPLETED"} <= set(types)


def test_block_never_executes(tmp_path):
    router, task, _ = stack(tmp_path)
    stub = _Stub()
    router.tools.register("captcha.defeat", stub)
    res = router.route(task.task_id,
                       act("captcha.defeat", "x", "read", task.task_id))
    assert not res.ok and "BLOCKED" in res.error
    assert stub.calls == 0
    assert task.history == []
    assert "ACTION_DENIED" in [e["type"] for e in router.audit.replay()]


def test_ask_parks_without_executing(tmp_path):
    router, task, _ = stack(tmp_path)
    stub = _Stub()
    router.tools.register("browser.submit", stub)
    res = router.route(task.task_id, act(
        "browser.submit", "https://x.example/f", "consequential",
        task.task_id))
    assert not res.ok and "PARKED" in res.error
    assert stub.calls == 0
    assert router.registry.get(task.task_id).status == Status.WAITING_APPROVAL
    assert task.history == []


def test_ask_approve_recheck_reroute(tmp_path):
    router, task, _ = stack(tmp_path)
    ws = tmp_path / "ws"
    a = act("fs.write", f"{ws / 'w.txt'}\n---\nbody", "bounded-mutation",
            task.task_id)
    # Force the ASK path with an out-of-bounds variant, then approve it.
    a2 = act("fs.write", "C:/elsewhere/w.txt", "bounded-mutation",
             task.task_id)
    res = router.route(task.task_id, a2)
    assert not res.ok
    approval_id = router.audit.replay()[-1]["payload"]["approval_id"]
    decided = router.approvals.decide(approval_id, True)
    assert router.approvals.recheck(decided) == "ASK"  # still gated: same action
    router.approvals.must_be_gated("ASK")
    # Approval authorizes exactly this action; routing the in-bounds twin is
    # a fresh ALLOW evaluated on its own merits.
    res2 = router.route(task.task_id, a)
    assert res2.ok


def test_unknown_tool_rejected_and_logged(tmp_path):
    router, task, _ = stack(tmp_path)
    with pytest.raises(UnknownToolError):
        router.route(task.task_id,
                     act("browser.click", "https://x.example", "reversible",
                         task.task_id))
    assert "EXECUTION_FAILED" in [e["type"] for e in router.audit.replay()]
    assert task.history == []


def test_ownerless_task_refused(tmp_path):
    router, task, _ = stack(tmp_path)
    router.registry.release(task.task_id, "exec")
    with pytest.raises(RouterError):
        router.route(task.task_id,
                     act("fs.list", str(tmp_path), "read", task.task_id))


def test_over_budget_refused(tmp_path):
    router, task, _ = stack(tmp_path)
    task.budget = Budget(max_steps=0, max_tokens_cents=0, max_minutes=30)
    with pytest.raises(RouterError):
        router.route(task.task_id,
                     act("fs.list", str(tmp_path), "read", task.task_id))


def test_attempt_id_propagates_and_closes(tmp_path):
    router, task, _ = stack(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    res = router.route(task.task_id,
                       act("fs.list", str(ws), "read", task.task_id))
    assert res.ok and res.attempt_id
    assert router.ledger.open_count() == 0
    types = [e["type"] for e in router.audit.replay()]
    assert "EXECUTION_STARTED" in types and "EXECUTION_COMPLETED" in types


def test_open_duplicate_suppressed_at_router(tmp_path):
    router, task, _ = stack(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    a = act("fs.list", str(ws), "read", task.task_id)
    # Simulate a wedged first attempt: open row with no close.
    wedged = router.ledger.begin(task.task_id, a.kind, a.target, a.effect)
    assert wedged
    res = router.route(task.task_id, a)
    assert not res.ok and res.error.startswith("DUPLICATE")
    assert any(e["type"] == "DUPLICATE_SUPPRESSED"
               for e in router.audit.replay())
    # No executor ran for the suppressed duplicate: history untouched.
    assert task.history == []
