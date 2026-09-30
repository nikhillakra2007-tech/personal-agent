"""Analyzer model-assist tests: additive-only merge, overrule, fallbacks."""

import json

import pytest

from lakra.control.analyzer import analyze
from lakra.control.analyzer_model import AnalyzerProposer
from lakra.control.budgets import TokenLedger
from lakra.models.base import ModelProvider, ModelResponse, ProviderTimeout


class FakeProvider(ModelProvider):
    def __init__(self, mode="valid"):
        self.mode = mode
        self.calls = 0
        self.prompts = []

    @property
    def name(self):
        return "test/fake-hints"

    def complete(self, prompt, *, budget_tokens):
        self.calls += 1
        self.prompts.append(prompt)
        if self.mode == "timeout":
            raise ProviderTimeout("slow")
        texts = {
            "valid": json.dumps({"expect_text": "pending",
                                 "selector": "#toggle"}),
            "conflict": json.dumps({"expect_text": "WRONG",
                                     "selector": "#toggle"}),
            "prose": "just look around",
            "secret": json.dumps({"text": "hunter2 password"}),
            "smuggle": json.dumps({"url": "not-a-url-at-all"}),
            "badshape": json.dumps({"model": "x"}),
        }
        text = texts[self.mode]
        return ModelResponse(text=text, prompt_tokens=4,
                             completion_tokens=len(text))


def baseline():
    return analyze("List my pending courses at file:///c.html")


def test_valid_additive_merge():
    pp = AnalyzerProposer(FakeProvider("valid"))
    merged, attempt = pp.suggest("List my pending courses", {
        "url": "file:///c.html"})
    assert attempt.parsed and not attempt.fallback_used
    assert merged["url"] == "file:///c.html"  # baseline kept
    assert merged["selector"] == "#toggle"  # model added
    assert attempt.tokens_used > 0


def test_conflict_deterministic_wins_logged():
    pp = AnalyzerProposer(FakeProvider("conflict"))
    merged, attempt = pp.suggest("List my pending courses",
                                 dict(baseline()))
    assert merged["expect_text"] == "pending"  # deterministic, not WRONG
    assert "expect_text" in attempt.overruled
    assert merged["selector"] == "#toggle"  # absent key still added


def clean_base():
    return {k: v for k, v in baseline().items() if k != "intent"}


def test_prose_falls_back_entirely():
    pp = AnalyzerProposer(FakeProvider("prose"))
    merged, attempt = pp.suggest("List my pending courses", dict(baseline()))
    assert merged == clean_base() and attempt.fallback_used
    assert "schema" in (attempt.rejection_reason or "")


def test_secret_shaped_suggestion_rejected():
    pp = AnalyzerProposer(FakeProvider("secret"))
    merged, attempt = pp.suggest("Fill the form field", {})
    assert "text" not in merged and attempt.fallback_used


def test_url_smuggling_rejected():
    pp = AnalyzerProposer(FakeProvider("smuggle"))
    merged, attempt = pp.suggest("List things", {})
    assert "url" not in merged and attempt.fallback_used
    assert "smuggling" in (attempt.rejection_reason or "")


def test_unknown_keys_rejected():
    pp = AnalyzerProposer(FakeProvider("badshape"))
    merged, attempt = pp.suggest("List things", {})
    assert merged == {} and attempt.fallback_used


def test_timeout_and_ledger_paths():
    pp = AnalyzerProposer(FakeProvider("timeout"))
    merged, attempt = pp.suggest("List things", {"url": "file:///c.html"})
    assert merged == {"url": "file:///c.html"} and "provider" in (
        attempt.rejection_reason or "")
    ledger = TokenLedger(0)
    pp = AnalyzerProposer(FakeProvider("valid"), ledger=ledger)
    merged, attempt = pp.suggest("List things", {})
    assert merged == {} and ledger.used_tokens == 0


def test_prompt_frozen_no_page_content():
    prov = FakeProvider("valid")
    AnalyzerProposer(prov).suggest("List things", {})
    prompt = prov.prompts[0]
    assert "strict JSON" in prompt and "never invent URLs" in prompt
    assert "snapshot" not in prompt.lower()


def test_baseline_untouched_by_suggest():
    base = dict(baseline())
    AnalyzerProposer(FakeProvider("conflict")).suggest("List my pending", base)
    assert base == baseline()  # input dict never mutated
