# Lakra V1 Final Validation

Validation-only phase (no Slice-52, no features, no product-code changes).
Repository state: post Slice-51. All product code untouched during this
run; validation artifacts live outside the repo
(`%TEMP%\opencode\v1val\`, pages + drivers + per-scenario workdirs).

## Executive Summary

V1 works as a real deterministic browser-automation system on fresh,
locally authored pages: all six roads, chains (2–4 legs), chain resume,
NL shaping, cross-process L3 approval, crash recovery, resource
guards, and read-only observability were exercised through the real
CLI with 55 live scenarios. Safety behavior is fail-closed throughout:
every negative test refused without forbidden action, and the audit
trail matches execution in each sampled case.

Three defects were found and documented (not fixed): one P1, one P2,
one P3. The P1 (chain goal-marker loss on mid-chain process death)
breaks chain resume exactly in the death-during-chain case and should
be resolved before V1 is declared complete. Everything else validated
is either working as designed or an intentional limitation.

## Current Baseline

- Full pytest: **640/640 pass** (run once this phase, ~12.5 min).
- V1 demo (`scripts/demo_v1.py`): **30/30 PASS**.
- `compileall` (scripts + src + tests): **clean**.
- `git status` after validation: only this report added
  (`docs/v1-final-validation.md`); no product files modified.
- Slice-51 chain resume: GREEN from prior phase (unit + bounded CLI).

## Capability Matrix

Legend: A = implemented + real-world validated (this phase, real CLI
on fresh pages); B = implemented + unit/integration tested only;
C = implemented but limited; D = intentionally deferred;
E = missing; F = defect found. Evidence = scenario IDs below and/or
suite names. ("Fresh pages" = 19 locally authored harbor-themed pages
in `%TEMP%\opencode\v1val\pages\`, structurally distinct from
`tests/fixtures`: definition-list nav, table-based index, `<output>`
results, checkbox form, mixed button/submit form, password form,
disabled/hidden controls, decoy-label form, near-miss text page.)

### Core

| Capability | Class | Evidence |
|---|---|---|
| Task lifecycle (QUEUED→RUNNING→COMPLETED/FAILED/CANCELLED, PAUSED, WAITING_APPROVAL; terminal immutability) | A | V-OBS-1, V-FRM-3, V-CHN-5, V-PD-2; `test_lifecycle.py` |
| Deterministic planner (templates + refusal, no inference) | A | All road scenarios; refusals V-FOL-3, V-CLK-3 |
| Policy engine L0–L4 (ALLOW/ASK/BLOCK) | A | V-SEA-1/V-FRM-1 parked (ASK), V-CLK-4/6 submit exclusion, `test_policy.py` 24/24 |
| Scheduler (priority/FIFO, pause/resume, usage write-through) | A | V-RS-1/V-RS-2 pressure pause; V-PD-2 resume; `test_scheduler.py` |
| Execution router (registry choke point, token burn) | A | APPROVAL_CONSUMED exactly once per approval (V-FRM-1, V-AP-1, live audit) |
| Verification (text/url/existence predicates, never click=success) | A | V-FOL-4 wrong-arrival STOPPED; V-OBS-2/3; VERIFICATION PASS/FAIL in every audit |
| SQLite persistence (WAL, versioned, usage/tokens/plans) | A | Cross-process resume/approve flows share one DB file throughout |
| Audit/replay (append-only JSONL, replay equality) | A | V-RO-1 hash-identical; demo act4 replay equality |
| Resource guards (steps/time/pressure → STOP/PAUSE) | A | V-RS-1 (RAM floor → PAUSED + GUARD_TRIP), V-RS-2 (CPU ceiling → PAUSED) |

### Browser roads

| Capability | Class | Evidence |
|---|---|---|
| observe (navigate + snapshot, L0) | A | V-OBS-1 DONE; V-OBS-2/3 safe STOPPED, task stays RUNNING |
| follow (grounded unique link + arrival proof) | A | V-FOL-1 DONE; V-FOL-3/V-FOL-4 refusals; **F** V-FOL-2 (see V1-D2) |
| search (grounded box + identical L3 gate) | A | V-SEA-1 DONE with 1 approval consumed; V-SEA-0/V-SEA-2 pre-plan refusals |
| form (typed slots + L3 gate, denial holds submit) | A | V-FRM-1 DONE; V-FRM-3 exit 2, audit shows navigate/type/type only, no `browser.submit` EXECUTION_COMPLETED |
| loop (links_matching ≤5, establish-per-cycle, per-iteration L3) | A | V-LOP-1 DONE, findings `['gray gull', 'gray petrel']`; V-LOP-2 usage-error; demo pause/resume |
| click (link texts + non-submit buttons, submit excluded) | A | V-CLK-1/5 DONE; V-CLK-2 tie, V-CLK-3 zero-match, V-CLK-4/6 submit-exclusion refusals |

### Composition

| Capability | Class | Evidence |
|---|---|---|
| 2/3/4-leg chains, stop-on-first-failure | A | V-CHN-1/2/3/6 (incl. W4 mixed 4-leg obs→click→obs→search), V-CHN-4 leg-3-never-planned audit |
| click chain legs | A | V-CHN-1; Slice-51 CLI validation (prior phase) |
| chain failure propagation | A | V-CHN-4 (click zero-match → STOPPED, RUNNING) |
| chain approval propagation (per-leg gates, denial → CANCELLED) | A | V-CHN-5 exit 2 CANCELLED; V-CHN-2 search leg parks mid-chain |
| `--from-leg` (fresh new-task rerun) | A | V-CHN-7 exit 0; Slice-51 phase |
| chain resume by leg (same task id) | A | V-CR-1/2 (stopped → resume legs 2..3 → COMPLETED, id preserved); F caveat V1-D1 for death-mid-chain |

### Natural language

| Capability | Class | Evidence |
|---|---|---|
| NL observe / follow / click | A | V-NL-1/2/4 DONE on fresh pages |
| NL search | C | V-NL-3 refused: NL submit phrase is fixed to "search", so a "Find"-labeled submit cannot ground (explicit `--road search --submit` unaffected) |
| Refusal behavior + guidance | A | V-NL-5/6/7 exact guidance strings, exit 1, no task created |
| URL keyword protection | A | V-NL-9: filename containing "click" still shaped observe (URLs excluded from intent vote) |
| Ambiguity handling | A | V-NL-5; unit suite |
| Credential NL refusal | F | V-NL-8 masked (see V1-D3, P3; still fail-safe refusal) |
| NL form / NL loop | D | Refuse with explicit-flag guidance by design (V-NL-8 shows form guidance) |

### Approval

| Capability | Class | Evidence |
|---|---|---|
| L3 approval gate (park → decide → consume) | A | V-FRM-1, V-SEA-1, V-CHN-2 |
| Cross-process approval (`--poll` + `approve.py`) | A | V-AP-1: process A parked, process B approved → DONE |
| Denial prevents execution | A | V-FRM-3: exit 2, CANCELLED, no submit execution in audit |
| Exactly-once token consumption | A | Live DB: approvals (1 approved, 1 denied), `APPROVAL_CONSUMED: 1`, tokens used=1; `test_approvals.py` CAS tests |
| Stale-token protection | A | Slice-51 unit tests (decide-on-settled raises; re-resume mints fresh id); no counterevidence live |
| Process death around approval | A | V-PD-1 (pending refusal naming id), V-PD-2 (approve-after-death + resume DONE) |

### Recovery

| Capability | Class | Evidence |
|---|---|---|
| Single-task resume | A | V-PD-2; prior kill→approve→resume proofs |
| Chain resume | A | V-CR-2; F caveat V1-D1 |
| Stranded task handling (ownership/pau
...[truncated 9113 chars]