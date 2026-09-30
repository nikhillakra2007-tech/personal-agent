"""LocalProvider tests against a fake Ollama (no live model required)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lakra.control.budgets import TokenLedger
from lakra.models.base import (
    BudgetExhausted,
    ModelResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from lakra.models.local import LocalProvider


class FakeOllama(BaseHTTPRequestHandler):
    mode = "ok"  # ok | garbage | slow | missing-model

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            if self.mode == "missing-model":
                return self._send({"models": [{"name": "other:1b"}]})
            return self._send({"models": [{"name": "tiny:1b"}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/api/tags":
            if self.mode == "missing-model":
                return self._send({"models": [{"name": "other:1b"}]})
            return self._send({"models": [{"name": "tiny:1b"}]})
        if self.path == "/api/generate":
            if self.mode == "garbage":
                self.send_response(200)
                self.end_headers()
                return self.wfile.write(b"not-json{{{")
            if self.mode == "slow":
                import time
                time.sleep(5)
                return self._send({"response": "late"})
            return self._send({"response": "hi there",
                               "prompt_eval_count": 3, "eval_count": 2})
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), FakeOllama)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def test_happy_path_with_usage_and_ledger(server):
    FakeOllama.mode = "ok"
    p = LocalProvider("tiny:1b", base_url=server)
    assert p.available() is True
    ledger = TokenLedger(100)
    res = p.complete("hello", budget_tokens=16, ledger=ledger)
    assert isinstance(res, ModelResponse) and res.text == "hi there"
    assert (res.prompt_tokens, res.completion_tokens) == (3, 2)
    assert ledger.used_tokens == 5


def test_unavailable_ollama_fails_cleanly():
    p = LocalProvider("tiny:1b", base_url="http://127.0.0.1:1")
    assert p.available() is False
    with pytest.raises(ProviderUnavailable):
        p.complete("hi", budget_tokens=8)


def test_missing_model_reports_unavailable(server):
    FakeOllama.mode = "missing-model"
    assert LocalProvider("tiny:1b", base_url=server).available() is False


def test_slow_server_times_out(server):
    FakeOllama.mode = "slow"
    p = LocalProvider("tiny:1b", base_url=server, timeout_s=1)
    with pytest.raises(ProviderTimeout):
        p.complete("hi", budget_tokens=8)


def test_garbage_response_is_unavailable_not_success(server):
    FakeOllama.mode = "garbage"
    with pytest.raises(ProviderUnavailable):
        LocalProvider("tiny:1b", base_url=server).complete("hi",
                                                           budget_tokens=8)


def test_zero_budget_refused_before_http(server):
    FakeOllama.mode = "ok"
    p = LocalProvider("tiny:1b", base_url=server)
    with pytest.raises(BudgetExhausted):
        p.complete("hi", budget_tokens=0)
    with pytest.raises(BudgetExhausted):
        p.complete("hi", budget_tokens=8, ledger=TokenLedger(0))


def test_loopback_only():
    with pytest.raises(ValueError):
        LocalProvider("m", base_url="http://192.168.1.2:11434")
