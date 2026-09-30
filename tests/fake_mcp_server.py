"""Fake MCP server for tests (NDJSON JSON-RPC over stdio).

Usage: fake_mcp_server.py [ok|slow|garbage|secret-env]
  ok:         initialize + tools/list(echo, failer) + tools/call echo/failer
  slow:       tools/call sleeps 30 s (client must time out first)
  garbage:    emits one non-JSON line, then closes stdin/stdout (EOF)
  secret-env: tools/call 'envpeek' returns os.environ as JSON text
"""

import json
import os
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def serve():
    if MODE == "garbage":
        # Poison the stream, then die: client must time out and quarantine.
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        return
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        ident = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": ident, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "fake", "version": "0.1"}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            if MODE == "slow":
                time.sleep(30)  # handshake itself must time out client-side
            tools = [{"name": "echo"}, {"name": "failer"}]
            if MODE == "secret-env":
                tools.append({"name": "envpeek"})
            send({"jsonrpc": "2.0", "id": ident,
                  "result": {"tools": tools}})
        elif method == "tools/call":
            name = msg.get("params", {}).get("name", "")
            args = msg.get("params", {}).get("arguments", {})
            if MODE == "slow":
                time.sleep(30)
            if MODE == "garbage":
                sys.stdout.write("this is not json\n")
                sys.stdout.flush()
                return
            if name == "echo":
                send({"jsonrpc": "2.0", "id": ident, "result": {
                    "content": [{"type": "text",
                                 "text": "echo:" + json.dumps(args)}]}})
            elif name == "failer":
                send({"jsonrpc": "2.0", "id": ident, "result": {
                    "content": [{"type": "text", "text": "boom"}],
                    "isError": True}})
            elif name == "envpeek":
                send({"jsonrpc": "2.0", "id": ident, "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(dict(os.environ))}]}})
            else:
                send({"jsonrpc": "2.0", "id": ident, "error": {
                    "code": -32601, "message": "unknown tool"}})
        else:
            if ident is not None:
                send({"jsonrpc": "2.0", "id": ident, "error": {
                    "code": -32601, "message": "unknown method"}})


serve()
