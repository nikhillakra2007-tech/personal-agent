"""Slice-46: deterministic natural-language goal shaping.

Maps prose to the exact road goal dicts the Slice-35 dispatcher already
consumes. Pure function: prose in, road goal dict out — or a refusal.
No model calls, no policy changes, no new roads. The shaper only
completes what prose can honestly supply; everything else refuses with
guidance.

Roads shaped: observe, follow (with arrival phrase), search (with
submit-control grounding), click (with target phrase grounded by the
click road). Roads refused: form, loop (typed values / safety bounds
the shaper cannot invent).
"""

from __future__ import annotations

from .analyzer import (
    INTENT_NAMES,
    SECRET_WORDS,
    STOPWORDS,
    TEMPLATES,
    URL_RE,
    WORD_RE,
    analyze,
)
from .planner import UnknownGoalError

ARRIVAL_CONNECTORS = (
    "and show me", "and show", "and see me", "and see",
    "expect to see", "and confirm", "and verify", "and check",
    "arriving at", "landing on", "and arrive at", "and land on",
)

SHAPABLE_INTENTS = ("observe", "follow", "search", "click")

UNSUPPORTED_INTENTS = ("fill-submit", "loop")

REFUSAL_GUIDANCE = {
    "fill-submit": "form needs typed slot values; use --road form --slots-json",
    "loop": "loop needs --max-items/--max-iters bounds; use --road loop",
}

MULTI_STEP_MARKERS = (
    "first ", " then ", "and then", "after that", "next,",
)


def _content_words(text: str) -> list[str]:
    """Content words using the analyzer's word-scan criteria (len>=3,
    no stopwords, no operator keywords), preserving original case so
    shaped text matches case-sensitive page verification."""
    operators = {k for group, _ in TEMPLATES for k in group}
    return [w for w in WORD_RE.findall(text)
            if len(w) >= 3 and w.lower() not in STOPWORDS
            and not any(op in w.lower() for op in operators)]


def _matching_intents(goal_lower: str) -> list[str]:
    """All roads whose keywords match the prose."""
    matches = []
    for name, (keywords, _) in zip(INTENT_NAMES, TEMPLATES):
        if any(k in goal_lower for k in keywords):
            matches.append(name)
    return matches


def _find_arrival_connector(prose_lower: str) -> tuple[int, int] | None:
    """Find the earliest arrival connector in the prose. Returns
    (start, end) indices or None."""
    best = None
    for connector in ARRIVAL_CONNECTORS:
        idx = prose_lower.find(connector)
        if idx != -1 and (best is None or idx < best[0]):
            best = (idx, idx + len(connector))
    return best


def _shape_observe(url: str, expect_text: str) -> dict:
    return {"url": url, "expect_text": expect_text}


def _shape_search(prose: str, url: str) -> dict:
    """Shape a search goal from prose.

    Prose structure: "Web search for <query> at <url> and confirm <expect>".
    Query = content words between "search for" and the URL. Submit
    phrase = "search" (the intent keyword; grounded later against
    observed submits). Expect_text = content words after
    "confirm"/"show" (if present).
    """
    prose_lower = prose.lower()
    url_idx = prose_lower.find(url.lower()) if url else -1
    query = ""
    for marker in ("search for ", "search "):
        idx = prose_lower.find(marker)
        if idx != -1:
            start = idx + len(marker)
            end = url_idx if url_idx > start else len(prose)
            segment = prose[start:end]
            words = _content_words(segment)
            if words:
                query = " ".join(words)
            break
    if not query:
        raise UnknownGoalError(
            "search goals need a query (what to search for);"
            " add it to the prose or use --road search --query")
    expect_text = ""
    for marker in ("confirm ", "show me ", "show "):
        idx = prose_lower.find(marker)
        if idx != -1:
            rest = prose[idx + len(marker):]
            rest_no_url = URL_RE.sub(" ", rest)
            words = _content_words(rest_no_url)
            if words:
                expect_text = " ".join(words)
            break
    return {"search_url": url, "query": {"text": query},
            "submit_phrase": "search", "expect_text": expect_text}


def _shape_click(prose: str, url: str) -> dict:
    """Shape a click goal from prose.

    Prose structure: "Open <url>, click the <target> button, and
    verify <expect>". Target = content words between the click verb
    and the expect marker (or URL). Expect_text = content words
    after "verify"/"confirm" (required: the click template must
    verify against caller-stated text). The target phrase is grounded
    later by the click road against the observed click inventory
    (submit-kind controls excluded there); nothing here invents a
    selector.
    """
    prose_lower = prose.lower()
    url_idx = prose_lower.find(url.lower()) if url else -1
    verb_at = -1
    verb_end = -1
    for marker in ("click ", "toggle ", "press "):
        idx = prose_lower.find(marker)
        if idx != -1 and (verb_at == -1 or idx < verb_at):
            verb_at = idx
            verb_end = idx + len(marker)
    expect_at = -1
    expect_end = -1
    for marker in ("and verify ", "and confirm ", "verify ", "confirm "):
        idx = prose_lower.find(marker)
        if idx != -1 and (expect_at == -1 or idx < expect_at):
            expect_at = idx
            expect_end = idx + len(marker)
    target = ""
    if verb_end != -1:
        end = len(prose)
        if expect_at != -1 and expect_at > verb_end:
            end = min(end, expect_at)
        if url_idx > verb_end:
            end = min(end, url_idx)
        words = _content_words(prose[verb_end:end])
        if words:
            target = " ".join(words)
    if not target:
        raise UnknownGoalError(
            "click goals need a target (which control to click);"
            " add it to the prose or use --road click --text")
    expect_text = ""
    if expect_end != -1:
        rest_no_url = URL_RE.sub(" ", prose[expect_end:])
        words = _content_words(rest_no_url)
        if words:
            expect_text = " ".join(words)
    if not expect_text:
        raise UnknownGoalError(
            "click goals need expected verification text (what the"
            " page shows after the click); add it to the prose or"
            " use --road click --expect")
    return {"click_url": url, "click_text": target,
            "expect_text": expect_text}


def _shape_follow(prose: str, url: str) -> dict:
    """Shape a follow goal from prose with an arrival phrase."""
    prose_no_url = URL_RE.sub(" ", prose)
    connector = _find_arrival_connector(prose_no_url.lower())
    if connector is None:
        raise UnknownGoalError(
            "follow goals need an arrival expectation (what the detail"
            " page shows); add it to the prose or use --road follow"
            " --expect")
    conn_start, conn_end = connector
    link_part = prose_no_url[:conn_start]
    arrival_part = prose_no_url[conn_end:]
    link_words = _content_words(link_part)
    arrival_words = _content_words(arrival_part)
    if not link_words:
        raise UnknownGoalError(
            "follow goals need a link phrase (which link to follow);"
            " rephrase or use --road follow --text")
    if not arrival_words:
        raise UnknownGoalError(
            "follow goals need an arrival expectation (what the detail"
            " page shows); add it to the prose or use --road follow"
            " --expect")
    return {"list_url": url, "goal_text": " ".join(link_words),
            "body_expect": " ".join(arrival_words)}


def shape_goal(prose: str) -> dict:
    """Prose -> road goal dict. Raises UnknownGoalError on any refusal.

    Pure function. Reuses analyzer.analyze for extraction. Observe,
    follow (with arrival phrase), search (with query), and click
    (with target phrase and verification text) shape; all other
    intents refuse with guidance. Ambiguous, multi-step,
    credential-laden, or empty prose refuses.
    """
    if not isinstance(prose, str) or not prose.strip():
        raise UnknownGoalError("empty goal")
    prose_lower = prose.lower()
    # URL text is addressing, not intent: ambiguity and guidance checks
    # run on the prose with URLs removed so a path segment such as
    # "nosubmit" can never vote for a road.
    prose_no_url_lower = URL_RE.sub(" ", prose_lower)
    for marker in MULTI_STEP_MARKERS:
        if marker in prose_lower:
            raise UnknownGoalError(
                "multi-step goals need --chain-file; shape one road at"
                " a time")
    try:
        hints = analyze(prose)
    except UnknownGoalError as exc:
        # Credential refusals keep precedence: never mask them with
        # road guidance. (Same word scan as analyzer.analyze.)
        words = set(prose_lower.replace("=", " ").replace(":", " ")
                    .split())
        if not (words & SECRET_WORDS):
            if "click" in _matching_intents(prose_no_url_lower):
                raise UnknownGoalError(
                    "click goals need a URL, a target phrase, and"
                    " verification text; use --road click"
                    " --url/--text/--expect") from exc
        for name in UNSUPPORTED_INTENTS:
            if name in _matching_intents(prose_no_url_lower):
                raise UnknownGoalError(REFUSAL_GUIDANCE[name]) from exc
        raise
    url = hints.get("url")
    expect_text = hints.get("expect_text")
    intent = hints.get("intent")
    if intent is None:
        raise UnknownGoalError(
            f"no road matches goal {prose!r}; use --road with one of"
            " follow|loop|form|observe|search|click")
    if intent == "follow":
        if not url:
            raise UnknownGoalError(
                "follow goals need a URL; add it to the prose or use"
                " --road follow --url")
        prose_no_url = URL_RE.sub(" ", prose)
        connector = _find_arrival_connector(prose_no_url.lower())
        if connector is None:
            raise UnknownGoalError(
                "follow goals need an arrival expectation (what the detail"
                " page shows); add it to the prose or use --road follow"
                " --expect")
        remaining = (prose_no_url[:connector[0]] + " "
                     + prose_no_url[connector[1]:])
        other_intents = [n for n in _matching_intents(remaining.lower())
                         if n != "follow"]
        if other_intents:
            raise UnknownGoalError(
                f"ambiguous: matches follow and {', '.join(other_intents)};"
                " rephrase or use --road")
        return _shape_follow(prose, url)
    if intent == "search":
        if not url:
            raise UnknownGoalError(
                "search goals need a URL; add it to the prose or use"
                " --road search --url")
        other_intents = [n for n in _matching_intents(prose_no_url_lower)
                         if n != "search"]
        if other_intents:
            raise UnknownGoalError(
                f"ambiguous: matches search and {', '.join(other_intents)};"
                " rephrase or use --road")
        return _shape_search(prose, url)
    if intent == "click":
        if not url:
            raise UnknownGoalError(
                "click goals need a URL; add it to the prose or use"
                " --road click --url")
        other_intents = [n for n in _matching_intents(prose_no_url_lower)
                         if n != "click"]
        if other_intents:
            raise UnknownGoalError(
                f"ambiguous: matches click and {', '.join(other_intents)};"
                " rephrase or use --road")
        return _shape_click(prose, url)
    if intent == "observe":
        other_intents = [n for n in _matching_intents(prose_no_url_lower)
                         if n != "observe"]
        if other_intents:
            raise UnknownGoalError(
                f"ambiguous: matches observe and {', '.join(other_intents)};"
                " rephrase or use --road")
        if not url:
            raise UnknownGoalError(
                "observe goals need a URL; add it to the prose or use"
                " --road observe --url")
        prose_no_url = URL_RE.sub(" ", prose)
        words = _content_words(prose_no_url)
        if not words:
            raise UnknownGoalError(
                "observe goals need something to observe;"
                " add it to the prose or use --road observe")
        best = words[0]
        for word in words[1:]:
            if len(word) > len(best):
                best = word
        return _shape_observe(url, best)
    if intent in REFUSAL_GUIDANCE:
        raise UnknownGoalError(REFUSAL_GUIDANCE[intent])
    raise UnknownGoalError(
        f"no road matches goal {prose!r}; use --road with one of"
        " follow|loop|form|observe|search|click")
