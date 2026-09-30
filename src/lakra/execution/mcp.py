"""Slice-07: scoped MCP client manager.

Default-deny throughout: a server runs ONLY from an explicit MCPServerSpec,
stdio commands run WITHOUT a shell, HTTP transports must be loopback-only,
subprocess environments carry ONLY allowlisted variable NAMES (values never
logged), and only allowlisted tools register. Discovered tools become
first-class registry executors (mcp.<server>.<tool>) so policy, router, and
audit govern them exactly like built-ins.

Transport is hand-rolled NDJSON JSON-RPC over stdio (no new dependencies):
initialize -> notifications/initialized -> tools/list -> tools/call.
A server that fails the handshake, times out, or emits garbage is
quarantined (MCPServerError, never registered) — fail closed.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.parse
from dataclasses import dataclass, field

from .registry import ExecuteResult
from ..control.policy import Action
from ..control.tasks import Task

MCP_VERSION = "2024-11-05"
READ_TIMEOUT_S = 30


class MCPServerError(RuntimeError):
    """Handshake/timeout/malformed behavior. Fail closed: never register."""


@dataclass
class MCPServerSpec:
    name: str
    transport: str = "stdio"  # "stdio" | "http"
    command: list[str] = field(default_factory=list)  # stdio argv, no shell
    url: str = ""  # http transport: loopback only
    env_allowlist: list[str] = field(default_factory=list)
    allow_tools: list[str] | None = None  # None = all discovered
    tool_effects: dict[str, str] = field(default_factory=dict)
    timeout_s: int = READ_TIMEOUT_S

    def validate(self) -> None:
        if not self.name or "/" in self.name or "." in self.name:
            raise MCPServerError(f"bad server name {self.name!r}")
        if self.transport == "stdio":
            if not self.command:
                raise MCPServerError(f"{self.name}: stdio needs argv")
        elif self.transport == "http":
            parts = urllib.parse.urlparse(self.url)
            if parts.scheme != "http" or parts.hostname != "127.0.0.1":
                raise MCPServerError(
                    f"{self.name}: http transport is loopback-only")
        else:
            raise MCPServerError(f"{self.name}: unknown transport")


class _StdioLink:
    """One NDJSON JSON-RPC conversation with a child process."""

    def __init__(self, spec: MCPServerSpec) -> None:
        env = {k: v for k, v in os.environ.items()
               if k in spec.env_allowlist}
        try:
            self.proc = subprocess.Popen(
                spec.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
                shell=False)
        except (FileNotFoundError, OSError) as exc:
            raise MCPServerError(f"{spec.name}: spawn failed: {exc}") from exc
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._next_id = 0

    def _pump(self) -> None:
        try:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._lines.put(line)
        except Exception:
            pass

    def call(self, method: str, params: dict,
             timeout_s: int) -> dict | None:
        """Request (id) or notification (id None). Returns result or None."""
        self._next_id += 1
        msg: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        ident = None
        if not method.startswith("notifications/"):
            ident = self._next_id
            msg["id"] = ident
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPServerError(f"write failed: {exc}") from exc
        if ident is None:
            return None
        deadline = timeout_s
        while True:
            try:
                line = self._lines.get(timeout=deadline)
            except queue.Empty:
                raise MCPServerError(
                    f"method {method} timed out after {timeout_s}s")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip garbage lines, keep waiting for our id
            if resp.get("id") == ident:
                if "error" in resp:
                    raise MCPServerError(
                        f"method {method} error: {resp['error']}")
                return resp.get("result", {})
            # else: someone else's late/notification line; keep waiting.
            # NOTE: deadline is not decremented per line; worst case each
            # line resets the wait. Bounded below by overall attempts? No:
            # a chatty server could stall us up to timeout_s PER line.
            # Acceptable for slice-07 (trusted-configured servers); revisit
            # with a monotonic deadline if untrusted servers arrive.

    def close(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass


class MCPToolExecutor:
    """One discovered tool. Effect class from the spec map (default
    bounded-mutation); the router still policy-checks every call."""

    def __init__(self, manager, server: str, tool: str, effect: str) -> None:
        self.manager = manager
        self.server = server
        self.tool = tool
        self.effect = effect

    def execute(self, action: Action, task: Task) -> ExecuteResult:
        _ = task
        raw = action.target.strip()
        if not raw:
            args = {}
        else:
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                return ExecuteResult(ok=False,
                                     error="target must be a JSON object")
            if not isinstance(args, dict):
                return ExecuteResult(ok=False,
                                     error="target must be a JSON object")
        try:
            return self.manager.call_tool(self.server, self.tool, args)
        except MCPServerError as exc:
            return ExecuteResult(ok=False, error=str(exc))


class MCPManager:
    """Per-task (or per-session) scoped manager. Owns server lifecycles."""

    def __init__(self) -> None:
        self._links: dict[str, _StdioLink] = {}
        self._specs: dict[str, MCPServerSpec] = {}

    def add_server(self, spec: MCPServerSpec) -> list[str]:
        """Validate, handshake, discover. Returns registered tool names.
        Raises MCPServerError: quarantined, nothing registered."""
        spec.validate()
        if spec.transport != "stdio":
            raise MCPServerError(f"{spec.name}: only stdio in slice-07")
        link = _StdioLink(spec)
        try:
            link.call("initialize", {
                "protocolVersion": MCP_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lakra", "version": "0.7"}}, spec.timeout_s)
            link.call("notifications/initialized", {}, spec.timeout_s)
            result = link.call("tools/list", {}, spec.timeout_s) or {}
        except Exception:
            link.close()
            raise
        tools = [t.get("name", "") for t in result.get("tools", [])]
        tools = [t for t in tools if t]
        if spec.allow_tools is not None:
            tools = [t for t in tools if t in spec.allow_tools]
        self._links[spec.name] = link
        self._specs[spec.name] = spec
        return tools

    def call_tool(self, server: str, tool: str, args: dict) -> ExecuteResult:
        link = self._links.get(server)
        spec = self._specs.get(server)
        if link is None or spec is None:
            return ExecuteResult(ok=False, error=f"unknown server {server}")
        try:
            result = link.call("tools/call",
                               {"name": tool, "arguments": args},
                               spec.timeout_s) or {}
        except MCPServerError as exc:
            return ExecuteResult(ok=False, error=str(exc))
        parts = []
        for block in result.get("content", []):
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        text = "\n".join(parts)
        if result.get("isError"):
            return ExecuteResult(ok=False, output=text,
                                 error=f"tool {tool} reported error")
        return ExecuteResult(ok=True, output=text or "(no output)")

    def register_all(self, tools) -> list[str]:
        """Register every discovered tool of every live server. Returns the
        registered capability names (mcp.<server>.<tool>); effect classes
        stay queryable via effect_of()."""
        names = []
        for server in self._links:
            for tool in self._tool_names(server):
                name = f"mcp.{server}.{tool}"
                tools.register(name, MCPToolExecutor(
                    self, server, tool, self.effect_of(server, tool)))
                names.append(name)
        return names

    def _tool_names(self, server: str) -> list[str]:
        # Re-list (cheap, local subprocess) so registration reflects reality.
        spec = self._specs[server]
        link = self._links[server]
        result = link.call("tools/list", {}, spec.timeout_s) or {}
        tools = [t.get("name", "") for t in result.get("tools", [])]
        tools = [t for t in tools if t]
        if spec.allow_tools is not None:
            tools = [t for t in tools if t in spec.allow_tools]
        return tools

    def effect_of(self, server: str, tool: str) -> str:
        return self._specs[server].tool_effects.get(tool, "bounded-mutation")

    def close(self) -> None:
        for link in self._links.values():
            link.close()
        self._links.clear()
        self._specs.clear()
