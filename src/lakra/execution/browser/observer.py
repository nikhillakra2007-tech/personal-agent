"""Slice-04: read-only browser observer.

One Snapshot per observation: url, title, timestamp, capped accessibility
tree, link inventory (slice-27, capped text->href map), table inventory
(slice-28, capped rows), control inventory (slice-31, capped labeled
controls), screenshot path. Credential fields are redacted before the tree is
stored; screenshots stay as local files (only paths enter history/audit).

Deliberately NO page.evaluate: page JavaScript is never executed by us and
page content is never eval'd on the Node side. The observer extracts
trees/text/screenshots only.

Interim router contact: until slice-05's controller exists, BrowserObserver
is the registered executor for browser.navigate / browser.snapshot /
browser.screenshot. Slice-05's controller will wrap observer+sessions and
become the sole router-facing seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page

from ..registry import ExecuteResult
from ...control.policy import Action
from ...control.tasks import Task

KINDS = frozenset({"browser.navigate", "browser.snapshot", "browser.screenshot"})

MAX_A11Y_NODES = 200
MAX_A11Y_CHARS = 8000

CREDENTIAL_MARKER = "[CREDENTIAL FIELD \u2014 value withheld]"


@dataclass
class Snapshot:
    url: str
    title: str
    ts: str
    a11y_tree: str
    nodes_capped: bool
    screenshot_path: str = ""
    redactions_applied: int = 0
    links: tuple = ()  # slice-27: ((text, href), ...) link inventory
    tables: tuple = ()  # slice-28: (((cell, ...), ...), ...) per table
    controls: tuple = ()  # slice-31: ((label, kind, selector), ...)


def _val(node: dict, key: str) -> str:
    return str((node.get(key) or {}).get("value", ""))


def _cdp_to_nested(nodes: list[dict]) -> dict | None:
    """Convert CDP's flat id/childIds tree into nested children dicts."""
    by_id = {n["nodeId"]: n for n in nodes}
    child_ids: set[str] = set()
    for n in nodes:
        child_ids.update(n.get("childIds") or [])

    def build(nid: str) -> dict:
        n = by_id[nid]
        return {
            "role": _val(n, "role"),
            "name": _val(n, "name"),
            "description": _val(n, "description"),
            "children": [build(c) for c in (n.get("childIds") or [])
                         if c in by_id],
        }

    roots = [n["nodeId"] for n in nodes if n["nodeId"] not in child_ids]
    if not roots:
        return None
    if len(roots) == 1:
        return build(roots[0])
    return {"role": "RootWebArea", "name": "", "description": "",
            "children": [build(r) for r in roots]}


def _render_a11y(node: dict | None, depth: int, cap: list[str],
                 budget: list[int], redactions: list[int]) -> None:
    """Flatten one accessibility node; caps invaded in document order."""
    if node is None or budget[0] <= 0 or len(cap) >= MAX_A11Y_NODES:
        return
    role = str(node.get("role", "?"))
    name = str(node.get("name", ""))
    if role.lower() in ("textbox",) and "password" in (
            str(node.get("description", "")) + name).lower():
        name = CREDENTIAL_MARKER
        redactions[0] += 1
    # Node values are never rendered: only role + (redacted) name.
    text = f"{'  ' * depth}{role}: {name}".strip()
    room = budget[0]
    if room <= 0:
        return
    cap.append(text[:room])
    budget[0] -= len(cap[-1])
    for child in node.get("children") or []:
        _render_a11y(child, depth + 1, cap, budget, redactions)


CDP_SESSION_RETRIES = 3
CDP_RETRY_WAIT_MS = 400

# Slice-27: link inventory caps. The section is observation only — it
# grants no authority and changes no verdict; it exists so a future
# planner (and today's humans/tests) can see where links lead.
MAX_LINKS = 50
MAX_LINK_PROBES = 200
MAX_LINK_TEXT_CHARS = 120

# Slice-28: table inventory caps. Same contract as links: observation
# only, bounded, never raises. Header cells render *starred*; nested
# tables are flattened one level (cell inner_text joins them) and never
# recursed — structure beyond one level is out of scope.
MAX_TABLES = 10
MAX_TABLE_ROWS = 20
MAX_TABLE_CELLS = 10
MAX_TABLE_CELL_CHARS = 80

# Slice-31: labeled-control inventory caps. Only #id selectors are ever
# emitted (a control without an id is ungroundable — stated, not worked
# around with positional selectors); password/hidden/submit controls
# are omitted outright, as are unlabeled ones (no safe name exists).
MAX_CONTROLS = 30
MAX_CONTROL_LABEL_CHARS = 80

CHECKABLE_TYPES = frozenset({"checkbox", "radio"})
TEXTUAL_TYPES = frozenset({"text", "search", "email", "tel", "url",
                           "number"})


def _fetch_ax_tree(page: Page) -> dict:
    """CDP Accessibility domain, bounded retries for transient agent-host
    unreadiness (observed: first session creation after an idle gap can
    report a dead guid; immediate reuse always works). Raises after bound."""
    last: Exception | None = None
    for _ in range(CDP_SESSION_RETRIES):
        session = page.context.new_cdp_session(page)
        try:
            return session.send("Accessibility.getFullAXTree",
                                {"max_depth": 50})
        except Exception as exc:
            last = exc
        finally:
            try:
                session.detach()
            except Exception:
                pass
        page.wait_for_timeout(CDP_RETRY_WAIT_MS)
    assert last is not None
    raise last


def collect_links(page: Page) -> tuple:
    """Inventory anchors as (text, href) pairs: visible text (whitespace-
    normalized, capped) mapped to its target. Same-text links keep the
    first; javascript:, fragment-only, and empty targets are omitted (no
    value to a follower); probing is bounded. Never raises: degradation
    is an empty inventory, and per-anchor failures skip that anchor."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        anchors = page.locator("a[href]")
        total = anchors.count()
    except Exception:
        return ()
    for i in range(min(total, MAX_LINK_PROBES)):
        if len(out) >= MAX_LINKS:
            break
        try:
            href = (anchors.nth(i).get_attribute("href") or "").strip()
            text = " ".join((anchors.nth(i).inner_text() or "").split())
        except Exception:
            continue  # races with page mutation; skip, don't fail
        if not text or not href:
            continue
        if href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append((text[:MAX_LINK_TEXT_CHARS], href))
    return tuple(out)


def collect_tables(page: Page) -> tuple:
    """Inventory tables as (((cell, ...), ...), ...): one tuple of rows
    per table, one tuple of cell strings per row. Header-only rows (any
    <th>, no <td>) render their cells *starred*. Only direct rows/cells
    are read (:scope keeps nested tables out of the structure; a nested
    table's text simply joins its cell's inner_text — flattened one
    level, never recursed). Bounded per the MAX_TABLE_* caps; never
    raises (degradation is fewer/no tables). No page.evaluate."""
    out: list[tuple[tuple[str, ...], ...]] = []
    try:
        tables = page.locator("table")
        total = tables.count()
    except Exception:
        return ()
    for ti in range(min(total, MAX_TABLES)):
        table = tables.nth(ti)
        try:
            rows = table.locator(
                ":scope > tr, :scope > thead > tr,"
                " :scope > tbody > tr, :scope > tfoot > tr")
            nrows = rows.count()
        except Exception:
            continue
        rendered: list[tuple[str, ...]] = []
        for ri in range(min(nrows, MAX_TABLE_ROWS)):
            row = rows.nth(ri)
            try:
                ths = row.locator(":scope > th").count()
                tds = row.locator(":scope > td").count()
                cells = row.locator(":scope > th, :scope > td")
                ncells = cells.count()
            except Exception:
                continue
            header = ths > 0 and tds == 0
            parts = []
            for ci in range(min(ncells, MAX_TABLE_CELLS)):
                try:
                    text = " ".join(
                        (cells.nth(ci).inner_text() or "").split())
                except Exception:
                    continue
                text = text[:MAX_TABLE_CELL_CHARS]
                parts.append(f"*{text}*" if header else text)
            rendered.append(tuple(parts))
        out.append((nrows, tuple(rendered)))
    return tuple(out)


def _control_label(page, ctrl, cid: str) -> str:
    """Resolve a control's human label: aria-label, then <label for>,
    then wrapping <label>, then placeholder. Empty string when none
    names it (the control is then omitted — never positionally named).
    A wrapping label's text MINUS the control's own rendered text (a
    select's options must not become its name). Never raises."""
    try:
        aria = (ctrl.get_attribute("aria-label") or "").strip()
        if aria:
            return aria
    except Exception:
        pass
    try:
        by_for = page.locator(f'label[for="{cid}"]')
        if by_for.count() > 0:
            text = (by_for.first.inner_text() or "").strip()
            if text:
                return text
    except Exception:
        pass
    try:
        wrap = page.locator(f"label:has(#{cid})")
        if wrap.count() > 0:
            text = " ".join((wrap.first.inner_text() or "").split())
            if text:
                try:
                    own = " ".join((ctrl.inner_text() or "").split())
                    if own and own in text:
                        text = " ".join(text.replace(own, "", 1).split())
                except Exception:
                    pass
                if text:
                    return text
    except Exception:
        pass
    try:
        ph = (ctrl.get_attribute("placeholder") or "").strip()
        if ph:
            return ph
    except Exception:
        pass
    return ""


def _norm_label(text: str) -> str:
    return " ".join(text.split())[:MAX_CONTROL_LABEL_CHARS]


def collect_controls(page: Page) -> tuple:
    """Inventory labeled native controls as (label, kind, selector).

    kind is text (fillable input), check (checkbox/radio), or select
    (native single-select) — the tag is known by which query found it,
    never by evaluate (banned). Selector is always #id. Omitted
    outright: password/hidden/submit/button inputs, multi-selects,
    disabled controls, controls without an id, and controls no label
    names. Bounded, never raises — degradation is fewer/no controls.
    """
    out: list[tuple[str, str, str]] = []

    def take(ctrl, cid: str, kind: str) -> None:
        label = _control_label(page, ctrl, cid)
        if not label:
            return
        out.append((_norm_label(label), kind, f"#{cid}"))

    def usable(ctrl) -> bool:
        try:
            return not ctrl.is_disabled()
        except Exception:
            return False

    try:
        inputs = page.locator("input")
        n_in = inputs.count()
    except Exception:
        return ()
    for i in range(n_in):
        if len(out) >= MAX_CONTROLS:
            break
        try:
            ctrl = inputs.nth(i)
            cid = (ctrl.get_attribute("id") or "").strip()
            if not cid or not usable(ctrl):
                continue
            itype = (ctrl.get_attribute("type") or "text").lower()
            if itype == "password":
                continue  # credential fields are never inventoried
            if itype in CHECKABLE_TYPES:
                take(ctrl, cid, "check")
            elif itype in TEXTUAL_TYPES:
                take(ctrl, cid, "text")
            # else: hidden/submit/button/image/... — omitted, not named
        except Exception:
            continue
    try:
        selects = page.locator("select")
        n_sel = selects.count()
    except Exception:
        return tuple(out)
    for i in range(n_sel):
        if len(out) >= MAX_CONTROLS:
            break
        try:
            ctrl = selects.nth(i)
            cid = (ctrl.get_attribute("id") or "").strip()
            if not cid or not usable(ctrl):
                continue
            if ctrl.get_attribute("multiple") is not None:
                continue
            take(ctrl, cid, "select")
        except Exception:
            continue
    return tuple(out)


def collect_submits(page: Page) -> tuple:
    """Inventory labeled submit controls as (label, kind, selector).

    kind is always "submit". Finds <button> and <input type=submit>
    elements with a label (text content, value attribute, or
    aria-label) and an id. Omitted: buttons without labels or ids,
    disabled buttons. Bounded, never raises — degradation is fewer/no
    submits. Slice-47: enables NL search submit-selector grounding.
    """
    out: list[tuple[str, str, str]] = []

    def _label(ctrl, cid: str) -> str:
        try:
            val = (ctrl.get_attribute("value") or "").strip()
            if val:
                return val
        except Exception:
            pass
        try:
            txt = (ctrl.inner_text() or "").strip()
            if txt:
                return txt
        except Exception:
            pass
        try:
            aria = (ctrl.get_attribute("aria-label") or "").strip()
            if aria:
                return aria
        except Exception:
            pass
        return ""

    def usable(ctrl) -> bool:
        try:
            return not ctrl.is_disabled()
        except Exception:
            return False

    try:
        buttons = page.locator("button")
        n_btn = buttons.count()
    except Exception:
        n_btn = 0
    for i in range(n_btn):
        if len(out) >= MAX_CONTROLS:
            break
        try:
            ctrl = buttons.nth(i)
            cid = (ctrl.get_attribute("id") or "").strip()
            if not cid or not usable(ctrl):
                continue
            label = _norm_label(_label(ctrl, cid))
            if label:
                out.append((label, "submit", f"#{cid}"))
        except Exception:
            continue
    try:
        inputs = page.locator("input[type=submit]")
        n_in = inputs.count()
    except Exception:
        n_in = 0
    for i in range(n_in):
        if len(out) >= MAX_CONTROLS:
            break
        try:
            ctrl = inputs.nth(i)
            cid = (ctrl.get_attribute("id") or "").strip()
            if not cid or not usable(ctrl):
                continue
            label = _norm_label(_label(ctrl, cid))
            if label:
                out.append((label, "submit", f"#{cid}"))
        except Exception:
            continue
    return tuple(out)


def capture_snapshot(page: Page, shot_dir: str | Path | None = None,
                     take_shot: bool = True) -> Snapshot:
    # CDP Accessibility domain: structured tree WITHOUT page.evaluate
    # (page.accessibility does not exist in Playwright Python).
    resp = _fetch_ax_tree(page)
    tree = _cdp_to_nested(resp.get("nodes", []))
    cap: list[str] = []
    budget = [MAX_A11Y_CHARS]
    redactions = [0]
    _render_a11y(tree, 0, cap, budget, redactions)
    nodes_capped = len(cap) >= MAX_A11Y_NODES or budget[0] <= 0
    shot_path = ""
    if take_shot and shot_dir is not None:
        shot_dir = Path(shot_dir)
        shot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        shot_path = str(shot_dir / f"shot-{stamp}.png")
        page.screenshot(path=shot_path)
    return Snapshot(
        url=page.url,
        title=page.title(),
        ts=datetime.now(timezone.utc).isoformat(),
        a11y_tree="\n".join(cap),
        nodes_capped=nodes_capped,
        screenshot_path=shot_path,
        redactions_applied=redactions[0],
        links=collect_links(page),
        tables=collect_tables(page),
        controls=collect_controls(page),
    )


class BrowserObserver:
    """Router executor for the three read-only browser kinds."""

    def __init__(self, sessions, shot_dir: str | Path) -> None:
        self.sessions = sessions
        self.shot_dir = Path(shot_dir)
        self._page: Page | None = None

    @property
    def current_page(self) -> Page | None:
        """Read-only view of the live page for verifiers (slice-05).
        Additive only; existing navigate/snapshot/screenshot behavior
        is unchanged."""
        return self._page

    def execute(self, action: Action, task: Task) -> ExecuteResult:
        _ = task
        if action.kind not in KINDS:
            return ExecuteResult(ok=False,
                                 error=f"unsupported kind {action.kind}")
        try:
            if action.kind == "browser.navigate":
                if self._page is not None:
                    try:
                        self._page.close()
                    except Exception:
                        pass
                self._page = self.sessions.new_page(action.target)
                snap = capture_snapshot(self._page, self.shot_dir)
            else:
                if self._page is None:
                    return ExecuteResult(
                        ok=False, error="no page open; navigate first")
                snap = capture_snapshot(
                    self._page, self.shot_dir,
                    take_shot=(action.kind == "browser.screenshot"))
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"observe failed: {exc}")
        lines = [f"url: {snap.url}", f"title: {snap.title}"]
        if snap.redactions_applied:
            lines.append(f"redactions: {snap.redactions_applied}")
        if snap.nodes_capped:
            lines.append("tree: CAPPED")
        if snap.screenshot_path:
            lines.append(f"screenshot: {snap.screenshot_path}")
        # Links ride AHEAD of the tree: ExecuteResult truncates output at
        # MAX_OUTPUT_CHARS, and the tree is the verbose part that may be
        # clipped — the compact inventory must survive truncation.
        lines.append(f"Links ({len(snap.links)}):")
        for text, href in snap.links:
            lines.append(f'  "{text}" -> {href}')
        lines.append(f"Tables ({len(snap.tables)}):")
        for ti, (total_rows, rows) in enumerate(snap.tables, 1):
            shown = len(rows)
            tag = f" ({shown} of {total_rows} rows)" if total_rows > shown \
                else f" ({shown} rows)"
            lines.append(f"  Table {ti}{tag}:")
            for row in rows:
                lines.append("    | " + " | ".join(row) + " |")
        lines.append(f"Controls ({len(snap.controls)}):")
        for label, kind, selector in snap.controls:
            lines.append(f'  [{kind}] "{label}" -> {selector}')
        lines.append("---")
        lines.append(snap.a11y_tree)
        return ExecuteResult(ok=True, output="\n".join(lines))
