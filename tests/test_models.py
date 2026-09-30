"""ModelProvider tests: interface conformance via stub; no runtime exists."""

import pytest

from lakra.control.budgets import TokenLedger
from lakra.models.base import (
    BudgetExhausted,
    ModelProvider,
    ModelResponse,
)


class StubProvider(ModelProvider):
    def __init__(self, reply="stub"):
        self._reply = reply

    @property
    def name(self) -> str:
        return "test/stub"

    def complete(self, prompt: str, *, budget_tokens: int,
                 ledger=None) -> ModelResponse:
        if budget_tokens <= 0:
            raise BudgetExhausted("budget_tokens must be > 0")
        if ledger is not None:
            budget_tokens = ledger.precheck(budget_tokens)
        text = f"{self._reply}:{prompt[:8]}"
        if ledger is not None:
            ledger.charge(len(text))
        return ModelResponse(text=text, prompt_tokens=2,
                             completion_tokens=len(text))


def test_stub_conforms_to_interface():
    p = StubProvider()
    assert isinstance(p, ModelProvider)
    assert p.name == "test/stub"
    res = p.complete("hello world", budget_tokens=16)
    assert res.text == "stub:hello wo"
    assert res.total_tokens == 2 + len("stub:hello wo")


def test_stub_honors_ledger():
    p, ledger = StubProvider(), TokenLedger(10)
    with pytest.raises(BudgetExhausted):
        TokenLedger(0).precheck(5)
    p.complete("hi", budget_tokens=64, ledger=ledger)
    assert ledger.used_tokens > 0


def test_interface_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        ModelProvider()  # abstract: no runtime without a real provider


def test_incomplete_subclass_rejected():
    class Half(ModelProvider):
        @property
        def name(self):
            return "half"
    with pytest.raises(TypeError):
        Half()
