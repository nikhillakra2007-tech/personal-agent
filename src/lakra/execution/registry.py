"""Slice-03: tool registry.

Maps capability names to executor objects. Names are opaque strings; future
capabilities (browser.*, model.*, mcp.*) stay UNREGISTERED until their slice
— resolving one raises UnknownToolError, which is how the seam is proven
without building the capability. No MCP-shaped schema is invented here;
slice-07 designs MCP properly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


MAX_OUTPUT_CHARS = 2000


@dataclass
class ExecuteResult:
    ok: bool
    output: str = ""
    error: str | None = None
    attempt_id: str = ""  # slice-17: ledger id of this execution attempt

    def __post_init__(self) -> None:
        if len(self.output) > MAX_OUTPUT_CHARS:
            self.output = (self.output[:MAX_OUTPUT_CHARS]
                           + f"...[truncated {len(self.output)} chars total]")


class UnknownToolError(KeyError):
    """No executor registered under this capability name."""


class ToolRegistry:
    """Name -> executor (duck-typed: execute(action, task) -> ExecuteResult)."""

    def __init__(self) -> None:
        self._executors: dict[str, object] = {}

    def register(self, name: str, executor: object) -> None:
        if not hasattr(executor, "execute"):
            raise TypeError(f"executor for {name!r} lacks execute()")
        self._executors[name] = executor

    def resolve(self, name: str) -> object:
        try:
            return self._executors[name]
        except KeyError:
            raise UnknownToolError(
                f"no executor registered for {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._executors

    def __len__(self) -> int:
        return len(self._executors)
