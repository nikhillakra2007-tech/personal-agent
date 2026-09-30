# Lakra Capability Matrix (audited 2026-09-29, post slice-43)

Ground truth for what Lakra can actually do, per the real-world task
capability audit. Every WORKING claim below was exercised live (browser
+ CLI) or is pinned by the 460-test suite; anything less is labeled
exactly that. Slices 01–43 built, 460/460 green, demo 30/30 PASS.

## WORKING (live-verified this pass or pinned by suite + prior live proof)

- Browser navigation (`browser.navigate`, URL-verified) and observation
  (snapshot + screenshot + title/URL capture) — `observer.py`,
  `sessions.py`; live on fixtures and fresh local pages.
- DOM/accessibility grounding: link inventory (capped text→href),
  table inventory, labeled `#id` control inventory — `observer.py`;
  snapshot redaction of credential fields verified live
  (`redactions_applied`, `[CREDENTIAL FIELD]` marker, zero leaks).
- Text matching (`text_contains` and friends) and per-action
  verification (PASS/FAIL predicates, never click-equals-success) —
  `verification.py`.
- Link following (grounded, unique-winner-or-refuse) + locator
  fallback chain (CSS → text → role, first rung wins, rung recorded).
- Buttons (click), inputs (deterministic clear+set type), checkboxes /
  radios (idempotent state-ensuring `browser.check`), native
  single-selects (exact value/label) — `actions.py`, all live.
- Multi-field forms (text + check + select entries, per-field
  verification) + submit that always parks at the L3 gate; denial
  holds the submit, task `CANCELLED`.
- Approval / denial / one-shot tokens (CAS decide, exact 4-tuple
  binding, single burn, adopt-after-external-decision) — proven
  two-process live (`approve.py` + `--poll` waiter).
- Resume (plan + loop, fresh-observation mandate, pending/uncertain/
  ownership refusals, re-park-for-fresh-consent) + kill/restart
  recovery (boot owners/sweep/uncertain-burn).
- Loops (cursor-unrolled `links_matching`, ≤5, establish-per-cycle,
  first-empty end, budget precheck, per-iteration L3) + follow /
  form / observe composed roads behind the key-presence
  dispatcher (slice-43 completes the family: every planner template
  is now goal-reachable).
- Search road (slice-44): navigate → grounded query box → type+verify
  → identical L3 submit gate (every search parks once) → results
  verification. Policy untouched.
- Chained multi-template runs (slice-45): 2–4 single-road legs under
  one task on a sibling non-completing runner; per-leg routing goals
  with restore; first non-DONE leg ends the chain; loop legs refused;
  no cross-leg data piping (each leg re-grounds fresh).
- Natural-language goal shaping (slice-46/47): deterministic prose →
  road goal dict for observe, follow (with arrival phrase), and
  search (with submit-control grounding); all other intents refuse
  with explicit-flag guidance; no model calls; policy, guards, and
  the L3 gate untouched; explicit --road CLI byte-identical.
- Re-planning (genuine, one bounded replan with supersede-freeze) +
  failure recovery fan-out (retry → replan → ask → stop).
- Task persistence (SQLite, WAL, versioned), task listing/status
  (text + closed-schema JSON), append-only audit + replay equality.
- Resource guards (steps/tokens/pressure → STOP/PAUSE + resume),
  tool router (registry choke point, unknown-tool rejection),
  policy L0–L4 truth table (24/24 incl. `captcha.defeat` → BLOCK).
- Filesystem / terminal executors (sandboxed root, argv allowlist).
- Ambiguity handling (ties/empty/credential-shaped → refusal, never
  a guess) + duplicate prevention (attempt ledger narrow suppression,
  token single-burn).
- Browser profile isolation (dedicated Playwright profile, user
  profile untouched) + cross-process CLI (`--poll` + `approve.py`).

## PARTIALLY WORKING (implemented, suite-green, not live-proven here)

- Scoped MCP client (`execution/mcp.py`): code + contract tests
  exist; no live server exercised in this pass.
- Model planning ladder (`ModelProvider`, local Ollama provider,
  measured proposals with deterministic fallback): interface +
  adapter proven per slice history; live-model behavior not
  re-verified in this pass (deterministic path is the default and is
  what everything above used).

## TEST-ONLY (by design)

- V1 acceptance harness, replay scripts, Slice-41 demo driver:
  verification infrastructure, not user capabilities.

## MISSING (no implementation)

- NL shaping for click/form/loop roads (slice-46/47 shapes observe,
  follow, and search; the rest refuse with explicit-flag guidance —
  page addressing, typed slot values, and safety bounds cannot be
  honestly derived from prose).
- Login/session workflows, downloads/uploads (`accept_downloads`
  is `False`; no transfer tooling), table-content grounding
  consumer, screenshot-grounded (visual-fallback) clicking.
- Daemon/scheduler-tick (one-shot runs only), JSON input/batch
  mode, multi-user/org concepts.

## UNSAFE / NEEDS GUARDRAIL

- None found in audit scope. Nearest risk reviewed: the observe road
  takes caller-stated `expect_text`, but the road is L0 read-only
  (navigate + snapshot), so there is no exploit surface — stating a
  wrong expectation yields verification failure, never action.

## DEFERRED BY DESIGN (approved, unchanged)

- C-A foreground watchdog + user preemption, C-B visible lock
  indicator (physical path never exercised).
- NL intake, daemon, table grounding, visual fallback, cloud
  fallback (per Slice-33–36 briefs).

## Known limitations (carried, not new)

- Fixture-scale runs finish in seconds: wall-clock mid-execution
  kills are impractical to stage live (deterministic pause/mid-body
  resume tests cover the semantics instead).
- Guard floors are environment-sensitive by design (documented
  `--ram-floor-mb` / `--cpu-ceiling` overrides).
- No `requirements.txt` (working set: `playwright psutil pytest`);
  no local git history in this tree.

## Next roadmap item (proposed, not started)

- **NL shaping for the remaining roads** (click/form/loop):
  slice-46/47 shaped observe, follow, and search; the rest refuse
  with guidance. A future slice could extend shaping to click (if
  selector grounding lands) or form (if typed-slot extraction from
  prose is deemed safe). Alternative: scheduler-tick daemon for
  unattended runs. Owner's choice; explicitly not started.
