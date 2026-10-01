# Slice-Click-Road Teaching Brief — Composed Click Road

**Status:** APPROVED FOR DESIGN — implement only after sign-off.
**Date:** 2026-10-01
**Predecessor:** Slice-47 (NL search shaping). Unblocks the BLOCKED Slice-48 (NL click), which is explicitly out of scope here.
**Reason this slice exists:** the repository has a click planner template but no composed click road — no `run_click_task`, no `CLICK_KEYS`, no dispatcher branch, no `--road click`. A click-shaped goal dict currently misroutes into the observe branch (selector dropped) and dies with `missing hints: selector`. This slice builds the missing road; nothing more.

---

## 1. Problem and scope

Every planner template except click is reachable through the Slice-35 dispatcher behind explicit CLI flags. Click exists only as `planner.py::_click_steps` — reachable solely by direct `TaskLoop.run_goal` calls, which no product path makes. Users cannot press a button, follow a same-page control, or dismiss a dialog through any supported road.

This slice adds a **composed click road**: open page → ground click target → click template → verification, behind `--road click`. Deterministic and refusal-first, following the Slice-33 (follow) precedent line by line.

**In scope:** `run_click_task`, `CLICK_KEYS` + dispatcher branch, `--road click` CLI wiring, one deterministic click-target binder, tests, documentation.

**Out of scope (deferred, not smuggled):**
- NL click shaping (blocked Slice-48 becomes unblocked by this slice; it is still a separate slice).
- Form/loop shaping, chain recovery, login/session, downloads/uploads, visual fallback, daemon, foreground input.
- Any model/LLM call.
- Any policy, approval, or audit-taxonomy change.

---

## 2. Design principles (non-negotiable)

1. **Mirror Slice-33, not invent.** `run_follow_task` is the structural template: open → read inventory → ground → `run_goal`. Same refusal shape (`PLAN_OUTCOME`, `plan_id="-"`, `steps_done=0`), same audit taxonomy, same lifecycle mapping.
2. **No new authority.** `browser.click` with `effect="reversible"` already evaluates to ALLOW under L1 (policy Rule 5) when in bounds. The road adds reachability, not permission.
3. **Close the submit-gate bypass.** A submit control clicked via `browser.click` would dodge the L3 `browser.submit` gate (policy keys on action kind, not target). The click inventory therefore **excludes submit-kind controls** — same exclusion `collect_controls` already applies. Submit buttons belong exclusively to the submit-gated roads.
4. **Unique winner or refuse.** Zero matches, tied matches, and non-clickable matches refuse with the existing shaped-refusal shape. Never invent a selector; never guess between targets.
5. **Explicit CLI unchanged except additively.** All existing `--road` paths byte-identical. `--road click` is a new additive branch.

---

## 3. Architecture

```
--road click --url U --text "Continue" --expect "..."
  -> build_goal() -> {"click_url": U, "click_text": ..., "expect_text": ...}
    -> run_task() key-presence route -> run_click_task()
      -> open_page(click_url) -> read inventory -> ground_click()
      -> run_goal(task, {"url", "selector", "expect_text"}) -> _click_steps
        -> browser.click (L1 ALLOW) -> text_contains verification
```

### 3.1 Goal contract

`CLICK_KEYS = ("click_url", "click_text", "expect_text")` — all three required:
- `click_url`: page addressing (mirrors `list_url`/`form_url`/`search_url` naming; a dedicated key so the dispatcher never confuses it with observe's `url`).
- `click_text`: caller-stated prose describing the target (mirrors follow's `goal_text`); grounded, never used raw.
- `expect_text`: caller-stated post-click verification text (mirrors follow's `body_expect`; the template already supports it, and requiring it keeps every road honestly verified).

### 3.2 Dispatcher placement and mixed-key rules

New branch checked **before** the link and observe branches (Slice-44 precedent: search is routed first for the same reason — a shared native key):
- `has_click = "click_url" in goal or "click_text" in goal`.
- Mixed-keys refusal if click keys combine with form, loop, link, search, or observe keys (mirror the existing refusal strings).
- `sub = {k: goal[k] for k in CLICK_KEYS if k in goal}` passed to the road, same as every other branch.

Placement rationale (concrete): a click goal carries `expect_text`, which would otherwise satisfy `has_observe` and route to the observe road with the click target silently dropped — the exact misroute proven in the Slice-48 investigation. Routing click before observe closes it.

### 3.3 Road implementation (`run_click_task`)

Signature mirrors follow: `run_click_task(taskloop, open_page, read_clicks, task, goal, decider)`.
1. Validate mapping + required keys → else shaped refusal.
2. `open_page(goal["click_url"])` → refusal on failure (`cannot open page`).
3. `inventory = list(read_clicks() or [])` → refusal on failure (`inventory failed`).
4. `selector = ground_click(goal["click_text"], inventory)` → refusal on `UnknownGoalError` (`no groundable click target (...)`).
5. `return taskloop.run_goal(task, {"url": goal["click_url"], "selector": selector, "expect_text": goal["expect_text"]}, decider)`.

No new executors, predicates, templates, policy, hint keys, or event types. Post-grounding outcomes (DONE/STOPPED/PAUSED) pass through untouched.

### 3.4 Reuse table (from the actual repository)

| Piece | Reused as-is | New |
|---|---|---|
| Click template `_click_steps` | Yes — needs `selector` + optional `expect_text`; unchanged | — |
| `browser.click` executor + CSS→text→role fallback | Yes | — |
| `text_contains` verification + recovery fan-out | Yes | — |
| Dispatcher key-presence pattern + refusal shape | Yes | One branch + `CLICK_KEYS` |
| `run_follow_task` structure | Yes (structural template) | — |
| L1 policy path (`reversible` → ALLOW) | Yes — verified in `policy.py` Rules 1–5 | — |
| Audit (`PLAN_CREATED`/`PLAN_OUTCOME`), lifecycle, guards | Yes | — |
| Single-plan resume (`road: "single"`, re-verify from first open step) | Yes — click has no cursor and no gate, so generic resume covers it | — |
| Link inventory `collect_links` | Yes (link texts are clickable via the text rung) | — |
| Submit-button inventory `collect_submits` | Partially — reused for button labels, but submit-kind entries are **filtered out** (see §4) | Filter rule |
| CLI `build_goal`/`_forbid`/`_need`/`default_task_goal`/`finish` | Yes | One road branch |

---

## 4. Grounding design

### 4.1 What is clickable

The click inventory is the union, in `(label, selector)` shape, of:
- **Link texts** from `collect_links` — clicked via the executor's text rung (follow-road precedent).
- **Non-submit buttons** — labeled `<button>` elements (excluding `type="submit"`) resolved to `#id` selectors.

Explicitly **excluded**:
- `input[type=submit]` and `<button type="submit">` — these belong to the L3-gated submit path; admitting them here would let `browser.click` dodge the `browser.submit` approval gate (policy keys on kind, not target).
- Password/hidden/disabled/unlabeled elements — same exclusions `collect_controls`/`collect_submits` already apply.
- Arbitrary page text, divs with click handlers, canvas, custom widgets — not structurally inventoried; visual fallback is deferred, so these refuse.

### 4.2 Binder (`ground_click`-style, in `analyzer.py` beside `ground_submit`)

Scores each inventory entry by distinct shared content-words between the caller's `click_text` and the entry label (exact word match, no stemming — the `ground_link`/`ground_fields` pattern). Returns the winning `#id` selector or link text.
- Zero overlap → `UnknownGoalError` (no target).
- Tied top score → `UnknownGoalError` naming the tied labels (never guess).
- Malformed inventory entries → `UnknownGoalError`.
- Pure function; planner re-validates output via `validate_hints` as usual.

### 4.3 Grounding rule (unchanged project-wide)

Exactly one valid target → proceed. Zero targets → refuse/stop safely. Multiple equally valid targets → refuse/stop safely.

---

## 5. Policy

Determined from the existing implementation, not designed: `browser.click` is absent from both `L3_KINDS` and `L4_KINDS`; the click template marks the action `effect="reversible"`; in-bounds non-navigate browser actions inherit the open page's trust (`policy.py` bounds comment). Verdict path: Rule 1 miss → Rule 2 miss → Rule 3 in-bounds pass → Rule 5 **ALLOW (L1)**. **No policy change is required or permitted.** Out-of-scope navigation still ASKs via Rule 3 exactly as today.

## 6. Verification

Reuses the template's `text_contains(expect_text)` on fresh post-click state; `max_retries=2` per the existing template:
- `expect_text` supplied (always, per §3.1) → page must contain it after the click.
- Click navigates away → text is verified on the destination page (arrival-proof pattern, same as follow).
- Target disappears / text absent → predicate fails → existing recovery (retry → replan → ask → stop) runs; no new predicates.
- No visible state change matching the text → verification failure, never assumed success.

## 7. Safety

The road must NOT: call a model; bypass policy, verification, or the dispatcher; invent selectors; execute ambiguous clicks; click submit-kind or credential-field controls (excluded at inventory level, §4.1); introduce new permissions, gates, or authority; alter any existing road. The existing `--road click` guidance string in `shaping.py` (currently pointing at a nonexistent flag) becomes true upon this slice landing — a one-string consistency fix inside this slice, not NL shaping.

## 8. CLI

```
lakra_do.py --road click --url <page> --text "Continue" --expect "Welcome"
    [--yes | --no | --poll SECS]
```
- `ROADS += ("click")`; `build_goal` branch: forbid `max_items/max_iters/slots_json/submit/query`, require `text` + `expect`; goal `{"click_url": url, "click_text": text, "expect_text": expect}`.
- `default_task_goal`: `"Click {text}"` (keeps planner keyword routing aligned).
- `read_clicks` seam in `main()`: merged link + non-submit-button inventory from the live page.
- Incompatible combinations (`--road click` + loop/form/search flags, `--goal` + `--road`, `--chain-file` + `--road`) refuse via the existing `_forbid`/usage-error machinery before anything launches.

## 9. Resume / recovery

No changes required, stated explicitly: a click task is a single persisted plan with no cursor and no approval gate. Crash mid-click resumes through the existing generic single-plan path (`resume_info` → `road: "single"` → `resume_task` supervised drain from the first open step with fresh re-observation and re-verification). A partially-executed click re-verifies rather than re-fires blindly, per the existing recovery contract.

## 10. Audit

Existing events only: `PLAN_CREATED`, per-step `TOOL_SELECTED`/`ACTION_ALLOWED`/`EXECUTION_*`/`VERIFICATION`, `PLAN_OUTCOME`, `TASK_LIFECYCLE`. No new event types. Lifecycle mapping unchanged (DONE→COMPLETED, deny/shape-refusal→STOPPED/CANCELLED per existing rules; click has no gate so no WAITING_APPROVAL arises from this road).

## 11. Testing

### Unit tests
- `ground_click`-style binder: unique winner (link text and `#id` button), zero-match refusal, tie refusal (named), malformed-inventory refusal, wrong-kind/submit-kind exclusion, determinism, no-model-calls source check.
- Shaper untouched (still refuses click prose with guidance — now pointing at a real flag).

### Integration tests (stub rig, real router/policy path)
- Shaped-style click goal routes to the click branch (not observe): `PLAN_CREATED` present, selector honored.
- Click executes via the existing `browser.click` executor; verification runs.
- Tie/zero-match: STOPPED before any click, `steps_done == 0`, single `PLAN_OUTCOME`.
- Mixed-keys refusal (click + form/loop/link/search keys).

### CLI tests
- Valid `--road click` → exit 0, COMPLETED, verified.
- Missing `--text`/`--expect`/`--url` → usage error, exit 1.
- Ambiguous/missing target → refusal, exit 1, zero clicks executed.
- Flag-matrix guards (`--road click` + foreign flags; `--goal` + `--road`).

### Real-world validation (unseen locally authored pages, small set)
1. Unique button → clicked → verified DONE.
2. Two similarly named buttons → refuse, ZERO click executions (audit proves).
3. No matching button → refuse, ZERO clicks.
4. Distracting page text → correct target grounded.
5. Submit button as the only match → refused (gate-bypass guard).
6. Non-clickable matching text → refuse.
7. Click causing navigation → destination text verified.
8. Expected-text mismatch → honest STOPPED (no normalization).
9. Explicit `--road click` on pre-existing fixtures → unchanged behavior baseline.

## 12. Definition of done (acceptance criteria)

- AC#1 `--road click` exists and runs a valid click to DONE/COMPLETED live.
- AC#2 Target grounding is deterministic (same page + phrase → same selector).
- AC#3 Unique target executes; ambiguous target never executes (audit shows zero clicks).
- AC#4 Missing target never executes.
- AC#5 The planner click template is actually reached (audit shows `browser.click` + `text_contains` verification).
- AC#6 Policy enforced (L1 ALLOW in-bounds; out-of-scope navigation ASKs); no policy code changed.
- AC#7 Submit-kind controls cannot be clicked through this road (bypass-guard test).
- AC#8 Audit/lifecycle correct (existing event types only); resume works or is explicitly unchanged-and-covered.
- AC#9 Full suite green (baseline 549 + new tests); demo 30/30 PASS; all other roads byte-identical.
- AC#10 No NL click implementation included (shaper still refuses click prose).

## 13. Non-goals

NL click shaping (the unblocked Slice-48 follow-up, still separate), NL form, NL loop, chain recovery, login/session, downloads/uploads, visual fallback, physical mouse/keyboard, daemon/background execution, model-based planning, policy redesign, new audit event types, new persistence/migrations.

---

## Repository facts justifying this scope

- `planner.py:244` `_click_steps`: `_need(hints, "selector")`, `expect_text` defaults to `sel`, one `browser.click` (`reversible`) + `text_contains`, `max_retries=2`.
- `loops.py:671-758` dispatcher: branches for form/loop/link/observe/search only; a `{url, selector, expect_text}` goal demonstrably misroutes to observe and dies with `missing hints: selector` (reproduced live, Slice-48 investigation).
- `policy.py:124-149`: click path Rule 1 miss → Rule 2 miss → Rule 3 pass → Rule 5 ALLOW; `browser.click` in neither `L3_KINDS` nor `L4_KINDS`.
- `actions.py:18`: `browser.click` takes a CSS selector; locator fallback chain covers text rung.
- `observer.py`: `collect_links` (text→href), `collect_submits` (labeled buttons + submit inputs), `collect_controls` (text/check/select only — buttons omitted).
- `resume.py:122-141`: generic `road: "single"` plan resume covers cursor-less, gate-less roads with re-verification — no click-specific state needed.
- `lakra_do.py:115`: `ROADS = ("follow", "loop", "form", "observe", "search")`; `build_goal`/`_forbid`/`_need`/`default_task_goal`/`finish` all extend per-road additively (Slice-43/44 precedent).
