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
  dispatcher.
- Search road (slice-44): navigate → grounded query box → type+verify
  → identical L3 submit gate (every search parks once) → results
  verification. Policy untouched.
- Click road (click-road slice): open page → ground click target
  (link texts + non-submit buttons, unique-winner-or-refuse; submit
  controls excluded so `browser.click` can never dodge the L3
  `browser.submit` gate) → existing click template → `text_contains`
  verification under the existing L1 policy. With this road every
  planner template is now goal-reachable through the dispatcher.
- Chain click legs (slice-49): "click" joins SINGLE_ROADS; each
  click leg dispatches through the unchanged `run_click_task` on
  the sibling non-completing runner with the live click inventory
  seam. All chain invariants hold (2–4 legs, stop-on-first-failure,
  no mid-chain completion, per-leg approvals, `--from-leg`).
- Chain status visibility (slice-50): `--status` (text + JSON)
  exposes per-leg road/outcome for chain tasks, derived read-only
  from the task goal, audit trail, and persisted plan hints
  (latest-wins per leg across `--from-leg` reruns; chain-level
  refusals show no legs). `--list` semantics unchanged.
- Chain resume by leg index (slice-51): `--resume TASK_ID
  --chain-file FILE --from-leg N` re-executes legs N..total on the
  SAME task through the unchanged `run_chain()` (fresh plans per
  leg; existing policy/approvals/lifecycle); stale live plans of
  that task are abandoned via `set_plan_status(..., SUPERSEDED)` +
  the existing `PLAN_SUPERSEDED` event; terminal/pending/uncertain/
  non-chain/ownership refusals are read-only before browser launch;
  bare `--resume` and bare `--chain-file --from-leg` unchanged.
- Chain goal-marker hardening (V1-D1 fix): the task goal is
  write-once at insert (`update_task()` never persists `goal`), so
  leg routing text can never overwrite the "Chain of N legs" marker
  — chain identity survives mid-chain process death (chain resume
  and `--status` keep working); bare `--resume` refuses chain tasks
  read-only with chain-resume guidance. No schema/policy/approval/
  lifecycle/audit changes.
- Link-ambiguity hardening (V1-D2 fix): the link inventory dedupes
  on (text, target), so an exact-duplicate visible label on
  different targets stays distinct and grounding refuses the tie
  instead of silently following the first; byte-identical repeats
  still collapse; unique/no-match/exclusion behavior unchanged.
- Credential-precedence hardening (V1-D3 fix): a credential-laden NL
  goal that also matches form/loop intent now yields the promised
  credential-shaped refusal instead of generic road guidance;
  refusal precedes any browser/DB/approval work. No policy/approval/
  audit/schema changes.
- Deterministic decomposition (V2-01): high-level prose splits into
  2-4 independently shapable legs (observe/follow/click/search;
  search submits grounded at the execution edge like --goal) and
  executes through the unchanged chain machinery on a new task via
  `--decompose`; pure-shaping refusals precede browser launch. No
  model calls; no policy/approval/lifecycle/audit/schema changes.
- Persisted work queue + run ledger (V2-02, schema v6): crash-safe
  QUEUED/CLAIMED/COMPLETED/FAILED/CANCELLED work items with atomic
  cross-process CAS claims, lease-based stale reclaim (live claims
  never stolen, terminal work never reruns), per-claim ledger rows,
  and a no-execution materialize() seam onto V2-01/chain machinery.
  No daemon; no policy/approval/lifecycle/audit changes.
- Chained multi-template runs (slice-45): 2–4 single-road legs under
  one task on a sibling non-completing runner; per-leg routing goals
  with restore; first non-DONE leg ends the chain; loop legs refused;
  no cross-leg data piping (each leg re-grounds fresh).
- Natural-language goal shaping (slice-46/47 + NL click):
  deterministic prose → road goal dict for observe, follow (with
  arrival phrase), search (with submit-control grounding), and
  click (with target phrase grounded by the click road); remaining
  intents refuse with explicit-flag guidance; no model calls;
  policy, guards, and the L3 gate untouched; explicit --road CLI
  byte-identical.
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

- NL shaping for form/loop roads (observe, follow, search, and
  click shape; the rest refuse with explicit-flag guidance — typed
  slot values and safety bounds cannot be honestly derived from
  prose).
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

- **NL shaping for the remaining roads** (form/loop): observe,
  follow, search, and click shape; the rest refuse with guidance.
  Form shaping still needs typed-slot extraction from prose
  (deemed unsafe). Alternative: scheduler-tick daemon for
  unattended runs. Owner's choice; explicitly not started.
