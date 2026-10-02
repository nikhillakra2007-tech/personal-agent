"""V2-01: deterministic decomposition contract.

High-level prose -> ordered road-goal list -> the existing V1
chain machinery. Pure functions only: split prose into 2-4 segments,
shape each segment through the existing shaper, attach the road by
the dispatcher's own key vocabulary, and enforce the chain's 2-4 leg
cap. No browser, no policy, no approvals, no audit, no persistence,
no lifecycle, no model calls.

Each segment must be independently shapable (a road that needs a URL
carries its own URL in its segment); nothing is threaded between
segments and nothing is invented. Search legs keep the shaper's
submit_phrase: the submit selector is grounded against the observed
submit inventory at the execution edge (same precedent as the
single-goal CLI path), and the final gate before execution is the
real validate_chain() on the exact dicts run_chain() receives.

A future model proposer implements this same shape
(prose in, road-goal list out) without touching the dispatcher,
policy, or approval machinery.
"""

from __future__ import annotations

import re

from .analyzer import UnknownGoalError
from .shaping import shape_goal

MIN_SEGMENTS = 2
MAX_SEGMENTS = 4

# Segment separators. Chosen so in-segment arrival connectors ("and
# show me", "and confirm", ...) never split: only ";" and standalone
# then/after-that markers separate legs. URLs carry no spaces, so a
# separator can never occur inside one.
SPLIT_RE = re.compile(
    r"\s*;\s*|\s+and\s+then\s+|\s+then\s+|\s+after\s+that\s+",
    re.IGNORECASE)

# Road by exact shaped-key vocabulary — the dispatcher's own contract
# (loops.run_task routes on key presence; shaped dicts are pure
# single-road by construction, so exact match is sufficient and no
# precedence logic is duplicated here).
SHAPED_ROADS = {
    frozenset({"url", "expect_text"}): "observe",
    frozenset({"list_url", "goal_text", "body_expect"}): "follow",
    frozenset({"click_url", "click_text", "expect_text"}): "click",
    frozenset({"search_url", "query", "submit_phrase",
                "expect_text"}): "search",
}


class DecomposeRefused(ValueError):
    """Prose does not deterministically decompose to a runnable
    chain. Fail-closed before anything launches."""


def split_goal(prose: str) -> list[str]:
    """Split prose into non-empty leg segments. Raises
    DecomposeRefused for non-string/empty prose or empty segments."""
    if not isinstance(prose, str) or not prose.strip():
        raise DecomposeRefused("empty goal: nothing to decompose")
    text = prose.strip()
    text = re.sub(r"^[Ff]irst[\s,]+", "", text)
    parts = [seg.strip().rstrip(".") for seg in SPLIT_RE.split(text)]
    segments = [seg for seg in parts if seg]
    if len(segments) != len(parts):
        raise DecomposeRefused("empty leg segment: every leg between"
                               " separators must state its own road")
    if not segments:
        raise DecomposeRefused("empty goal: nothing to decompose")
    return segments


def road_of_shaped(shaped: dict) -> str:
    """Attach the road by exact shaped-key vocabulary. Raises
    DecomposeRefused for unrecognized key shapes (never guessed)."""
    if not isinstance(shaped, dict):
        raise DecomposeRefused("shaped leg must be a mapping")
    try:
        return SHAPED_ROADS[frozenset(shaped)]
    except KeyError:
        raise DecomposeRefused(
            f"unrecognized road keys {sorted(shaped)}: refusing") from None


def decompose_goal(prose: str) -> list:
    """Prose -> ordered road-goal leg list (each with a "road" key).

    Raises DecomposeRefused for anything that is not 2-4
    independently shapable segments. Search legs keep submit_phrase
    for edge grounding; every other leg is dispatcher-ready as is.
    """
    segments = split_goal(prose)
    if not (MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS):
        raise DecomposeRefused(
            f"decomposition needs {MIN_SEGMENTS}..{MAX_SEGMENTS}"
            f" legs, got {len(segments)} (single roads use --goal)")
    legs = []
    for n, segment in enumerate(segments, 1):
        try:
            shaped = shape_goal(segment)
        except UnknownGoalError as exc:
            raise DecomposeRefused(
                f"leg {n} does not shape ({exc})") from None
        road = road_of_shaped(shaped)
        legs.append({"road": road, **shaped})
    return legs
