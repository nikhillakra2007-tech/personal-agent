"""Slice-06: LocalProvider — Ollama behind the vendor-neutral interface.

Ollama is an implementation detail of this file: loopback HTTP only
(127.0.0.1, never LAN/cloud), stdlib urllib (no net dependencies), typed
failures, token-cap pre-check against an optional ledger. Nothing in this
file touches tools, browser, policy, or audit.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import (
    BudgetExhausted,
    ModelError,
    ModelProvider,
    ModelResponse,
    ProviderTimeout,
    ProviderUnavailable,
)


class LocalProvider(ModelProvider):
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434",
                 timeout_s: int = 60) -> None:
        if not base_url.startswith("http://127.0.0.1:"):
            raise ValueError("LocalProvider is loopback-only")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def available(self) -> bool:
        """Fast probe: is the runtime up AND is our model present?"""
        try:
            tags = self._get("/api/tags", timeout_s=5)
            return any(m.get("name") == self.model
                       for m in tags.get("models", []))
        except ModelError:
            return False

    def complete(self, prompt: str, *, budget_tokens: int,
                 ledger=None) -> ModelResponse:
        if budget_tokens <= 0:
            raise BudgetExhausted("budget_tokens must be > 0")
        if ledger is not None:
            budget_tokens = ledger.precheck(budget_tokens)
        body = {"model": self.model, "prompt": prompt, "stream": False,
                "options": {"num_predict": budget_tokens}}
        try:
            resp = self._post("/api/generate", body, timeout_s=self.timeout_s)
        except ModelError:
            raise
        text = resp.get("response", "")
        usage = (resp.get("prompt_eval_count", 0),
                 resp.get("eval_count", 0))
        if ledger is not None:
            ledger.charge(usage[0] + usage[1])
        return ModelResponse(text=text, prompt_tokens=usage[0],
                             completion_tokens=usage[1])

    def _post(self, path: str, body: dict, timeout_s: int) -> dict:
        data = json.dumps(body).encode("utf-8")
        return self._request(path, data, timeout_s)

    def _get(self, path: str, timeout_s: int) -> dict:
        return self._request(path, None, timeout_s)

    def _request(self, path: str, data: bytes | None,
                 timeout_s: int) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as fh:
                return json.loads(fh.read().decode("utf-8"))
        except OSError as exc:
            # URLError subclasses OSError; unwrapped socket errors (reset,
            # refused, aborted) arrive here directly. Either shape maps to
            # typed failures — nothing escapes raw.
            reason = (exc.reason if isinstance(exc, urllib.error.URLError)
                      else exc)
            if isinstance(reason, TimeoutError):
                raise ProviderTimeout(
                    f"ollama timed out after {timeout_s}s") from exc
            raise ProviderUnavailable(f"ollama unreachable: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderUnavailable(
                f"ollama returned garbage: {exc}") from exc
