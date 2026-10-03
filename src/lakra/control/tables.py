"""V2-04: deterministic table grounding + read-only extraction.

Pure functions over the existing table inventory shape produced by
collect_tables (one (total_rows, rows) entry per table; header-only
rows render their cells *starred*). No browser, no policy, no audit,
no persistence, no model calls — the same contract as the analyzer's
ground_* family, whose unique-winner-or-refuse pattern this mirrors.

Inventory entries arrive as (total_rows, rows) tuples (or lists);
each row is a tuple (or list) of cell strings. Anything else is
malformed and refused — never guessed around.

Grounding (ground_table): a caller-stated description scores every
table by DISTINCT shared content-words (exact word match, no
stemming, no fuzz, same vocabulary as analyzer._content_words) and
returns the unique winner's 0-based index. Refuses (UnknownGoalError,
never a guess): empty/secret-shaped description, empty/malformed
inventory, zero overlap, or tied winners. Tables are located by
inventory position (table.nth(i)), never by agent-invented CSS.

Extraction (extract_table): the grounded entry becomes a
deterministic, JSON-serializable dict {table_index, total_tables,
headers, rows, shape}. Headers are the first row's cells when that
row is header-marked (stars stripped); rows keep every row in order
with stars stripped and empty cells preserved as "". The page is
never mutated and no page.evaluate is involved (the inventory was
already read read-only by the observer).
"""

from __future__ import annotations

import re

from .planner import SECRET_WORDS, UnknownGoalError

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")

_STOPWORDS = frozenset({
    "the", "a", "an", "my", "me", "to", "for", "of", "on", "in", "and",
    "or", "please", "with", "from", "find", "get", "open", "go", "show",
    "which", "what", "are", "is", "was", "were", "be", "been", "this",
    "that", "these", "those", "it", "its", "at", "by", "as", "has",
    "have", "will", "would", "can", "could",
})


def _content_words(text: str) -> list[str]:
    """Goal/table vocabulary: lowercase words, len>=3, no stopwords
    (same scan as the analyzer's grounding vocabulary)."""
    return [w for w in _WORD_RE.findall(text.lower())
            if len(w) >= 3 and w not in _STOPWORDS]


def _check_description(description: str) -> str:
    if not description or not description.strip():
        raise UnknownGoalError("empty table description")
    lowered = description.lower()
    words = set(lowered.replace("=", " ").replace(":", " ").split())
    if words & SECRET_WORDS:
        raise UnknownGoalError("credential-laden descriptions refused")
    return lowered


def _cells_of(entry, pos: int) -> list[str]:
    """Flatten one inventory entry to its cell strings. Raises
    UnknownGoalError for any shape violation (never guessed around)."""
    try:
        total, rows = entry
    except (TypeError, ValueError) as exc:
        raise UnknownGoalError(
            f"malformed table {pos}: not a (total, rows) entry"
            f" ({exc})") from None
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise UnknownGoalError(f"malformed table {pos}: bad total_rows")
    try:
        row_list = list(rows)
    except TypeError as exc:
        raise UnknownGoalError(
            f"malformed table {pos}: rows not a sequence"
            f" ({exc})") from None
    cells: list[str] = []
    for row in row_list:
        try:
            items = list(row)
        except TypeError as exc:
            raise UnknownGoalError(
                f"malformed table {pos}: row not a sequence"
                f" ({exc})") from None
        for cell in items:
            if not isinstance(cell, str):
                raise UnknownGoalError(
                    f"malformed table {pos}: non-string cell")
            cells.append(cell)
    return cells


def _entry_rows(entry, pos: int) -> list[list[str]]:
    """Row-preserving view of one inventory entry (validated)."""
    try:
        _total, rows = entry
        row_list = list(rows)
    except (TypeError, ValueError) as exc:
        raise UnknownGoalError(
            f"malformed table {pos}: not a (total, rows) entry"
            f" ({exc})") from None
    out: list[list[str]] = []
    for row in row_list:
        try:
            items = list(row)
        except TypeError as exc:
            raise UnknownGoalError(
                f"malformed table {pos}: row not a sequence"
                f" ({exc})") from None
        for cell in items:
            if not isinstance(cell, str):
                raise UnknownGoalError(
                    f"malformed table {pos}: non-string cell")
        out.append(list(items))
    return out


def _as_list(tables) -> list:
    try:
        items = list(tables)
    except TypeError as exc:
        raise UnknownGoalError(f"malformed inventory: {exc}") from None
    if not items:
        raise UnknownGoalError("empty inventory: nothing to ground in")
    return items


def ground_table(description: str, tables) -> int:
    """Bind a caller-stated description to one observed table.

    Returns the unique winner's 0-based inventory index. Refuses
    (UnknownGoalError, never a guess): empty/secret-shaped
    description, empty/malformed inventory, zero word overlap, or
    tied top scores. Pure function.
    """
    lowered = _check_description(description)
    items = _as_list(tables)
    for pos in range(len(items)):
        _cells_of(items[pos], pos)  # validate every entry first
    goal_words = set(_content_words(lowered))
    if not goal_words:
        raise UnknownGoalError("description carries no groundable words")
    scored = []
    for pos in range(len(items)):
        vocab: set[str] = set()
        for cell in _cells_of(items[pos], pos):
            vocab.update(_content_words(cell))
        scored.append((len(goal_words & vocab), pos))
    best = max(s for s, _ in scored)
    if best < 1:
        raise UnknownGoalError("no table overlaps the description")
    winners = sorted(pos for s, pos in scored if s == best)
    if len(winners) > 1:
        raise UnknownGoalError(
            f"ambiguous tables {['table %d' % (w + 1) for w in winners]}:"
            " refuse, never guess")
    return winners[0]


def _strip_star(cell: str) -> tuple[str, bool]:
    """Remove one header star pair. Returns (text, was_header)."""
    if len(cell) >= 2 and cell.startswith("*") and cell.endswith("*"):
        return cell[1:-1], True
    return cell, False


def extract_table(tables, index: int) -> dict:
    """Render the grounded entry as structured data.

    {"table_index", "total_tables", "headers", "rows", "shape"} with
    headers from the first row when header-marked (stars stripped),
    rows in observed order (stars stripped, empty cells kept as ""),
    and shape [nrows, maxcols]. Deterministic and JSON-serializable.
    Raises UnknownGoalError for bad indices or malformed entries.
    """
    items = _as_list(tables)
    if isinstance(index, bool) or not isinstance(index, int) \
            or not (0 <= index < len(items)):
        raise UnknownGoalError(f"table index {index!r} out of range for"
                               f" {len(items)} table(s)")
    rows = _entry_rows(items[index], index)
    headers: list[str] = []
    if rows and rows[0] and all(_strip_star(c)[1] for c in rows[0]):
        headers = [_strip_star(c)[0] for c in rows[0]]
    body = [[_strip_star(c)[0] for c in row] for row in rows]
    width = max((len(r) for r in body), default=0)
    return {"table_index": index, "total_tables": len(items),
            "headers": headers, "rows": body,
            "shape": [len(body), width]}


def secret_shaped_text(value: str) -> bool:
    """Word-boundary secret scan (same vocabulary as the planner's
    hint gate, so "secretary" passes and "my password" does not)."""
    words = set(value.lower().replace("=", " ").replace(":", " ")
                .replace("/", " ").split())
    return bool(words & SECRET_WORDS)


def table_holds_secret(extracted: dict) -> bool:
    """True when any header or cell is secret-shaped: extracted
    content carrying credentials must never complete (refuse rather
    than record or transmit it)."""
    try:
        cells = list(extracted.get("headers", []))
        for row in extracted.get("rows", []):
            cells.extend(row)
    except (TypeError, AttributeError):
        return True  # unshaped extraction: fail closed
    return any(isinstance(c, str) and secret_shaped_text(c)
               for c in cells)


def verify_expected(extracted: dict, expect: str) -> None:
    """Confirm expected content inside the EXTRACTED table (not merely
    somewhere on the page). Substring match over headers + cells,
    mirroring text_contains semantics. Raises UnknownGoalError when
    the expectation is empty/secret-shaped or absent — a missing
    value must never read as COMPLETED."""
    if not isinstance(expect, str) or not expect.strip():
        raise UnknownGoalError("expect must be a non-empty string")
    if secret_shaped_text(expect):
        raise UnknownGoalError("secret-shaped expectation: rejected")
    try:
        cells = list(extracted.get("headers", []))
        for row in extracted.get("rows", []):
            cells.extend(row)
    except (TypeError, AttributeError) as exc:
        raise UnknownGoalError(
            f"malformed extraction ({exc})") from None
    want = expect.strip()
    if not any(isinstance(c, str) and want in c for c in cells):
        raise UnknownGoalError(
            f"expected {want!r} not in the extracted table")
