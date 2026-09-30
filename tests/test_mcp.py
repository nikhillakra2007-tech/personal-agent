"""MCP tests: fake stdio server, default-deny specs, fail-closed behavior."""

import json
import os
import sys
from pathlib import Path

import pytest

from lakra.control.policy import Action
from lakra.control.tasks import Task
from lakra.execution.mcp import (
    MCPManager,
    MCPServerError,
    MCPServerSpec,
)
from lakra.execution.registry import ToolRegistry

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")


def spec(mode="ok", **kw):
    kw.setdefault("transport", "stdio")
    kw.setdefault("command", [sys.executable, FAKE, mode])
    kw.setdefault("timeout_s", 10)
    return MCPServerSpec(name="fake", **kw)


def task():
    return Task.create("g")


def test_discover_and_call_echo():
    mgr = MCPManager()
    try:
        assert mgr.add_server(spec()) == ["echo", "failer"]
        res = mgr.call_tool("fake", "echo", {"a": 1})
        assert res.ok and json.loads(res.output.split("echo:")[1]) == {"a": 1}
        fail = mgr.call_tool("fake", "failer", {})
        assert not fail.ok and fail.output == "boom"
    finally:
        mgr.close()


def test_allow_tools_filters_registration():
    mgr = MCPManager()
    try:
        mgr.add_server(spec(allow_tools=["echo"]))
        tools = ToolRegistry()
        assert mgr.register_all(tools) == ["mcp.fake.echo"]
        assert "mcp.fake.failer" not in tools
    finally:
        mgr.close()


def test_unknown_tool_call_errors():
    mgr = MCPManager()
    try:
        mgr.add_server(spec())
        res = mgr.call_tool("fake", "nope", {})
        assert not res.ok
    finally:
        mgr.close()


def test_slow_server_times_out():
    mgr = MCPManager()
    try:
        with pytest.raises(MCPServerError):
            mgr.add_server(spec("slow", timeout_s=2))
        assert "fake" not in mgr._links  # quarantined: nothing registered
    finally:
        mgr.close()


def test_garbage_server_quarantined():
    mgr = MCPManager()
    try:
        with pytest.raises(MCPServerError):
            mgr.add_server(spec("garbage", timeout_s=3))
        assert "fake" not in mgr._links
    finally:
        mgr.close()


def test_missing_command_quarantined():
    mgr = MCPManager()
    try:
        with pytest.raises(MCPServerError):
            mgr.add_server(MCPServerSpec(
                name="fake", transport="stdio",
                command=["/no/such/binary", FAKE], timeout_s=5))
    finally:
        mgr.close()


def test_spec_validation_rejects():
    with pytest.raises(MCPServerError):
        MCPServerSpec(name="bad name!").validate()
    with pytest.raises(MCPServerError):
        MCPServerSpec(name="x", transport="sms").validate()
    with pytest.raises(MCPServerError):
        MCPServerSpec(name="x", transport="http",
                      url="http://example.com/mcp").validate()  # not loopback
    MCPServerSpec(name="x", transport="http",
                  url="http://127.0.0.1:9000/mcp").validate()  # ok


def test_env_allowlist_names_only():
    os.environ["LAKRA_TEST_SECRET_XYZ"] = "topsecret"
    mgr = MCPManager()
    try:
        mgr.add_server(spec("secret-env", env_allowlist=[]))
        res = mgr.call_tool("fake", "envpeek", {})
        assert res.ok and "LAKRA_TEST_SECRET_XYZ" not in res.output
    finally:
        mgr.close()
        del os.environ["LAKRA_TEST_SECRET_XYZ"]
    os.environ["LAKRA_TEST_SECRET_XYZ"] = "topsecret"
    mgr = MCPManager()
    try:
        mgr.add_server(spec("secret-env",
                            env_allowlist=["LAKRA_TEST_SECRET_XYZ"]))
        res = mgr.call_tool("fake", "envpeek", {})
        assert res.ok and "topsecret" in res.output  # explicitly allowed
    finally:
        mgr.close()
        del os.environ["LAKRA_TEST_SECRET_XYZ"]


def test_executor_validates_json_target():
    mgr = MCPManager()
    try:
        mgr.add_server(spec())
        tools = ToolRegistry()
        mgr.register_all(tools)
        ex = tools.resolve("mcp.fake.echo")
        bad = ex.execute(Action(kind="mcp.fake.echo", target="not json",
                                effect="read", task_id="t"), task())
        assert not bad.ok
    finally:
        mgr.close()
