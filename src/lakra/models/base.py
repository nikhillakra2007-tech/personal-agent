"""Slice-02 interface, extended slice-06 with usage + typed errors.

Contract: give it text plus a token cap, get text AND usage back, with an
identity for audit/cost attribution. The only absolute rules: loopback/local
transports for LocalProvider, typed failures (never half-strings), and no
provider ever touching tools, browser, or policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ModelError(RuntimeError):
    """Base model failure. Never carries partial completions as success."""


class ProviderUnavailable(ModelError):
    """Runtime down, refused, or model not present."""


class ProviderTimeout(ModelError):
    """Generation exceeded its deadline."""


class BudgetExhausted(ModelError):
    """Token ledger has nothing left for this call."""


@dataclass
class ModelResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ModelProvider(ABC):
    """Contract every provider (local, cloud, …) conforms to."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identity, e.g. 'ollama/llama3.1:8b'. Used in audit/costs."""
        raise NotImplementedError

    @abstractmethod
    def complete(self, prompt: str, *, budget_tokens: int) -> ModelResponse:
        """One synchronous completion within the token cap.

        Raises ProviderUnavailable / ProviderTimeout / BudgetExhausted.
        budget_tokens bounds the COMPLETION (new tokens), not the prompt.
        """
        raise NotImplementedError
