"""Slice-19: measured model hint suggester (untrusted, additive only).

AnalyzerProposer asks the model for hint values under a frozen prompt and
merges them with the deterministic baseline under one iron rule: the model
may ADD keys determinism missed, but NEVER overrides a deterministic value
(conflicts are logged as overruled). validate_hints() remains the
acceptance gate, URLs are re-validated independently, and every failure
mode (bad JSON, bad hints, secrets, smuggling, timeout, exhaustion) falls
back to the deterministic result. No planner/runner/policy/router changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse
import json

from ..models.base import BudgetExhausted, ModelError
from .analyzer import validate_hints
from .planner import UnknownGoalError

GOAL_CAP_CHARS = 1000

PROMPT_TEMPLATE = (
    "Suggest browser-task hints as strict JSON (no other text) with any of"
    " these keys: url, expect_text, selector, text, submit_selector."
    " Rules: output JSON only; never invent URLs (only repeat one present"
    " in the goal, or omit the key); never include credentials, passwords,"
    " secrets, or tokens; values are short strings, except achieved which"
    " you must omit. Example: {\"expect_text\": \"pending\"}."
    " Task goal: {goal}")


@dataclass
class SuggestAttempt:
    model_name: str
    calls_made: int = 0
    tokens_used: int = 0
    parsed: bool = False
    overruled: list = field(default_factory=list)
    rejection_reason: str | None = None
    fallback_used: bool = True


def _valid_url(value: str) -> bool:
    try:
        parts = urlparse(value)
    except Exception:
        return False
    return parts.scheme in ("http", "https", "file") and bool(
        parts.netloc or parts.path)


class AnalyzerProposer:
    """Untrusted hint suggester. Additive-only merge, deterministic wins."""

    def __init__(self, provider, ledger=None) -> None:
        self.provider = provider
        self.ledger = ledger

    def suggest(self, goal_text: str, baseline: dict
                ) -> tuple[dict, SuggestAttempt]:
        """Returns (merged_hints, attempt). The informational 'intent' key
        from analyze() is planner-incompatible and is normalized away first;
        fallback comparison is against the normalized baseline."""
        attempt = SuggestAttempt(model_name=self.provider.name)
        clean = {k: v for k, v in baseline.items() if k != "intent"}
        prompt = PROMPT_TEMPLATE + "\nGoal: " + goal_text[:GOAL_CAP_CHARS]
        try:
            grant = (self.ledger.precheck(128) if self.ledger is not None
                     else 128)
            resp = self.provider.complete(prompt, budget_tokens=grant)
            if self.ledger is not None:
                self.ledger.charge(resp.total_tokens)
        except (ModelError, BudgetExhausted) as exc:
            attempt.calls_made += 1
            attempt.rejection_reason = f"provider: {exc}"
            return clean, attempt
        attempt.calls_made += 1
        attempt.tokens_used += resp.total_tokens
        try:
            obj = json.loads(self._extract(resp.text))
        except (json.JSONDecodeError, ValueError) as exc:
            attempt.rejection_reason = f"schema: {exc}"
            return clean, attempt
        if not isinstance(obj, dict):
            attempt.rejection_reason = "schema: not an object"
            return clean, attempt
        merged = dict(clean)
        for key, value in obj.items():
            if key in merged:
                attempt.overruled.append(key)  # deterministic wins, logged
                continue
            candidate = dict(merged)
            candidate[key] = value
            try:
                validate_hints(candidate)
            except UnknownGoalError as exc:
                attempt.rejection_reason = f"hints: {exc}"
                continue
            if key == "url" and not _valid_url(str(value)):
                attempt.rejection_reason = "url smuggling rejected"
                continue
            merged[key] = value
        attempt.parsed = True
        attempt.fallback_used = (merged == clean)
        return merged, attempt

    @staticmethod
    def _extract(text: str) -> str:
        s = text.strip()
        if "{" in s:
            s = s[s.find("{"):s.rfind("}") + 1]
        return s
