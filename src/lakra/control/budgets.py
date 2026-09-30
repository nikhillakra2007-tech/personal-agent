"""Slice-06: token budget ledger (the path overview.md reserved).

One ledger per task (or per session): every model call pre-checks the cap
and charges actual usage afterwards. Empty ledgers refuse BEFORE any HTTP
is sent. Owner of the counter is the ledger; the provider only reports
usage. Exhaustion refuses the request — it never cancels tasks (that
mapping belongs to a future planner).
"""

from __future__ import annotations

from ..models.base import BudgetExhausted


class TokenLedger:
    def __init__(self, limit_tokens: int) -> None:
        if limit_tokens < 0:
            raise ValueError("limit_tokens must be >= 0")
        self.limit_tokens = limit_tokens
        self.used_tokens = 0
        self._task_id: str | None = None
        self._store = None

    def attach(self, task_id: str, store) -> TokenLedger:
        """Bind usage persistence. Restores previously charged tokens."""
        from . import task_store
        self._task_id = task_id
        self._store = store
        _, tokens = task_store.get_usage(store, task_id)
        self.used_tokens = tokens
        return self

    @property
    def remaining(self) -> int:
        return max(0, self.limit_tokens - self.used_tokens)

    def precheck(self, want_tokens: int) -> int:
        """Return the grantable amount, or raise BudgetExhausted."""
        grant = min(want_tokens, self.remaining)
        if grant <= 0:
            raise BudgetExhausted(
                f"token budget exhausted "
                f"(used {self.used_tokens}/{self.limit_tokens})")
        return grant

    def charge(self, actual_tokens: int) -> None:
        if actual_tokens < 0:
            raise ValueError("actual_tokens must be >= 0")
        self.used_tokens += actual_tokens
        if self._store is not None and self._task_id is not None:
            from . import task_store
            task_store.record_tokens(self._store, self._task_id,
                                     self.used_tokens)
