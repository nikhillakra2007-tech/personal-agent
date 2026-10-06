"""V2-07: provider construction for model planning (isolated).

The ModelProvider interface itself lives in lakra.models.base and
the Ollama implementation in lakra.models.local; this module only
builds a provider from operator configuration so the CLI and the
planner never touch transport details. Ollama stays optional: with
no explicit model configuration nothing here is ever constructed,
and every construction failure maps to the deterministic fallback
upstream (never a launch, never a browser).

Supported specs:
  ollama:<model>[@base-url]  (loopback only, enforced by
                              LocalProvider itself)
"""

from __future__ import annotations


class ModelProviderConfigError(ValueError):
    """Unusable model configuration. Fail-closed before any call."""


def build_provider(spec: str = "", *, timeout_s: int = 60,
                   base_url: str = "http://127.0.0.1:11434"):
    """Build a ModelProvider from an operator spec string.

    Empty spec means "no model configured" (caller keeps the
    deterministic planner and never calls this). "name" alone uses
    the loopback default; "ollama:name" pins the scheme explicitly;
    a third @-segment overrides the base URL (still loopback-only:
    anything else raises here, before any HTTP exists).
    """
    from ..models.local import LocalProvider
    if not isinstance(spec, str) or not spec.strip():
        raise ModelProviderConfigError("no model configured")
    text = spec.strip()
    if text.startswith("ollama:"):
        text = text[len("ollama:"):]
    elif text.startswith("ollama/"):
        text = text[len("ollama/"):]
    if "@" in text:
        model, _, url = text.partition("@")
        base_url = url.strip() or base_url
    else:
        model = text
    if not model or not isinstance(timeout_s, int) \
            or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise ModelProviderConfigError(
            "model name must be non-empty and timeout positive")
    try:
        return LocalProvider(model=model, base_url=base_url,
                             timeout_s=timeout_s)
    except ValueError as exc:
        raise ModelProviderConfigError(str(exc)) from None


def provider_available(provider) -> bool:
    """Best-effort presence probe (fast, bounded by the provider).
    False means "fall back now" — never an error, never a retry."""
    try:
        return bool(provider.available())
    except Exception:
        return False
