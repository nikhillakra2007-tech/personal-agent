"""Slice-05: deterministic recovery alternates (no AI, just rules).

Two alternates, each tried at most once per step, then escalate:
  execution failure  -> re-query the selector fresh + scroll it into view,
                        then the controller retries the action exactly once.
  verification failure -> wait briefly for slow renders to settle, then the
                        controller re-verifies exactly once.

Finite by construction: the controller, not this module, counts attempts.
"""

from __future__ import annotations

SETTLE_WAIT_MS = 500


def recover_execution(page, selector: str) -> bool:
    """Prepare a fresh attempt after an execution failure. True if the
    alternate itself succeeded (element found + scrolled into view)."""
    try:
        if not selector or len(selector) > 500:
            return False
        loc = page.locator(selector).first
        if loc.count() == 0:
            return False
        loc.scroll_into_view_if_needed(timeout=5000)
        return True
    except Exception:
        return False


def recover_verification(page, wait_ms: int = SETTLE_WAIT_MS) -> None:
    """Let slow renders settle before the final re-verification."""
    try:
        page.wait_for_timeout(min(wait_ms, 2000))
    except Exception:
        pass
