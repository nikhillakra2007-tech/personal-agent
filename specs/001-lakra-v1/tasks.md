# Tasks — 001-lakra-v1 (planning only; build starts after approval)

## slice-01 — Control core (BUILT 2026-09-26, all green; see report in session)

- [x] T1: `Task` dataclass per `docs/contracts/task-schema.md` (+ status transitions validated)
- [x] T2: `PolicyEngine.evaluate(action, task)` per `docs/contracts/policy-schema.md` (L0–L4 → ALLOW/ASK/BLOCK truth table)
- [x] T3: In-memory task registry (create/get/update, single-owner checkout, atomic)
- [x] T4: Audit event writer (append-only JSONL, full taxonomy, replay reads back identical stream)
- [x] T5: Scripted replay test: two tasks (L1 navigate vs L3 submit) → assert ALLOW vs ASK; checkout twice → second fails; replay audit → matches
- [x] T6: Slice notes (what/why/how, communication, data flow, security/resource notes, tests + results, limits, next)

Manual verification: run replay script, read JSONL, confirm verdicts + lock behavior.

## Later slices (planned, not authorized)

- slice-02: scheduler + locks + pause/resume/cancel + `ModelProvider` interface + minimal ASK resume (CLI prompt) — BUILT 2026-09-26, 67/67 green
- slice-03: tool router + sandboxed filesystem/terminal executors — BUILT 2026-09-26, 88/88 green
- slice-04: read-only browser observer (navigate/snapshot/screenshot) — BUILT 2026-09-26, 99/99 green
- slice-05: browser actions + verification loop + recovery (ASK resume required here) — BUILT 2026-09-26, 122/122 green
- slice-06: `LocalProvider` wiring (Ollama) + resource monitor + budgets — BUILT 2026-09-26 (foundation only; no Ollama install, no live model), 146/146 green
- LIVE-PROOF 2026-09-27: Ollama 0.34.4 installed (loopback-only), qwen2.5:0.5b (397 MB) pulled; LocalProvider + proposal boundary proven live; 2 real bugs fixed (unwrapped OSError, POST /api/tags); 165/165 green
- slice-07: scoped MCP + polished approvals UX (mechanics already in place) — BUILT 2026-09-26 (incl. one-shot approval tokens), 165/165 green
- slice-08: SQLite persistence (tasks/approvals/tokens/usage) + boot/crash recovery + cross-process CLI — BUILT 2026-09-26, 180/180 green
- slice-09: deterministic planner v0 (templates + refusal) + runner loop — BUILT 2026-09-26, 193/193 green
- slice-10: measured model proposals (live qwen, strict adapter, fallback) — BUILT 2026-09-26, 202/202 green
- slice-11: task-carried hints + genuine replan + plan persistence (immutable history, resume-after-fresh-observe) — BUILT 2026-09-27, 210/210 green
- slice-12: model-aware replan + observational failure corpus (no learning) — BUILT 2026-09-27, 219/219 green
- slice-13: supervised end-to-end loop (abstract decider) + retention pruning + thin CLI — BUILT 2026-09-27, 229/229 green
- slice-14: task lifecycle ownership (plan DONE/STOPPED -> task mapping, cancel propagation) — BUILT 2026-09-27, 237/237 green
- slice-15: deterministic analyzer v0 (goal text -> validated hints, no model calls) — BUILT 2026-09-27, 246/246 green
- slice-16: diff verification (appeared/disappeared/count/url-changed) + snapshot store + explicit NO_BASELINE — BUILT 2026-09-27, 255/255 green
- slice-17: execution attempt ledger + narrow duplicate suppression + lineage + boot abandon — BUILT 2026-09-27, 264/264 green
- slice-18: retention sweep for attempts/approvals/tokens/usage (dry-run, tombstones kept) — BUILT 2026-09-27, 272/272 green
- slice-19: measured analyzer model-assist (additive merge, deterministic wins, overrule logged) — BUILT 2026-09-27, 281/281 green
- slice-20: automatic guardrail enforcement (Guards: steps/time+tokens->STOP, pressure->PAUSE+GUARD_TRIP, guard-error fail-safe pause, resume-after-relief) — BUILT 2026-09-28, 293/293 green
- slice-21: V1 acceptance harness (supervised fixture-only A/B/C/D, verdict table, replay equality) — BUILT 2026-09-28, 299/299 green
- slice-22: live browser.submit executor (hands, L3 path unchanged, stub twins retired, page-state proof) + threshold measurement note (defaults kept, conservative) — BUILT 2026-09-28, 303/303 green
- slice-23: multi-field form fill (fields hint 1..8, fill-multi template, per-field verification, L3 gate unchanged, single-field byte-identical) — BUILT 2026-09-28, 309/309 green
- slice-24: locator fallback CSS->text->role (first rung wins, rung recorded, CSS hits byte-identical, total miss unchanged) — BUILT 2026-09-28, 314/314 green
- slice-25: stateful browser.check (idempotent, strict checked|unchecked, new predicates, mixed text+check fields, radios included, selects deferred) — BUILT 2026-09-28, 326/326 green
- slice-26: native single-select browser.select (exact value/label, value-preferred, ambiguity/zero/index/multi/disabled refused, mixed fields, no new predicates) — BUILT 2026-09-28, 335/335 green
- slice-27: follow-link detail flows (capped link inventory ahead of truncation, 4-step template, detail fixture, analyzer intent realigned) — BUILT 2026-09-28, 341/341 green
- slice-28: table inventory in snapshots (capped tables/rows/cells/chars, truncation-safe placement, header stars, fixture price table) — BUILT 2026-09-28, 344/344 green
- slice-29A: cursor-unrolled links_matching loops (RepeatSpec ≤5, establish-per-cycle, first_empty, budget precheck, cursor/findings persistence, LOOP_* audit, per-iteration L3, v5 migration) — BUILT 2026-09-28, 354/354 green
- slice-30: grounded link hints (deterministic inventory scoring, strict unique winner, ties/empty refused, planner untouched, live DONE) — BUILT 2026-09-28, 362/362 green
- slice-31: grounded form controls (labeled #id inventory, strict typed slot bindings, per-slot refusal, values caller-supplied, L3 gate unchanged) — BUILT 2026-09-28, 370/370 green
- slice-32: composed grounded-link task runner (goal -> observe -> ground_match -> RepeatSpec -> LoopRunner, grounded-link road only, deterministic, executors/predicates/templates/policy untouched) — BUILT 2026-09-29, 376/376 green
- slice-33: composed grounded-follow task runner (goal -> observe -> grounded_follow -> TaskLoop.run_goal, single-follow road only, deterministic, executors/predicates/templates/policy untouched, live DONE) — BUILT 2026-09-29, 381/381 green
- slice-34: composed grounded-form task runner (goal -> observe -> ground_fields -> TaskLoop.run_goal, single-form road only, deterministic, L3 gate unchanged, live approve DONE / deny held) — BUILT 2026-09-29, 387/387 green
- slice-35: composed road dispatcher (union goal -> key-presence route -> run_follow/linked/form_task unchanged, no probing/guessing/fall-through, result passthrough, live all-roads proof) — BUILT 2026-09-29, 394/394 green
- slice-36: thin browser CLI scripts/lakra_do.py (--road follow|loop|form -> union goal -> run_task() one-shot, isolated profile, real guards, --yes/--no/--poll/interactive deciders, exit 0/2/1; scripts-only, src/ untouched) — BUILT 2026-09-29, 405/405 green
- slice-37: adopt externally-settled approvals (Approvals.adopt() read-only + TaskRegistry.refresh() resync, run_plan + lakra_run.py --poll adoption, CAS/token/recheck/deny/audit unchanged, two-process --poll approve/deny proof) — BUILT 2026-09-29, 412/412 green
- slice-38: resume stranded task by id (control/resume.py discovery + TaskLoop.resume_plan supervised drain, lakra_do --resume TASK_ID, pending/uncertain/ownership refusals, re-park fresh consent, kill→approve→resume live proof) — BUILT 2026-09-29, 424/424 green
- slice-39: read-only task visibility (lakra_do --list/--status on existing readers only, full-id output, truncated goals, allowlisted audit tail, approve→resumable→resume without sqlite, byte-identical files) — BUILT 2026-09-29, 434/434 green
- slice-40: machine-readable visibility (lakra_do --list/--status --format text|json, closed schemas, explicit-key serialization, blocked-shape parity, scripts-only, src/ untouched) — BUILT 2026-09-29, 442/442 green
- V1 CLOSEOUT 2026-09-29: scripts/demo_v1.py 30/30 PASS (docs/demo-v1.md runbook, AC#1/AC#2/AC#4/AC#5 demonstrated, AC#3 physical half stands deferred per C-A/C-B); full baseline 442 passed; docs/VERSION 0.1.0 recorded. No Slice-42 needed — demo exposed zero behavioral defects.
- slice-42 (D-01 fix) 2026-09-29: loop pending-body resume on fresh stacks (resume_info surfaces pending-body URL; LoopRunner.resume pending branch drains supervised via resume_plan; resume_task loop road requires observe) — BUILT, 449/449 green; demo 30/30 still PASS. One pre-existing Slice-38 test updated to the corrected supervised-consent outcomes (documented). No Slice-43.
- slice-43: composed observe road (run_observe_task: goal -> TaskLoop.run_goal via observe template, L0 read-only, no seams/approvals/side effects; dispatcher url/expect_text branch + mixed refusal; CLI --road observe) — BUILT 2026-09-29, 460/460 green; demo 30/30 still PASS. Capability audit in docs/capability-matrix.md (all planner templates now goal-reachable).
- slice-44: composed search road (navigate -> grounded query box -> type+verify -> identical L3 submit gate -> results verification; policy untouched, every search parks once; "web search" dispatch first; fixed "search" grounding slot) — BUILT 2026-09-29, 471/471 green; demo 30/30 still PASS. One-word analyzer INTENT_NAMES realignment (positional parallel to TEMPLATES) required for byte-identical intents.
- slice-45: chained multi-template runs (control/chain.py run_chain over the unchanged dispatcher on a sibling non-completing runner; per-leg routing goals with restore; 2-4 legs, loop legs refused, first non-DONE leg ends the chain; CLI --chain-file/--from-leg) — BUILT 2026-09-29, 483/483 green; demo 30/30 still PASS.
- slice-46: natural-language goal shaping (control/shaping.py shape_goal: deterministic prose -> road goal dict for observe and follow with arrival phrase; search/click/form/loop refuse with explicit-flag guidance; no model calls; policy/guards/L3 gate untouched; CLI --goal additive, explicit --road byte-identical) — BUILT 2026-09-30, 514/514 green; demo 30/30 still PASS.
- slice-47: NL search shaping (analyzer.ground_submit + observer.collect_submits: deterministic submit-control grounding; shaping.py search intent with query/URL/expect_text extraction; CLI run_goal grounds submit selector before run_task; explicit --road search unchanged; duplicated run_chain_file consolidated) — BUILT 2026-09-30, 543/543 green; demo 30/30 still PASS.

## Deferred post-V1 (approved 2026-09-28)

- C-A foreground watchdog + user preemption, C-B visible lock indicator: V1's execution plane is background-only (isolated Playwright profile; physical path never exercised), so AC#3's physical half is inapplicable, not failing. Lock contract (exclusive, released, no steal) is enforced for the day foreground steps exist.
