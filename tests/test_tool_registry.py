"""Registry tests: resolve, unknown rejection, opaque future names."""

import pytest

from lakra.execution.filesystem import FileSystemExecutor
from lakra.execution.registry import (
    ExecuteResult,
    ToolRegistry,
    UnknownToolError,
)


def test_register_and_resolve(tmp_path):
    tools = ToolRegistry()
    ex = FileSystemExecutor(tmp_path)
    tools.register("fs.read", ex)
    assert tools.resolve("fs.read") is ex
    assert "fs.read" in tools
    assert len(tools) == 1


def test_unknown_tool_rejected():
    tools = ToolRegistry()
    with pytest.raises(UnknownToolError):
        tools.resolve("browser.click")  # seam proven, capability absent


def test_executor_without_execute_rejected():
    with pytest.raises(TypeError):
        ToolRegistry().register("bad", object())


def test_result_truncates_long_output():
    r = ExecuteResult(ok=True, output="x" * 5000)
    assert len(r.output) <= 2100 and "truncated" in r.output
