# Slice-46 Teaching Brief — Natural-Language Goal Shaping

**Status:** APPROVED FOR DESIGN — implement only after sign-off.
**Date:** 2026-09-30
**Predecessor:** Slice-45 (chained multi-template runs). Depends on the
Slice-35 dispatcher, Slice-44 search road, and the Slice-15/30/31 analyzer
grounding family. No new roads, no new chains, no new policy rules.

---

## 1. Problem and scope

The CLI requires `--road` plus shaped flags (`--url`, `--text`, `--expect`,
`--slots-json`, `--submit`, `--query`). Nothing infers a road or its
addressing from prose. The capability matrix lists this under MISSING and
names it the next roadmap item: *"Natural-language goal shaping (prose →
road + goal dict, still deterministic and refusal-first)."*

Slice-46 adds a **deterministic shaping layer** that maps natural-language
prose to the exact goal dicts the Slice-35 dispatcher already consumes.
The shaper is a pure function: prose in, road goal dict out — or a
refusal. It never executes anything, never calls a model, and never
touches policy.

**In scope:** single-road shaping (observe, follow, search, click) from
prose; a new `--goal` CLI flag; refusal-first ambiguity rules; tests;
documentation.

**Out of scope (deferred, not smuggled):**
- Form shaping from prose. Form slots require typed values
  (`{"text"|"checked"|"select"}`); prose cannot honestly supply them.
  Form stays explicit (`--road form --slots-json`). Refuse with guidance.
- Chain shaping from prose. Multi-leg composition from free text is a
  separate trust problem. Refuse with `--chain-file` guidance.
- Loop shaping from prose. Loops need `max_items`/`max_iters` bounds;
  prose cannot set safe bounds. Refuse with explicit-flag guidance.
- Any model/LLM call. The shaper is deterministic (analyzer precedent).
- Any change to existing explicit `--road` CLI behavior.

---

## 2. Design principles (non-negotiable)

1. **Deterministic, refusal-first.** Same prose → same dict or same
   refusal. No randomness, no clock, no model. Ambiguity refuses; it
   never guesses. This is the analyzer/grounding family contract
   (slices 15/30/31/32) applied to the CLI boundary.
2. **No unrestricted authority.** The shaper chooses among the five
   *declared* roads only. It cannot invent a road, a tool, a domain, or
   a policy exemption. The set of reachable goals is closed.
3. **No policy bypass.** A shaped goal dict is indistinguishable from a
   flag-built one. It flows through the same dispatcher → road grounding
   → planner `validate_hints` → per-action `policy.evaluate`. L3 gates
   still park for the human decider. Shaping happens *before* planning
   and grants nothing.
4. **No explicit-CLI change.** The `--road` + flags path is byte-identical.
   `--goal` is additive and mutually exclusive with `--road`. Existing
   tests must pass unchanged.
5. **No invented addressing.** Every value in a shaped dict is extracted
   from the prose by the existing analyzer (URL regex, content-word scan,
   intent keywords). If a required key has no prose source, the shaper
   refuses naming the gap. The shaper never fabricates selectors, slot
   values, or arrival proofs.
6. **No new machinery.** No new executors, predicates, templates, policy
   rules, hint keys, audit event types, or migrations. The shaper
   reuses `analyzer.analyze`, `planner.validate_hints`, and the
   dispatcher's key-presence routing unchanged.

---

## 3. Architecture

```
prose ──► shape_goal(prose) ──► road goal dict ──► run_task() ──► road
              │                       ▲
              │                       │ (identical to flag-built dicts)
              ▼                       │
         analyzer.analyze()     build_goal(opts)
              │
              ▼
         intent + url + expect_text
```

**New module:** `src/lakra/control/shaping.py`

```python
def shape_goal(prose: str) -> dict:
    """Prose -> road goal dict. Raises UnknownGoalError on any refusal.

    Pure function. Reuses analyzer.analyze for extraction. Maps the
    analyzer intent to the dispatcher's road key set. Refuses when the
    prose cannot supply every key the road requires.
    """
```

**CLI:** `scripts/lakra_do.py` gains `--goal "prose"`. A new
`run_goal()` path shapes the prose, then calls the *same* `run_task()`
the explicit path uses. No new stack construction, no new router, no
new decider plumbing.

### 3.1 Shaping rules per road

The shaper runs `analyze(prose)` to get `{url, expect_text, intent}`,
then maps intent → road and checks that every required key has a prose
source. The mapping uses the existing `INTENT_NAMES`/`TEMPLATES`
positional parallel (analyzer.py:43) — no new keyword tables.

| Road | Required keys | Prose source | Shaped when |
|------|---------------|--------------|-------------|
| observe | `url`, `expect_text` | both from `analyze` | always (if intent=observe) |
| follow | `list_url`, `goal_text`, `body_expect` | `list_url`=url; `goal_text`=prose link phrase; `body_expect`=prose arrival phrase | only if prose states an arrival expectation; else refuse with `--road follow --expect` guidance |
| search | `search_url`, `query`, `submit_selector`, `expect_text` | `search_url`=url; `query`=prose query phrase; `expect_text`=prose; `submit_selector`=**never from prose** | refuse: submit_selector is page addressing the shaper cannot invent; suggest explicit `--road search --submit` |
| click | `selector` | **never from prose** | refuse: selector is page addressing; suggest explicit `--road click` (or a form/search road) |
| form | `form_url`, `goal_slots`, `submit_selector` | slots never from prose | refuse: typed slot values required; suggest explicit `--road form --slots-json` |
| loop | `list_url`, `goal_text`, `body_expect`, `max_items`, `max_iters` | bounds never from prose | refuse: bounds are safety limits; suggest explicit `--road loop --max-items --max-iters` |

**Result:** Slice-46 fully shapes **observe** goals and **follow** goals
that state an arrival expectation. Search, click, form, and loop refuse
with specific, actionable guidance. This is honest: the shaper only
completes what prose can honestly supply.

### 3.2 Follow shaping detail

Follow is the highest-value road after observe. The prose must supply:
- a URL (analyzer regex),
- a link phrase (the prose's own words describing which link),
- an arrival expectation (what the detail page should show).

Example that shapes:
> "Follow the Beta record on file:///l.html and show me the detail record"

- `analyze` extracts `url=file:///l.html`, `intent=follow`,
  `expect_text` = earliest longest content word.
- The shaper builds `{"list_url": url, "goal_text": <link phrase>,
  "body_expect": <arrival phrase>}`.
- `goal_text` and `body_expect` are derived from the prose's content
  words (deterministic extraction, same word-scan as `analyze`). If the
  prose has no distinguishable arrival phrase, refuse.

Example that refuses:
> "Follow the Beta record on file:///l.html"

- No arrival expectation in prose → `UnknownGoalError` with guidance:
  *"follow goals need an arrival expectation (what the detail page
  shows); add it to the prose or use --road follow --expect."*

### 3.3 Refusal rules (closed set)

The shaper raises `UnknownGoalError` (the existing refusal type) for:

1. **Empty / whitespace-only prose.**
2. **Credential-laden prose** (analyzer `SECRET_WORDS` — inherited).
3. **No extractable content** (no URL and no content words).
4. **No intent match** (prose matches no `INTENT_NAMES` keyword).
5. **Ambiguous intent** (prose matches multiple roads with no unique
   winner — same tie-refusal rule as `ground_link`/`ground_match`).
6. **Missing required key** (road needs a key the prose cannot supply —
   see §3.1 table). The refusal names the road, the missing key, and the
   explicit-flag alternative.
7. **Multi-step prose** (prose implies a chain: "first… then…", "and
   then", enumerated steps). Refuse with `--chain-file` guidance.
8. **Prose mixed with explicit flags** (CLI-level, see §4).

Every refusal is a shaped `PLAN_OUTCOME` audit entry (the existing
composed-entry refusal shape, `plan_id="-"`, `steps_done=0`) — no new
audit event types.

### 3.4 Confirmation boundaries

- The shaper **never launches a browser, never opens a page, never
  executes an action.** It returns a dict or raises.
- The shaped dict is **confirmed by the user only at the L3 gate**, same
  as every other goal. Shaping does not pre-approve anything.
- The CLI prints the shaped dict before running (transparency): the user
  sees exactly what the prose became before anything launches.
- A shaped goal that reaches an L3 gate parks for the threaded decider
  (`--yes`/`--no`/`--poll`/interactive) — identical to explicit runs.

---

## 4. CLI design

### 4.1 New flag

```
--goal "prose"     Shape a natural-language goal from prose.
                   Mutually exclusive with --road and --chain-file.
                   Takes no road-specific addressing flags.
```

### 4.2 Flag matrix (usage errors fail closed before anything launches)

| Combination | Verdict |
|-------------|---------|
| `--goal "…" --road …` | usage error: `--goal` takes no `--road` |
| `--goal "…" --url/--text/--expect/--slots-json/--submit/--query/--max-items/--max-iters` | usage error: `--goal` takes no addressing flags (all from prose) |
| `--goal "…" --chain-file …` | usage error: chains are explicit; see guidance |
| `--goal "…" --resume …` | usage error: `--goal` takes no `--resume` |
| `--goal "…" --yes/--no/--poll SECS` | allowed (decider) |
| `--goal "…" --allow-domain …` | allowed (scope) |
| `--goal "…" --audit/--profile-dir/--shots-dir/--ram-floor-mb/--cpu-ceiling` | allowed (infra) |
| `--road …` (existing) | **unchanged** |
| `--chain-file …` (existing) | **unchanged** |

### 4.3 Execution path

```
main()
  ├─ --goal present → run_goal(argv, opts, ...)
  │     ├─ shape_goal(prose)  → dict | UsageError(refusal)
  │     ├─ print shaped dict (transparency)
  │     └─ run_task(taskloop, db, hands.open, read_pairs,
 │                   read_controls, task, shaped_dict, decider)
  │        (identical call to the explicit path)
  └─ --road present → existing build_goal() path (unchanged)
```

The task's `goal` field is set to the prose itself (the "why"), so the
DB row and audit trail record what the user actually asked. The shaped
dict is the routing input, not the persisted goal.

### 4.4 Exit codes (unchanged)

0 = road DONE, 2 = human-denied at L3 gate, 1 = usage error / refusal /
any other STOPPED. A shaping refusal exits 1 with the refusal text on
stdout.

---

## 5. Security constraints

1. **No model calls.** The shaper imports only `analyzer` and
   `planner.validate_hints`. Source inspection test asserts no
   `models.`/`complete(`/`ollama` (analyzer test precedent,
   test_analyzer.py:75).
2. **Closed road set.** The shaper can only produce dicts whose keys are
   subsets of the five declared roads' key sets. The dispatcher's
   key-presence routing is the second gate: a smuggled key combination
   is refused by the dispatcher's mixed-keys rule.
3. **validate_hints re-validation.** Every shaped dict passes
   `planner.validate_hints` (closed key set, 2000-char cap, secret
   rejection) before planning. The shaper does not bypass this; it
   constructs dicts that already satisfy it, and the planner re-checks.
4. **Policy untouched.** `policy.evaluate` is not modified. L3 kinds
   (`browser.submit`, …) still ASK. L4 kinds still BLOCK. Bounds
   (allowed_tools/allowed_domains) still enforced per task.
5. **No privilege elevation.** Shaping cannot raise `permission_level`,
   widen `allowed_domains`, or set `submission_policy`. Those come from
   the task/CLI scope, unchanged.
6. **Audit integrity.** Shaping refusals log `PLAN_OUTCOME` (existing
   type). Successful shaping logs nothing extra — the road's own
   PLAN_CREATED/PLAN_OUTCOME events carry the trail. No new event types.
7. **Prose cannot smuggle keys.** The shaper constructs the dict from
   extracted values; prose is never evaluated as a dict or merged with
   caller-supplied road keys. The CLI rejects addressing flags alongside
   `--goal` (§4.2).

---

## 6. Ambiguity and refusal rules (summary table)

| Condition | Refusal | Guidance in message |
|-----------|---------|---------------------|
| Empty/whitespace | yes | "empty goal" |
| Credential-shaped | yes | "credential-laden goals are refused" |
| No URL and no content words | yes | "nothing extractable" |
| No intent keyword match | yes | "no road matches; use --road" |
| Multi-intent tie | yes | "ambiguous: matches form and follow; rephrase or use --road" |
| Multi-step prose (chain-like) | yes | "multi-step goals need --chain-file" |
| follow without arrival phrase | yes | "add an arrival expectation or use --road follow --expect" |
| search (no submit_selector) | yes | "search needs --submit; use --road search" |
| click (no selector) | yes | "click needs a selector; use --road click or form/search" |
| form (no slots) | yes | "form needs --slots-json; use --road form" |
| loop (no bounds) | yes | "loop needs --max-items/--max-iters; use --road loop" |
| observe (url + text present) | **no** | — |

---

## 7. Tests

New file `tests/test_shaping.py`, following `test_analyzer.py` patterns
(pure-function tests + stub-rig integration + CLI smoke).

### 7.1 Unit — shaping
- `test_observe_shapes_from_prose`: URL + content word → observe dict.
- `test_follow_shapes_with_arrival_phrase`: full follow dict.
- `test_follow_refuses_without_arrival`: refusal names `--expect`.
- `test_search_refuses_no_submit_selector`: refusal names `--submit`.
- `test_click_refuses_no_selector`: refusal names `--road click`.
- `test_form_refuses_no_slots`: refusal names `--slots-json`.
- `test_loop_refuses_no_bounds`: refusal names `--max-items`.
- `test_multi_step_prose_refuses_with_chain_guidance`.
- `test_ambiguous_intent_refuses`: multi-intent prose → refusal.
- `test_empty_and_credential_refused` (inherited analyzer rules).
- `test_deterministic`: same prose → same dict.
- `test_no_model_calls_in_shaper`: source inspection.

### 7.2 Integration — stub rig (test_chain.py precedent)
- `test_shaped_observe_routes_through_dispatcher`: shaped dict →
  `run_task` → observe road runs, task COMPLETED.
- `test_shaped_follow_grounds_link`: shaped follow dict → follow road
  grounds link_text from live inventory, DONE.
- `test_shaped_goal_passes_validate_hints`: every shaped dict validates.
- `test_shaping_refusal_logs_plan_outcome`: refusal → one PLAN_OUTCOME
  audit entry, plan_id "-", nothing planned.

### 7.3 Integration — L3 gate
- `test_shaped_search_parks_at_gate`: shaped search goal (if a future
  slice allows search shaping) or a shaped form-like goal → submit step
  parks, decider consulted once, deny → CANCELLED. (If search stays
  refused in slice-46, this test uses a stub submit goal to prove the
  shaped path does not bypass the gate.)

### 7.4 CLI smoke (test_cli_do.py precedent)
- `test_cli_goal_observe_exit_0`: `--goal "Show … at file:///…"` →
  exit 0, "status: DONE".
- `test_cli_goal_refusal_exit_1`: ambiguous prose → exit 1, refusal
  text on stdout.
- `test_cli_goal_rejects_road_mix`: `--goal … --road follow` →
  usage error, exit 1.
- `test_cli_goal_rejects_addressing_flags`: `--goal … --url …` →
  usage error, exit 1.
- `test_explicit_road_path_unchanged`: existing `--road` tests pass
  unmodified (regression).

### 7.5 Regression
- Full suite green (baseline 483 + new shaping tests).
- V1 demo 30/30 still PASS.

---

## 8. Acceptance criteria

- **AC#1** — Clear-intent observe prose with URL + content word shapes to
  the observe goal dict and runs to DONE live.
- **AC#2** — Clear-intent follow prose with URL + link phrase + arrival
  phrase shapes to the follow goal dict and runs to DONE live.
- **AC#3** — Ambiguous, incomplete, credential-laden, or multi-step prose
  refuses with `UnknownGoalError`; the refusal names the gap and the
  explicit-flag alternative. Never guesses.
- **AC#4** — Every shaped dict passes `planner.validate_hints` unchanged.
- **AC#5** — A shaped goal that reaches an L3 gate parks for the decider;
  deny → CANCELLED. Policy is not bypassed.
- **AC#6** — The explicit `--road` + flags CLI path is byte-identical;
  all pre-existing CLI tests pass unmodified.
- **AC#7** — The shaper makes no model calls (source-inspected).
- **AC#8** — No new roads, chains, tools, policy rules, hint keys, audit
  event types, or migrations.
- **AC#9** — Full regression suite green; V1 demo 30/30 PASS.
- **AC#10** — Capability matrix updated: NL shaping moves from MISSING to
  WORKING for observe/follow; search/click/form/loop shaping documented
  as deferred with reasons.

---

## 9. Files touched (implementation plan)

| File | Change |
|------|--------|
| `src/lakra/control/shaping.py` | **new** — `shape_goal()` |
| `scripts/lakra_do.py` | add `--goal` flag, `run_goal()`, flag-matrix guards |
| `tests/test_shaping.py` | **new** — unit + integration + CLI smoke |
| `docs/capability-matrix.md` | move NL shaping to WORKING; note deferred roads |
| `specs/001-lakra-v1/tasks.md` | record slice-46 as BUILT with test count |
| `docs/demo-v1.md` | no change (demo uses explicit flags) |

**Untouched:** planner, analyzer, loops (dispatcher), chain, policy,
approvals, audit, guards, store, runner, scheduler, tasks, executors,
predicates, templates, lakra_run.py, approve.py.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Prose is mis-shaped to the wrong road | Intent keywords are the existing planner set; ambiguity refuses. Dispatcher key-presence is the second gate. |
| Shaped goal bypasses L3 gate | Shaped dicts are identical to flag-built ones; policy.evaluate is unchanged; gate parks per action. |
| User trusts prose too much | CLI prints the shaped dict before running; L3 gate still parks; audit records the prose as the task goal. |
| Scope creep into form/chain shaping | §1 out-of-scope list; refusal guidance points to explicit flags. |
| Model creep (someone wires an LLM) | AC#7 source inspection test; design principle §2.1. |

---

## 11. Definition of done

1. All §7 tests pass; full suite green; demo 30/30 PASS.
2. compileall clean.
3. AC#1–AC#10 verified.
4. Capability matrix + tasks.md updated.
5. No changes to any file in §9 "Untouched" list.
6. Runtime artifacts cleaned; git status inspected.
