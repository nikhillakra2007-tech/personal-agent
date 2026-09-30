"""Slice-05: deterministic browser action executors (the "hands"), slice-22 submit.

Kinds: browser.click | browser.type | browser.press | browser.scroll |
browser.wait | browser.submit | browser.check | browser.select.
Structured locators only; page.evaluate is
banned. Each executor resolves its selector, acts with a bounded timeout,
and reports an ExecuteResult — it NEVER judges success beyond its own
execution. Whether the action achieved anything is verification.py's job,
on fresh state.

Submit (slice-22) activates a submit control by clicking it — the same
physical mechanics as click, but a distinct kind so policy keeps it L3
(consequential, approval-gated) and audit distinguishes it. It reaches the
executor ONLY through the approval-token path (router enforces); the hands
themselves grant no authority.

Target forms (documented contract, kept tiny):
  click:  "CSS selector"
  type:   "selector\\n---\\ntext"            (fill: deterministic clear+set)
  press:  "selector\\n---\\nKey" | "Key"     (Key in PRESS_KEYS; no selector =
                                              focused element)
  check:  "selector\\n---\\nchecked|unchecked" (idempotent: reads state,
                                              clicks ONLY when it differs;
                                              already-correct is a no-op
                                              success; unreadable state is
                                              refused, never clicked blind)
  select: "selector\\n---\\nvalue|label"     (native single-select only:
                                              exact value match first, else
                                              exact normalized-label match;
                                              zero/ambiguous matches, option
                                              indexes, multi-selects,
                                              disabled selects, and non-
                                              select elements are refused)
  scroll: "down:500" | "up:200" | "into-view:<selector>"
  wait:   "ms:1500" (capped) | "selector:<sel>" (wait until visible)

Refusals (ok=False, nothing touched): empty/oversize selectors, unknown
keys, typing into password fields (credential automation is banned),
scroll/wait forms that don't parse, zero-match selectors.
"""

from __future__ import annotations

from ..registry import ExecuteResult
from ...control.policy import Action
from ...control.tasks import Task

KINDS = frozenset({
    "browser.click", "browser.type", "browser.press",
    "browser.scroll", "browser.wait", "browser.submit",
    "browser.check", "browser.select",
})

MAX_SELECTOR_CHARS = 500
ACTION_TIMEOUT_MS = 5000
MAX_WAIT_MS = 10000

PRESS_KEYS = frozenset({
    "Enter", "Tab", "Escape", "Backspace", "Delete",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
})


def _check_selector(selector: str) -> str | None:
    if not selector or not selector.strip():
        return "empty selector"
    if len(selector) > MAX_SELECTOR_CHARS:
        return "selector too long"
    return None


def _rung_note(rung: str) -> str:
    """Audit suffix naming the resolving rung. CSS hits (the historical
    behavior) record nothing, so pre-slice-24 outputs are byte-identical."""
    return "" if rung == "css" else f" [fallback: {rung}]"


class BrowserActions:
    """Router executor for the eight acting kinds. Needs the live page."""

    def __init__(self, sessions) -> None:
        self.sessions = sessions
        self._page = None

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("no page open; navigate first")
        return self._page

    def open(self, url: str, timeout_ms: int = 15000):
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
        self._page = self.sessions.new_page(url, timeout_ms)
        return self._page

    def attach(self, page) -> None:
        """Point this executor at an already-open page (e.g. one navigated
        through the observer). The page must belong to our sessions."""
        self._page = page

    def close_page(self) -> None:
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

    # -- router entry ------------------------------------------------------
    def execute(self, action: Action, task: Task) -> ExecuteResult:
        _ = task
        if action.kind not in KINDS:
            return ExecuteResult(ok=False,
                                 error=f"unsupported kind {action.kind}")
        try:
            if action.kind == "browser.click":
                return self.click(action.target)
            if action.kind == "browser.type":
                return self.type(action.target)
            if action.kind == "browser.press":
                return self.press(action.target)
            if action.kind == "browser.scroll":
                return self.scroll(action.target)
            if action.kind == "browser.submit":
                return self.submit(action.target)
            if action.kind == "browser.check":
                return self.check(action.target)
            if action.kind == "browser.select":
                return self.select(action.target)
            return self.wait(action.target)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"action failed: {exc}")

    # -- the six hands ---------------------------------------------------
    def _locate(self, selector: str):
        """Fixed-order fallback (slice-24): CSS -> visible-text -> ARIA
        role; the first rung with a match wins. Returns (locator, rung,
        count). A total miss raises exactly as before (LookupError, same
        message); empty/oversize selectors raise ValueError as before.
        No fuzzy matching, no iframes/shadow-DOM piercing: each rung is
        an exact Playwright query over the main frame."""
        err = _check_selector(selector)
        if err:
            raise ValueError(err)
        css = self.page.locator(selector)
        if css.count() > 0:
            return css.first, "css", css.count()
        try:
            by_text = self.page.get_by_text(selector, exact=False)
            if by_text.count() > 0:
                return by_text.first, "text", by_text.count()
        except Exception:
            pass  # engine-level failure: fall through, never crash locate
        try:
            by_role = self.page.get_by_role(selector)
            if by_role.count() > 0:
                return by_role.first, "role", by_role.count()
        except Exception:
            pass  # e.g. not a valid ARIA role name: not this rung's job
        raise LookupError(f"no element matches {selector!r}")

    def click(self, selector: str) -> ExecuteResult:
        try:
            loc, rung, n = self._locate(selector)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        try:
            loc.click(timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"click failed: {exc}")
        note = f" ({n} matches, used first)" if n > 1 else ""
        return ExecuteResult(
            ok=True, output=f"clicked {selector}{note}{_rung_note(rung)}")

    def type(self, target: str) -> ExecuteResult:
        if "\n---\n" not in target:
            return ExecuteResult(ok=False,
                                 error="type target must be 'selector\\n---\\ntext'")
        selector, text = target.split("\n---\n", 1)
        try:
            loc, rung, _n = self._locate(selector)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        try:
            if (loc.get_attribute("type") or "").lower() == "password":
                return ExecuteResult(
                    ok=False,
                    error="refused: typing into password fields is banned")
            loc.fill(text, timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"type failed: {exc}")
        return ExecuteResult(
            ok=True,
            output=f"filled {selector} ({len(text)} chars){_rung_note(rung)}")

    def press(self, target: str) -> ExecuteResult:
        if "\n---\n" in target:
            selector, key = target.split("\n---\n", 1)
        else:
            selector, key = "", target
        key = key.strip()
        if key not in PRESS_KEYS:
            return ExecuteResult(ok=False,
                                 error=f"refused: key {key!r} not in allowlist")
        try:
            if selector:
                self._locate(selector)[0].press(key,
                                                timeout=ACTION_TIMEOUT_MS)
            else:
                self.page.keyboard.press(key)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"press failed: {exc}")
        return ExecuteResult(ok=True, output=f"pressed {key}")

    def scroll(self, target: str) -> ExecuteResult:
        try:
            if target.startswith("into-view:"):
                sel = target[len("into-view:"):]
                loc, rung, _n = self._locate(sel)
                loc.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
                return ExecuteResult(
                    ok=True, output=f"scrolled to {sel}{_rung_note(rung)}")
            direction, _, amount = target.partition(":")
            dy = int(amount)
            if direction == "down":
                pass
            elif direction == "up":
                dy = -dy
            else:
                return ExecuteResult(
                    ok=False,
                    error="scroll form: 'down:<px>' | 'up:<px>' | "
                          "'into-view:<selector>'")
            self.page.mouse.wheel(0, dy)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"scroll failed: {exc}")
        return ExecuteResult(ok=True, output=f"scrolled {target}")

    def submit(self, selector: str) -> ExecuteResult:
        """Activate a submit control (click mechanics, distinct kind).

        Same refusals as click (empty/oversize/zero-match selectors); the
        executor reports only that the control was activated, never what
        followed. Runs exclusively behind the L3 approval-token path —
        the router guarantees that, not this method."""
        try:
            loc, rung, n = self._locate(selector)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        try:
            loc.click(timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"submit failed: {exc}")
        note = f" ({n} matches, used first)" if n > 1 else ""
        return ExecuteResult(
            ok=True, output=f"submitted {selector}{note}{_rung_note(rung)}")

    def check(self, target: str) -> ExecuteResult:
        """Ensure a checkbox/radio reports a desired state (idempotent).

        Reads checked state first and clicks ONLY when it differs, so
        retries and recovery can never invert the control. Already-correct
        is a no-op success. Unreadable state (missing, uncheckable
        element) is refused — never clicked blind. Radios ride the same
        path (click selects; deselect is the group's business, not ours).
        Tri-state/indeterminate controls are NOT represented: only strict
        checked|unchecked targets are accepted."""
        if "\n---\n" not in target:
            return ExecuteResult(
                ok=False,
                error="check target must be 'selector\\n---\\n"
                      "checked|unchecked'")
        selector, want = target.split("\n---\n", 1)
        want = want.strip()
        if want not in ("checked", "unchecked"):
            return ExecuteResult(
                ok=False,
                error="check state must be 'checked' or 'unchecked' (strict)")
        try:
            loc, rung, _n = self._locate(selector)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        try:
            current = loc.is_checked()
        except Exception as exc:
            return ExecuteResult(
                ok=False, error=f"refused: not a checkable control ({exc})")
        current = bool(current)
        if (want == "checked") == current:
            state = "checked" if current else "unchecked"
            return ExecuteResult(
                ok=True,
                output=f"already {state} {selector}{_rung_note(rung)}")
        try:
            loc.click(timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"check failed: {exc}")
        return ExecuteResult(
            ok=True, output=f"{want} {selector}{_rung_note(rung)}")

    def select(self, target: str) -> ExecuteResult:
        """Choose one option of a native single-select (exact resolution).

        want matches an option VALUE exactly first (value preferred on
        ties), else an option LABEL exactly (whitespace-normalized).
        Zero or ambiguous matches are refused; option indexes, multi-
        selects, disabled selects, custom-widget dropdowns, and non-
        <select> elements are refused — never touched. Composes with the
        _locate fallback chain for finding the select itself."""
        if "\n---\n" not in target:
            return ExecuteResult(
                ok=False,
                error="select target must be 'selector\\n---\\nvalue|label'")
        selector, want = target.split("\n---\n", 1)
        want = want.strip()
        if not want:
            return ExecuteResult(
                ok=False, error="select option must be non-empty")
        try:
            loc, rung, _n = self._locate(selector)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        try:
            options = loc.locator("option")
            count = options.count()
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"select failed: {exc}")
        if count == 0:
            return ExecuteResult(
                ok=False,
                error=f"refused: {selector!r} is not a select element")
        try:
            if loc.get_attribute("multiple") is not None:
                return ExecuteResult(
                    ok=False,
                    error="refused: multi-selects are not supported")
            if loc.is_disabled():
                return ExecuteResult(
                    ok=False, error="refused: select is disabled")
            entries = []
            for i in range(count):
                opt = options.nth(i)
                entries.append((opt.get_attribute("value"),
                                opt.inner_text() or ""))
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"select failed: {exc}")
        by_value = [e for e in entries if e[0] == want]
        if len(by_value) > 1:
            return ExecuteResult(
                ok=False, error=f"refused: ambiguous option {want!r}")
        if len(by_value) == 1:
            return self._apply_select(loc, selector, rung, want,
                                      value=by_value[0][0])
        norm = " ".join(want.split())
        by_label = [e for e in entries
                    if " ".join(e[1].split()) == norm]
        if len(by_label) != 1:
            reason = "ambiguous" if by_label else "no"
            return ExecuteResult(
                ok=False,
                error=f"refused: {reason} option matches {want!r}")
        attr, text = by_label[0]
        if attr is not None:
            return self._apply_select(loc, selector, rung, want,
                                      value=attr)
        return self._apply_select(loc, selector, rung, want, label=text)

    def _apply_select(self, loc, selector: str, rung: str, want: str,
                      value: str | None = None,
                      label: str | None = None) -> ExecuteResult:
        try:
            if value is not None:
                loc.select_option(value=value, timeout=ACTION_TIMEOUT_MS)
            else:
                loc.select_option(label=label, timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"select failed: {exc}")
        return ExecuteResult(
            ok=True,
            output=f"selected {want} in {selector}{_rung_note(rung)}")

    def wait(self, target: str) -> ExecuteResult:
        try:
            if target.startswith("ms:"):
                ms = int(target[3:])
                if not 0 < ms <= MAX_WAIT_MS:
                    return ExecuteResult(
                        ok=False,
                        error=f"wait capped at {MAX_WAIT_MS} ms")
                self.page.wait_for_timeout(ms)
                return ExecuteResult(ok=True, output=f"waited {ms} ms")
            if target.startswith("selector:"):
                sel = target[len("selector:"):]
                err = _check_selector(sel)
                if err:
                    return ExecuteResult(ok=False, error=err)
                self.page.locator(sel).first.wait_for(
                    state="visible", timeout=ACTION_TIMEOUT_MS)
                return ExecuteResult(ok=True, output=f"{sel} visible")
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"wait failed: {exc}")
        return ExecuteResult(
            ok=False, error="wait form: 'ms:<n>' | 'selector:<sel>'")
