"""Slice-07 demo: MCP end-to-end, token one-shot, TTL fail-closed.

1. Fake MCP server: discover -> register -> route L1 tool through policy.
2. Hostile MCP proposal: BLOCKed, zero execution.
3. Approval token: park -> approve -> mint -> execute-once -> burn; reuse dies.
4. TTL sweep: expired approval fails closed (task cancelled).
5. Quarantine: garbage server never registers.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice07.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.policy import Action  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.mcp import (  # noqa: E402
    MCPManager,
    MCPServerError,
    MCPServerSpec,
)
from lakra.execution.registry import ExecuteResult, ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

FAKE = str(ROOT / "tests" / "fake_mcp_server.py")
LOG_PATH = ROOT / "var" / "audit-slice07.jsonl"
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
approvals = Approvals(registry)
tools = ToolRegistry()
router = ToolRouter(registry, tools, approvals, audit, Scheduler(registry))
mgr = MCPManager()
try:
    # 1. discover + register + route ------------------------------------------------
    found = mgr.add_server(MCPServerSpec(
        name="demo", command=[sys.executable, FAKE, "ok"], timeout_s=15))
    audit.log("MCP_SERVER_ADDED", "-", {"server": "demo", "tools": found})
    names = mgr.register_all(tools)
    assert names == ["mcp.demo.echo", "mcp.demo.failer"], names
    task = registry.add(Task.create(
        "MCP demo", allowed_tools=["mcp"], allowed_domains=[],
        allowed_paths=[]))
    audit.log("TASK_CREATED", task.task_id, {"goal": task.goal})
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)
    res = router.route(task.task_id, Action(
        kind="mcp.demo.echo", target=json.dumps({"q": 1}),
        effect="bounded-mutation", task_id=task.task_id))
    assert res.ok and '"q": 1' in res.output, res
    print("1. MCP echo routed through policy (ALLOW) and executed")

    # 2. hostile proposal BLOCKed -------------------------------------------------------
    bad = Action(kind="mcp.demo.echo", target="{}", effect="blocked",
                 task_id=task.task_id)
    res = router.route(task.task_id, bad)
    assert not res.ok and "BLOCKED" in (res.error or "")
    print("2. hostile MCP proposal BLOCKed, zero execution")

    # 3. token one-shot -------------------------------------------------------------------
    l3 = Action(kind="publish", target="blog-post", effect="consequential",
                task_id=task.task_id)

    class _Stub:
        def execute(self, action, task):
            return ExecuteResult(ok=True, output="published")

    tools.register("publish", _Stub())
    parked = router.route(task.task_id, l3)
    assert "PARKED" in (parked.error or "")
    approval_id = audit.replay()[-1]["payload"]["approval_id"]
    token = approvals.mint_token(
        approvals.decide(approval_id, True).approval_id)
    done = router.route(task.task_id, l3, approval_token=token.token_id)
    assert done.ok, done.error
    assert task.history[-1].verdict == "ASK+approved"
    retry = router.route(task.task_id, l3, approval_token=token.token_id)
    assert not retry.ok and "TOKEN REJECTED" in (retry.error or "")
    print("3. token executed once (ASK+approved), burned; reuse rejected")

    # 4. TTL fail-closed ---------------------------------------------------------------------
    t2 = registry.add(Task.create("expiring", allowed_tools=["fs"],
                                  allowed_domains=[], allowed_paths=[]))
    registry.checkout(t2.task_id, "exec")
    registry.set_status(t2.task_id, Status.RUNNING)
    approvals.request(t2.task_id, Action(
        kind="fs.list", target="C:/x", effect="bounded-mutation",
        task_id=t2.task_id), ttl_s=-1)
    assert approvals.sweep() != []
    assert registry.get(t2.task_id).status == Status.CANCELLED
    print("4. expired approval swept: task cancelled, fail closed")

    # 5. quarantine ------------------------------------------------------------------------------
    try:
        mgr.add_server(MCPServerSpec(
            name="bad", command=[sys.executable, FAKE, "garbage"],
            timeout_s=3))
        raise SystemExit("FAIL: garbage server registered")
    except MCPServerError as exc:
        print(f"5. garbage server quarantined ({str(exc)[:60]}...)")
finally:
    mgr.close()

print(f"   log -> {LOG_PATH} ({len(audit.replay())} events)")
print("REPLAY OK")
