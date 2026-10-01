"""Slice-15: deterministic analyzer v0 (no model calls — approved scope),
slice-30 grounded hints, slice-32 loop-match grounding.

Converts raw goal phrasing into the validated hints dict the planner
already consumes. Pure function: goal text in, hints out (or refusal).
The planner re-validates and re-interprets independently — the analyzer
suggests data, never meaning. Anything unparseable raises UnknownGoalError
instead of padded guesses. Credential-laden goals are refused outright.

Slice-30 adds observation-grounded derivation: ground_link() scores a
snapshot link inventory against the goal and returns the winning link
TEXT (or refuses); grounded_follow() wraps it into validated follow
hints. The planner, templates, policy, and executors are untouched —
grounding only supplies the link_text a human used to hand-feed.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .planner import (
    HINT_VALUE_CAP,
    TEMPLATES,
    UnknownGoalError,
    validate_hints,
)

URL_RE = re.compile(r"(https?://\S+|file://\S+)")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")

STOPWORDS = frozenset({
    "the", "a", "an", "my", "me", "to", "for", "of", "on", "in", "and",
    "or", "please", "with", "from", "find", "get", "open", "go", "show",
    "which", "what", "are", "is", "was", "were", "be", "been", "this",
    "that", "these", "those", "it", "its", "at", "by", "as", "has",
    "have", "will", "would", "can", "could",
})

# Parallel to planner.TEMPLATES by POSITION (_intent zips them): any new
# template must add its name here in the same slot (slice-44 lesson:
# appending a template without this silently remaps every intent).
INTENT_NAMES = ("search", "follow", "fill-submit", "click", "observe")

SECRET_WORDS = frozenset({"password", "passwd", "secret", "api_key",
                          "apikey"})


def _intent(goal_lower: str) -> str | None:
    for name, (keywords, _) in zip(INTENT_NAMES, TEMPLATES):
        if any(k in goal_lower for k in keywords):
            return name
    return None


def analyze(goal_text: str) -> dict:
    """Derive planner hints from a raw goal. Raises UnknownGoalError."""
    if not goal_text or not goal_text.strip():
        raise UnknownGoalError("empty goal")
    lowered = goal_text.lower()
    words = set(lowered.replace("=", " ").replace(":", " ").split())
    if words & SECRET_WORDS:
        raise UnknownGoalError("credential-laden goals are refused")
    hints: dict = {}
    urls = URL_RE.findall(goal_text)
    for raw in urls:
        parts = urlparse(raw.rstrip(").,;\"'"))
        if parts.scheme in ("http", "https", "file") and (
                parts.netloc or parts.path):
            hints["url"] = raw.rstrip(").,;\"'")
            break
    # URL text is addressing, not content: exclude it from word scan so
    # path segments (personal-agents, ...) never become expect_text.
    prose = URL_RE.sub(" ", goal_text)
    operators = {k for group, _ in TEMPLATES for k in group}
    content = [w for w in WORD_RE.findall(prose.lower())
               if len(w) >= 3 and w not in STOPWORDS
               and not any(op in w for op in operators)]
    if content:
        # Earliest longest content word (deterministic, goal-ordered).
        best = content[0]
        for word in content[1:]:
            if len(word) > len(best):
                best = word
        hints["expect_text"] = best
    # URL text is addressing, not intent (same principle as the word
    # scan above): classify on the goal prose with URLs removed so a
    # path segment can never vote for a road.
    intent = _intent(URL_RE.sub(" ", lowered))
    if intent:
        hints["intent"] = intent
    if "url" not in hints and "expect_text" not in hints:
        raise UnknownGoalError(
            f"nothing extractable from goal {goal_text!r}")
    # 'intent' is informational only: strip before planner validation.
    planner_hints = {k: v for k, v in hints.items() if k != "intent"}
    validate_hints(planner_hints)
    return hints


def _content_words(text: str) -> list[str]:
    """Goal/link vocabulary: lowercase words, len>=3, no stopwords.
    Shared with analyze()'s word scan (operators are NOT excluded here:
    grounding matches raw vocabulary, and template routing stays the
    planner's job)."""
    return [w for w in WORD_RE.findall(text.lower())
            if len(w) >= 3 and w not in STOPWORDS]


def _check_goal(goal_text: str) -> str:
    if not goal_text or not goal_text.strip():
        raise UnknownGoalError("empty goal")
    lowered = goal_text.lower()
    words = set(lowered.replace("=", " ").replace(":", " ").split())
    if words & SECRET_WORDS:
        raise UnknownGoalError("credential-laden goals are refused")
    return lowered


def ground_link(goal_text: str, links) -> str:
    """Derive link_text from an observed inventory (slice-30).

    links: iterable of (text, href) pairs — the snapshot inventory
    shape. Scores each candidate by DISTINCT shared content-words with
    the goal (exact word match, no stemming, no fuzz). Returns the
    unique winner's text. Refuses (UnknownGoalError, never a guess):
    empty/malformed inventory, zero overlap, tied top score, or a
    stopword-only goal. Pure function; planner re-validates output.
    """
    lowered = _check_goal(goal_text)
    try:
        candidates = [(text, href) for text, href in links]
    except (TypeError, ValueError) as exc:
        raise UnknownGoalError(f"malformed inventory: {exc}")
    if not candidates:
        raise UnknownGoalError("empty inventory: nothing to ground in")
    for text, href in candidates:
        if not isinstance(text, str) or not text.strip():
            raise UnknownGoalError("malformed inventory: bad link text")
    goal_words = set(_content_words(lowered))
    if not goal_words:
        raise UnknownGoalError("goal carries no groundable words")
    scored = []
    for text, _href in candidates:
        overlap = len(goal_words & set(_content_words(text)))
        scored.append((overlap, text))
    best = max(s for s, _ in scored)
    if best < 1:
        raise UnknownGoalError("no link overlaps the goal")
    winners = sorted(t for s, t in scored if s == best)
    if len(winners) > 1:
        raise UnknownGoalError(
            f"ambiguous links {winners}: refuse, never guess")
    return winners[0]


def grounded_follow(goal_text: str, list_url: str, links,
                    body_expect: str) -> dict:
    """Goal + observed inventory -> validated follow hints (slice-30).

    {url, link_text (derived), expect_text (caller-stated arrival
    proof)}. The full dict passes validate_hints — the planner's own
    gate — before return. link_text comes from the world, never from
    the goal's spelling of it."""
    link_text = ground_link(goal_text, links)
    if (not isinstance(body_expect, str) or not body_expect.strip()
            or len(body_expect) > HINT_VALUE_CAP):
        raise UnknownGoalError("body_expect must be a non-empty string")
    words = set(body_expect.lower().replace("=", " ").replace(":", " ")
                .replace("/", " ").split())
    if words & SECRET_WORDS:
        raise UnknownGoalError("secret-shaped arrival proof: rejected")
    hints = {"url": list_url, "link_text": link_text,
             "expect_text": body_expect.strip()}
    validate_hints(hints)
    return hints


def _stem(word: str) -> str:
    """Minimal plural fold for loop matching (slice-32): trailing-s
    strip on longer words, ss-endings kept ("glass" stays "glass").
    Applied to BOTH sides equally, so it can only conflate what both
    sides spell alike — and unique-winner refusal contains the rest."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def ground_match(goal_text: str, link_texts) -> str:
    """Derive a loop match substring from goal + inventory (slice-32).

    Scores each goal content-word by the number of DISTINCT links
    containing its stem; the unique top scorer with count >= 2 wins —
    a loop needs plurality, so a word naming one link (or none) can
    never ground a loop. Refuses (UnknownGoalError, never a guess):
    empty/malformed inventory, no word spanning 2+ links (single-
    target goals belong to follow, not loops), tied winners,
    stopword-only or credential-laden goals. Pure function.
    """
    lowered = _check_goal(goal_text)
    try:
        texts = list(link_texts)
    except TypeError as exc:
        raise UnknownGoalError(f"malformed inventory: {exc}")
    if not texts:
        raise UnknownGoalError("empty inventory: nothing to ground in")
    for entry in texts:
        if not isinstance(entry, str) or not entry.strip():
            raise UnknownGoalError("malformed inventory: bad link text")
    goal_words = {_stem(w) for w in _content_words(lowered)}
    if not goal_words:
        raise UnknownGoalError("goal carries no groundable words")
    link_vocab = [{_stem(w) for w in _content_words(t)} for t in texts]
    scored = [(sum(1 for vocab in link_vocab if stem in vocab), stem)
              for stem in goal_words]
    best = max(s for s, _ in scored)
    if best < 2:
        raise UnknownGoalError(
            "no goal word spans 2+ links: not a loop goal")
    winners = sorted(stem for s, stem in scored if s == best)
    if len(winners) > 1:
        raise UnknownGoalError(
            f"ambiguous match {winners}: refuse, never guess")
    return winners[0]


def _secret_shaped(value: str) -> bool:
    words = set(value.lower().replace("=", " ").replace(":", " ")
                .replace("/", " ").split())
    return bool(words & SECRET_WORDS)


# Slot value shapes (slice-31): the caller states WHAT (typed), the page
# states WHERE. {"text": ...} fills, {"checked": bool} ensures state,
# {"select": ...} chooses an option. Anything else is refused here —
# the planner would refuse it later, but naming the slot early is
# honest failure.
def _entry_for(slot: str, value, selector: str) -> dict:
    if isinstance(value, dict):
        if set(value) == {"text"} and isinstance(value["text"], str) \
                and value["text"] and len(value["text"]) <= HINT_VALUE_CAP \
                and not _secret_shaped(value["text"]):
            return {"selector": selector, "text": value["text"]}
        if set(value) == {"checked"} \
                and type(value["checked"]) is bool:
            return {"selector": selector, "checked": value["checked"]}
        if set(value) == {"select"} and isinstance(value["select"], str) \
                and value["select"].strip() \
                and len(value["select"]) <= HINT_VALUE_CAP \
                and not _secret_shaped(value["select"]):
            return {"selector": selector, "select": value["select"]}
    raise UnknownGoalError(
        f"slot {slot!r} must be {{'text': ...}}, {{'checked': bool}},"
        f" or {{'select': ...}} (strict, secret-free)")


def ground_fields(goal_slots: dict, controls) -> list:
    """Bind caller-named slots to observed controls (slice-31).

    goal_slots: {slot_name: {"text"|"checked"|"select"} value}.
    controls: iterable of (label, kind, selector) — the snapshot
    inventory shape (kind: text|check|select).
    Matching is strict per slot: candidates are controls of the
    matching kind (text->text, checked->check, select->select); the
    winner is the unique control with top slot-word/label-word
    overlap (minimum 1). Refuses (UnknownGoalError, slot named):
    empty slots/inventory, malformed entries, kind mismatch (no
    control of the needed kind), zero overlap, tied winners.
    Values travel untouched from caller to entry — the page never
    supplies fill text. Pure function.
    """
    if not isinstance(goal_slots, dict) or not goal_slots:
        raise UnknownGoalError("goal_slots must be a non-empty mapping")
    try:
        items = [(label, kind, selector) for label, kind, selector
                 in controls]
    except (TypeError, ValueError) as exc:
        raise UnknownGoalError(f"malformed controls: {exc}")
    if not items:
        raise UnknownGoalError("empty controls: nothing to ground in")
    for label, kind, selector in items:
        if (not isinstance(label, str) or not label.strip()
                or kind not in ("text", "check", "select")
                or not isinstance(selector, str)
                or not selector.startswith("#")):
            raise UnknownGoalError("malformed controls: bad entry")
    need_kind = {"text": "text", "checked": "check", "select": "select"}
    fields = []
    for slot, value in goal_slots.items():
        if not isinstance(slot, str) or not slot.strip():
            raise UnknownGoalError("slot names must be non-empty strings")
        if not isinstance(value, dict) or len(value) != 1:
            raise UnknownGoalError(
                f"slot {slot!r} must carry one typed value")
        shape = next(iter(value))
        if shape not in need_kind:
            raise UnknownGoalError(
                f"slot {slot!r} must be {{'text'}}, {{'checked'}},"
                " or {{'select'}}")
        slot_words = set(_content_words(slot))
        if not slot_words:
            raise UnknownGoalError(
                f"slot {slot!r} carries no groundable words")
        same_kind = [(label, sel) for label, kind, sel in items
                     if kind == need_kind[shape]]
        if not same_kind:
            raise UnknownGoalError(
                f"slot {slot!r} needs a {need_kind[shape]} control:"
                " none observed")
        scored = [(len(slot_words & set(_content_words(label))), label,
                   sel) for label, sel in same_kind]
        best = max(s for s, _, _ in scored)
        if best < 1:
            raise UnknownGoalError(
                f"slot {slot!r} matches no control label")
        winners = sorted((label, sel) for s, label, sel in scored
                         if s == best)
        if len(winners) > 1:
            raise UnknownGoalError(
                f"slot {slot!r} is ambiguous between"
                f" {[l for l, _ in winners]}: refuse, never guess")
        _label, selector = winners[0]
        fields.append(_entry_for(slot, value, selector))
    return fields


def ground_submit(phrase: str, submits) -> str:
    """Ground a submit-selector phrase to an observed submit control.

    Slice-47: NL search shaping needs a submit selector, but the shaper
    is a pure function and cannot observe the page. This grounds the
    prose phrase ("search", "go", "submit") against the observed submit
    inventory (collect_submits) using the same unique-winner-or-refuse
    pattern as ground_fields. Returns the winning selector. Refuses
    (UnknownGoalError, never a guess): empty/malformed inventory, zero
    overlap, tied winners, or non-submit kind. Pure function.
    """
    lowered = _check_goal(phrase)
    try:
        items = [(label, kind, selector) for label, kind, selector
                 in submits]
    except (TypeError, ValueError) as exc:
        raise UnknownGoalError(f"malformed submits: {exc}")
    if not items:
        raise UnknownGoalError("empty submits: nothing to ground in")
    for label, kind, selector in items:
        if (not isinstance(label, str) or not label.strip()
                or kind != "submit"
                or not isinstance(selector, str)
                or not selector.startswith("#")):
            raise UnknownGoalError("malformed submits: bad entry")
    phrase_words = set(_content_words(lowered))
    if not phrase_words:
        raise UnknownGoalError("phrase carries no groundable words")
    scored = [(len(phrase_words & set(_content_words(label))), label,
               selector) for label, _, selector in items]
    best = max(s for s, _, _ in scored)
    if best < 1:
        raise UnknownGoalError("no submit control overlaps the phrase")
    winners = sorted((label, sel) for s, label, sel in scored
                     if s == best)
    if len(winners) > 1:
        raise UnknownGoalError(
            f"ambiguous submit {[l for l, _ in winners]}:"
            " refuse, never guess")
    return winners[0][1]


def ground_click(phrase: str, clicks) -> str:
    """Ground a click-target phrase to an observed clickable target.

    Composed click road: binds the caller-stated target phrase
    ("Continue", "Next page") against the observed click inventory
    (collect_clicks: link texts + non-submit button #ids) using the
    same unique-winner-or-refuse pattern as ground_submit. Returns
    the winning ref (link text for the executor's text rung, or a
    #id selector). Submit-kind controls must never reach this
    inventory (excluded at collection); any such entry is malformed
    here. Refuses (UnknownGoalError, never a guess):
    empty/malformed inventory, zero overlap, or tied winners.
    Pure function.
    """
    lowered = _check_goal(phrase)
    try:
        items = [(label, ref) for label, ref in clicks]
    except (TypeError, ValueError) as exc:
        raise UnknownGoalError(f"malformed clicks: {exc}")
    if not items:
        raise UnknownGoalError("empty clicks: nothing to ground in")
    for label, ref in items:
        if (not isinstance(label, str) or not label.strip()
                or not isinstance(ref, str) or not ref.strip()):
            raise UnknownGoalError("malformed clicks: bad entry")
    phrase_words = set(_content_words(lowered))
    if not phrase_words:
        raise UnknownGoalError("phrase carries no groundable words")
    scored = [(len(phrase_words & set(_content_words(label))), label,
               ref) for label, ref in items]
    best = max(s for s, _, _ in scored)
    if best < 1:
        raise UnknownGoalError("no clickable target overlaps the phrase")
    winners = sorted((label, ref) for s, label, ref in scored
                     if s == best)
    if len(winners) > 1:
        raise UnknownGoalError(
            f"ambiguous click target {[l for l, _ in winners]}:"
            " refuse, never guess")
    return winners[0][1]
