"""Slice-05: deterministic browser action executors (the "hands"), slice-22 submit.

Kinds: browser.click | browser.type | browser.press | browser.scroll |
browser.wait | browser.submit | browser.check | browser.select |
browser.download | browser.upload (V2-05).
Structured locators only; page.evaluate is
banned. Each executor resolves its selector, acts with a bounded timeout,
and reports an ExecuteResult — it NEVER judges success beyond its own
execution. Whether the action achieved anything is verification.py's job,
on fresh state (transfer executors additionally confirm their own file
pre/post-conditions; the plan predicate re-confirms on fresh state).

Submit (slice-22) activates a submit control by clicking it — the same
physical mechanics as click, but a distinct kind so policy keeps it L3
(consequential, approval-gated) and audit distinguishes it. It reaches the
executor ONLY through the approval-token path (router enforces); the hands
themselves grant no authority.

Target forms (documented contract, kept tiny):
  click:  "CSS selector"
  download: "trigger-text\\n---\\ndest-relpath" (V2-05: trigger is the
             grounded link text, exactly one match; dest is
             sandbox-confined, never overwritten, size-bounded;
             L3-gated like submit)
  upload: "selector(#id)\\n---\\nsrc-relpath" (V2-05: grounded file-input
             #id only, exactly one enabled file input; src is an
             existing sandbox file, size-bounded; L3-gated like submit)
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

import os
from pathlib import Path

from ..registry import ExecuteResult
from ...control.policy import Action
from ...control.tasks import Task

KINDS = frozenset({
    "browser.click", "browser.type", "browser.press",
    "browser.scroll", "browser.wait", "browser.submit",
    "browser.check", "browser.select",
    "browser.download", "browser.upload",
})

MAX_SELECTOR_CHARS = 500
ACTION_TIMEOUT_MS = 5000
MAX_WAIT_MS = 10000

# V2-05 transfer bounds. Single-file caps keep transfers deterministic
# and memory-safe; the download wait is wall-time bounded like every
# other browser action.
MAX_TRANSFER_BYTES = 5_000_000
DOWNLOAD_TIMEOUT_MS = 30000

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
    """Router executor for the acting kinds (V2-05: ten). Needs the live page.

    transfer_root confines browser.download destinations and
    browser.upload sources to one sandbox directory (the
    filesystem-executor confinement algorithm: realpath-prefix, so
    absolute-outside, ../, and symlink escapes all refuse). None
    disables both transfer executors outright.
    """

    def __init__(self, sessions, transfer_root=None) -> None:
        self.sessions = sessions
        self._page = None
        if transfer_root is None:
            self._transfer_root = None
            self._transfer_real = None
        else:
            self._transfer_root = Path(transfer_root)
            self._transfer_root.mkdir(parents=True, exist_ok=True)
            self._transfer_real = os.path.realpath(self._transfer_root)

    def _confine(self, target: str) -> Path | None:
        """In-root path or None on escape (same contract as the
        filesystem executor: absolute targets accepted iff they
        resolve inside the root)."""
        if self._transfer_real is None:
            return None
        if os.path.isabs(target):
            full = os.path.realpath(target)
        else:
            full = os.path.realpath(
                os.path.join(self._transfer_real, target))
        if full != self._transfer_real and not full.startswith(
                self._transfer_real + os.sep):
            return None
        return Path(full)

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
            if action.kind == "browser.download":
                return self.download(action.target)
            if action.kind == "browser.upload":
                return self.upload(action.target)
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

    def download(self, target: str) -> ExecuteResult:
        """Save a download triggered by one grounded control, sandboxed.

        Target form: "selector\\n---\\ndest-relpath". The selector is
        the grounded trigger text (follow-road discipline); exactly one
        match is required — a changed page with twins refuses instead
        of clicking first. The click runs inside expect_download
        (bounded); the payload saves ONLY to the confined dest (no
        overwrite of an existing file — refuse rather than invent
        semantics), then must exist within 1..MAX_TRANSFER_BYTES or it
        is deleted and the step fails. Runs exclusively behind the L3
        approval-token path (router guarantees that, not this method).
        No page.evaluate.
        """
        if "\n---\n" not in target:
            return ExecuteResult(
                ok=False,
                error="download target must be 'selector\\n---\\ndest'")
        selector, dest_rel = target.split("\n---\n", 1)
        if self._transfer_root is None:
            return ExecuteResult(
                ok=False, error="refused: no transfer sandbox configured")
        if not getattr(self.sessions, "accept_downloads", False):
            return ExecuteResult(
                ok=False,
                error="refused: downloads not enabled for this session")
        dest = self._confine(dest_rel.strip())
        if dest is None:
            return ExecuteResult(
                ok=False,
                error=f"refused: {dest_rel.strip()!r} escapes"
                      " the transfer sandbox")
        if not dest.name:
            return ExecuteResult(
                ok=False, error="refused: destination must name a file")
        if dest.exists():
            return ExecuteResult(
                ok=False,
                error=f"refused: {dest_rel.strip()!r} already exists"
                      " (no silent overwrite)")
        try:
            loc, rung, n = self._locate(selector)
        except (ValueError, LookupError) as exc:
            return ExecuteResult(ok=False, error=str(exc))
        if n != 1:
            return ExecuteResult(
                ok=False,
                error=f"refused: {n} download triggers match"
                      f" {selector!r}: resolve to one first")
        try:
            with self.page.expect_download(
                    timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
                loc.click(timeout=ACTION_TIMEOUT_MS)
            download = dl_info.value
        except Exception as exc:
            return ExecuteResult(
                ok=False, error=f"download did not start: {exc}")
        if download.failure():
            return ExecuteResult(
                ok=False, error=f"download failed: {download.failure()}")
        suggested = download.suggested_filename or ""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(dest))
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"save failed: {exc}")
        try:
            size = dest.stat().st_size
        except OSError as exc:
            return ExecuteResult(ok=False, error=f"unreadable file: {exc}")
        if not 0 < size <= MAX_TRANSFER_BYTES:
            try:
                dest.unlink()
            except OSError:
                pass
            return ExecuteResult(
                ok=False,
                error=f"refused: downloaded size {size} out of bounds"
                      f" (1..{MAX_TRANSFER_BYTES})")
        return ExecuteResult(
            ok=True,
            output=f"downloaded {dest_rel.strip()} ({size} bytes;"
                   f" source suggested {suggested!r}){_rung_note(rung)}")

    def upload(self, target: str) -> ExecuteResult:
        """Set one grounded file input to a sandbox file (no click).

        Target form: "selector\\n---\\nsrc-relpath". The selector must
        be a grounded #id (form-road discipline — arbitrary CSS is
        refused); exactly one element must match and it must be an
        enabled file input (password/hidden/submit inputs never
        qualify). The source must already exist in the sandbox as a
        regular file within 0..MAX_TRANSFER_BYTES. Runs exclusively
        behind the L3 approval-token path. No page.evaluate.
        """
        if "\n---\n" not in target:
            return ExecuteResult(
                ok=False,
                error="upload target must be 'selector\\n---\\nsrc'")
        selector, src_rel = target.split("\n---\n", 1)
        if self._transfer_root is None:
            return ExecuteResult(
                ok=False, error="refused: no transfer sandbox configured")
        selector = selector.strip()
        if not selector.startswith("#"):
            return ExecuteResult(
                ok=False,
                error="refused: upload needs a grounded #id selector")
        err = _check_selector(selector)
        if err:
            return ExecuteResult(ok=False, error=err)
        src = self._confine(src_rel.strip())
        if src is None:
            return ExecuteResult(
                ok=False,
                error=f"refused: {src_rel.strip()!r} escapes"
                      " the transfer sandbox")
        try:
            is_file = src.is_file()
            size = src.stat().st_size if is_file else -1
        except OSError as exc:
            return ExecuteResult(ok=False, error=f"unreadable file: {exc}")
        if not is_file:
            return ExecuteResult(
                ok=False,
                error=f"refused: {src_rel.strip()!r} is not an"
                      " uploadable file")
        if size > MAX_TRANSFER_BYTES:
            return ExecuteResult(
                ok=False,
                error=f"refused: source size {size} exceeds"
                      f" {MAX_TRANSFER_BYTES}")
        try:
            loc = self.page.locator(selector)
            count = loc.count()
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"upload failed: {exc}")
        if count != 1:
            return ExecuteResult(
                ok=False,
                error=f"refused: {count} upload inputs match"
                      f" {selector!r}: resolve to one first")
        try:
            first = loc.first
            if (first.get_attribute("type") or "").lower() != "file":
                return ExecuteResult(
                    ok=False,
                    error=f"refused: {selector!r} is not a file input")
            if first.is_disabled():
                return ExecuteResult(
                    ok=False,
                    error=f"refused: {selector!r} is disabled")
            first.set_input_files(str(src), timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return ExecuteResult(ok=False, error=f"upload failed: {exc}")
        return ExecuteResult(
            ok=True,
            output=f"uploaded {src_rel.strip()} ({size} bytes)"
                   f" into {selector}")

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
