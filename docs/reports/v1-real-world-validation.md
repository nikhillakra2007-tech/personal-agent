# Lakra 2.0 V1 Real-World Validation

Validation pass over the completed V1 (Slice-01 → Slice-41), performed
2026-09-29 by an independent QA posture: no product files changed, no
tests edited, no fixtures touched, no slices created. The implementation
under test is taken as-is; every unexpected result below is observed
and classified, never patched.

## 1. Repository / revision tested

- Local tree under test: `Lakra-2.0/` (Windows path used during
  validation; content certified below).
- `docs/VERSION`: `0.1.0`, baseline 442 passed, demo 30/30 PASS,
  dated 2026-09-29.
- `specs/001-lakra-v1/tasks.md`: Slice-01 → Slice-41 plus V1 closeout
  line; deferred post-V1 items C-A/C-B (foreground watchdog, visible
  lock indicator) unchanged since approval 2026-09-28.
- VCS status: **the local tree is not a git repository** (no `.git`;
  `git status` → "not a git repository"). The GitHub remote
  `https://github.com/nikhillakra2007-tech/personal-agent` is reachable
  but **empty** (clone yields only `.git`, zero commits, no branches).
  Consequence: there is no commit hash to record for the tree under
  test, and V1 code exists only locally. See finding F-01 (§9) and the
  Level 1 adaptation (§4).

## 2. Environment

- OS: Windows (PowerShell 5.1 harness). CPU/RAM class per runbook:
  16 GB RAM laptop with shared load during validation.
- System Python: 3.12.10, pip 25.0.1.
- Author venv (tree `.venv/`): playwright 1.63.0, psutil 7.2.2,
  pytest 9.1.1 (plus greenlet/pyee/packaging/pluggy/iniconfig/
  Pygments/typing_extensions/colorama pinned by install).
- Level-1 fresh venv: identical pinned set installed from PyPI
  (`playwright psutil pytest` — there is no requirements.txt; see
  finding D-02 §9).
- Browser: Playwright Chromium 153.0.8010.12 via the shared
  `%LOCALAPPDATA%\ms-playwright` store (chromium-1243). Shared
  machine-level binaries, not project state; a bare machine would need
  `playwright install chromium` (not exercised — network to the CDN
  was not tested).
- All validation state (databases, profiles, shots, audits) under
  fresh `Temp` directories per test; repo `var/` untouched by
  validation runs (CLI invocations passed explicit `--audit`,
  `--profile-dir`, `--shots-dir`, and `LAKRA_DB`).

## 3. Certified baseline (Phase 0, local tree)

- `compileall src tests scripts`: clean, warning-free
  (`-W error::SyntaxWarning`).
- `pytest --co`: **442 tests collected**.
- Full suite, four batches: 284 + 71 + 58 + 29 = **442 passed**,
  zero failures (durations ≈ 89s + 188s + 353s + 144s).
- `scripts/demo_v1.py`: **DEMO PASS, 30 checks across 5 acts**.
- Baseline verdict: **reproduced exactly as certified. PASS.**

## 4. Level 1 — Clean Checkout

Goal: can another developer obtain the repository and reproduce V1
without author machine state?

- **L1-1 — Clone from GitHub: FAIL (non-product).** `git clone`
  succeeds but warns "You appear to have cloned an empty repository";
  the clone contains only `.git`. There is nothing to check out, so
  the level could not run as specified. Classified as publication gap
  F-01 (§9), not a product defect. No workaround applied to the
  product.
- **Adaptation (recorded deviation):** pristine filesystem copy of the
  tree via robocopy excluding machine state (`.venv/`, `var/`,
  `__pycache__`, `.pytest_cache`, `*.pyc`), fresh venv, fresh pip
  install (identical pins verified via freeze), shared Chromium
  binaries as noted in §2. No author DB/profile/cache reused.
- **L1-2 — Fresh install: PASS.** `pip install playwright psutil
  pytest` completed from PyPI; freeze matches the author venv pin
  for pin.
- **L1-3 — Chromium in fresh env: PASS.** Headless launch + version
  report OK (153.0.8010.12).
- **L1-4 — compileall + collect in pristine copy: PASS**
  (warning-free; 442 collected).
- **L1-5 — Full suite in pristine copy: PASS**, 284 + 71 + 58 + 29 =
  442 passed across four batch runs (45s + 59s + 109s + 113s).
- **L1-6 — Demo in pristine copy: PASS**, 30/30 checks, 5 acts.
- Level 1 verdict: **PASS with one recorded deviation** (empty origin
  forced a pristine-copy substitution for the clone step). Setup gap
  found: `docs/demo-v1.md` Act 0 cites `pip install -r
  requirements.txt`, but **no requirements.txt exists anywhere in the
  tree** (finding D-02 §9, documentation issue). The working set is
  `playwright psutil pytest` per both venv freezes.

## 5. Level 2 — Real-World Browser (fresh local pages, outside fixtures)

Pages authored for validation in `Temp` only (never in the repo):
harbor-library catalog (4 items + detail pages), ambiguous shelf,
membership form (name/hometown/country + submit), sign-in page with
password field, three-button desk page. All `file://`, all harmless.

- **T2.1 navigation + observation: PASS.** Loop over the unseen
  catalog (`--road loop`, goal "Open every catalog entry"): exit 0,
  `DONE`, findings all 4 items in order, task COMPLETED
  (`06813021…`). Honest note: the first attempt with goal "Open every
  catalog item" was **correctly refused** (`no groundable match` —
  grounding matches link texts only and the links shared no word);
  the test page (not the product) was fixed to share "entry".
- **T2.2a specific link follow: PASS.** "Open the Tide almanac entry
  page" → exit 0, `DONE/4`, task COMPLETED (`f99304cd…`).
- **T2.2b ambiguous target: PASS.** "Show Harbor" against Harbor
  guide/manual → exit 1, `STOPPED`, `no groundable link (ambiguous
  links ['Harbor guide', 'Harbor manual']: refuse, never guess)`,
  task left RUNNING, nothing executed.
- **T2.3 multi-field form: PASS.** Name/hometown/country bound to
  `#f-name/#f-town/#f-nation` with caller values (Test User, Jaipur,
  `in` visible in `ACTION_ALLOWED` targets), all three fields
  `VERIFICATION … PASS` **before** `ACTION_REQUIRES_APPROVAL`, single
  `APPROVAL_CONSUMED` → `APPROVED_STEP_EXECUTED` → `DONE`, task
  COMPLETED (`385afb8a…`). Submission waited at L3; never pre-approved.
- **T2.4 approval flow (delayed): PASS.** Parked via `--poll`, killed;
  `--list`/`--status` showed `WAITING_APPROVAL` + named pending id +
  not-resumable; pre-approve `APPROVED_STEP_EXECUTED` count = 0
  (nothing submitted early); `approve.py --yes` → `--resume` →
  `DONE`, task COMPLETED (`3a19e822…`); post-run audit: consumed 1,
  executed 1, all four verifications PASS.
- **T2.5 denial flow: PASS.** `--no` → exit 2, `STOPPED "human denied
  approval"`, task CANCELLED (`27ece5d0…`); audit shows
  `APPROVAL_CONSUMED: 0`, `APPROVED_STEP_EXECUTED: 0`; later
  `--resume` refused `CANCELLED; history immutable` (exit 1) — the
  denied action cannot silently execute later.
- Level 2 verdict: **6/6 PASS.** Public-web targets were deliberately
  not attempted (out of the safe-targets rule for this pass; the
  `file://` + explicit `--allow-domain` boundary was exercised
  throughout instead).

## 6. Level 3 — Failure / Safety

- **T3.1 process kill + resume: PASS (with documented harness
  limits).** Kill-at-park + resume verified with re-observation
  evidence (`PLAN_RESUMED` logged once, fresh snapshot 95 chars) and
  exactly one submit `EXECUTION_STARTED` (no blind repeat). Pre-
  persistence and post-completion kills fail closed (refusals observed
  directly). Wall-clock mid-execution kill was attempted 4× but the
  fixture-scale runs outran Windows job control each time (two runs
  completed pre-kill → terminal-refusal, correctly; two kills landed
  on already-terminal work) — a validation-harness limit, not a
  product result; the deterministic equivalents (pause/mid-body resume
  in-suite, cursor-1 stub resume, and T3.3 below) cover the semantics.
- **T3.2 pending approval + process death: PASS.** Parked, killed,
  inspected from a second process (`--list`/`--status` correct,
  approval persists and stays visible), approved, resumed → `DONE`.
  Approval state and task state both survived the death; resume never
  bypasses the gate (pending → named refusal first).
- **T3.3 resource guard: PASS.** Loop with `--ram-floor-mb 999999` →
  `PAUSED` (`paused: low RAM…`, `GUARD_TRIP` in audit, zero body
  steps, task `PAUSED`); `--resume` after relief → `DONE`, both
  findings, task `COMPLETED`. Full RUNNING → pressure → PAUSED →
  resume → DONE trail recorded.
- **T3.4 ambiguous target: PASS.** "Click the button" on a no-link
  page → `STOPPED`, `no groundable link (empty inventory)`, zero
  clicks executed; tie case covered live in T2.2b. Nothing arbitrary
  is ever selected.
- **T3.5 credential safety: PASS.** Password slot on a sign-in page →
  `STOPPED`, `no groundable fields (slot 'password' matches no
  control label)`; live probe confirms the password control is
  omitted from the control inventory (only `Username/text/#s-user`
  inventoried) and the snapshot redacts the field
  (`redactions_applied: 1`, `CREDENTIAL_MARKER` present); the
  attempted fake value and the fixture's password value appear in
  **zero** audit files (Select-String, no matches).
- **T3.6 policy boundaries: PASS (layered).** L0/L1 automatic live
  (follow DONE, zero approvals); L2 sandboxed live (`fs.list C:\`
  out-of-workspace → ASK parked → denied, exit 2, task cancelled);
  L3 approval-gated live (T2.3/T2.4); L4 BLOCK by truth table
  (`test_policy.py` 24/24 incl. `captcha.defeat` → BLOCK even when
  mislabeled `read`); a live CAPTCHA-defeat goal executes zero
  clicks/types (planner refuses before any L4-shaped action can be
  constructed — defense in depth, recorded as designed).
- **Finding D-01 (minor behavioral defect, fail-closed): loop
  pending-body resume on a fresh stack stops instead of continuing.**
  A loop guard-paused mid-body (pending body plan persisted), process
  killed, then `--resume` on a fresh stack returns `STOPPED`
  ("iteration stopped: resume refused: no fresh state") because
  `resume_info` carries no URL for the loop road, so the fresh
  observation cannot establish a page. Findings preserved in order,
  nothing executed without consent, loop row terminal-STOPPED (task
  left RUNNING). Narrow preconditions (pause must strike mid-body +
  death + fresh-stack resume); zero safety impact; but a stranded
  loop the resume contract implies should continue. Repro,
  task `e2694155…`, loop `f537c5ce…` (cursor 3, `t31d` state):
  pause-mid-body → kill → `--resume` → STOPPED as above. NOT fixed
  per validation rules; proposed as Slice-42 candidate (surface the
  pending body plan's persisted URL into discovery so the observe
  seam can establish it).
- Level 3 verdict: **6/6 PASS with one minor defect recorded (D-01).**

## 7. Test Matrix

| ID | Name | Result | Exit | Evidence |
|---|---|---|---|---|
| P0-1 | compileall clean | PASS | 0 | warning-free (`-W error`) |
| P0-2 | collect baseline | PASS | — | 442 collected |
| P0-3 | suite batch 1 (non-live) | PASS | 0 | 284 passed |
| P0-4 | suite batch 2 (composed/live) | PASS | 0 | 71 passed |
| P0-5 | suite batch 3 (acceptance/browser) | PASS | 0 | 58 passed |
| P0-6 | suite batch 4 (browser rest) | PASS | 0 | 29 passed |
| P0-7 | Slice-41 demo | PASS | 0 | 30/30, 5 acts |
| L1-1 | clone from GitHub | FAIL* | — | *empty origin; see F-01 (non-product) |
| L1-2 | fresh pip install | PASS | 0 | pins match author venv |
| L1-3 | chromium in fresh env | PASS | 0 | 153.0.8010.12 headless |
| L1-4 | pristine compileall+collect | PASS | 0 | clean, 442 |
| L1-5 | pristine full suite | PASS | 0 | 284+71+58+29=442 |
| L1-6 | pristine demo | PASS | 0 | 30/30 |
| T2.1 | unseen catalog loop | PASS | 0 | DONE, 4/4 findings, COMPLETED |
| T2.2a | specific link follow | PASS | 0 | DONE/4, COMPLETED |
| T2.2b | ambiguous link tie | PASS | 1 | STOPPED, named winners, 0 executions |
| T2.3 | multi-field form L3 | PASS | 0 | 3 verifications PASS pre-gate, consumed×1 |
| T2.4 | delayed approve+resume | PASS | 0 | 0 pre-executions, consumed×1, COMPLETED |
| T2.5 | denial holds submit | PASS | 2 | consumed×0/executed×0, CANCELLED, resume refused |
| T3.1 | kill + resume rules | PASS | — | re-observed (95ch), 1 submit start, fail-closed edges |
| T3.2 | pending survives death | PASS | 0 | visible→approved→DONE via resume |
| T3.3 | guard pause/resume | PASS | 0/1 | PAUSED→DONE, GUARD_TRIP trail |
| T3.4 | ambiguous, no arbitrary click | PASS | 1 | 0 clicks, shaped refusal |
| T3.5 | credential refusal+redaction | PASS | 1 | slot refused, inventory omits, marker set, 0 leaks |
| T3.6 | L0–L4 boundaries | PASS | 0/1/2 | auto/gate/deny live, BLOCK by table |
| D-01 | loop pending-body fresh resume | DEFECT | 1 | shaped STOPPED, findings kept, §11 |

## 8. PASS / FAIL / BLOCKED Summary

- Validation tests performed: **25 rows** (7 P0 + 6 L1 + 6 L2 + 6 L3).
- **PASS: 24. FAIL: 1** (L1-1, empty origin repository — publication
  gap, not product behavior). **BLOCKED: 0.**
- Product-behavior verdicts: every executed product test PASSED;
  one minor fail-closed defect recorded (D-01).

## 9. Unexpected Behavior

1. **Initial loop-goal refusal on the new catalog page** (`no
   groundable match — not a loop goal`). Correct per design
   (grounding matches link texts; my links shared no word). Fixed the
   test page, not the product.
2. **Windows job control vs. fast runs.** `Stop-Job` repeatedly landed
   after completion or failed to stop live child processes (whose
   orphaned Chromium instances later tripped RAM guards mid-
   validation — visible as an unexpected `PAUSED`, itself correct
   behavior). Timing-based kills were replaced with state-based ones
   (poll DB/persisted markers, guard-induced pauses). Validation-
   harness limit; Lingering `headless_shell.exe` processes were
   terminated at cleanup (user `chrome.exe` untouched).
3. **D-01** (§6/§11): the only product-side surprise; fail-closed.

## 10. Confirmed V1 Limitations

- V1 code exists only in the local tree; the GitHub remote is empty
  (F-01). Clean-checkout-from-GitHub is impossible until published.
- No `requirements.txt`; working set is `playwright psutil pytest`
  (D-02 documentation gap).
- Fixture-scale runs complete in seconds, so wall-clock mid-execution
  kills are impractical to stage live; covered deterministically
  in-suite instead.
- Public-web targets deliberately unattempted (safe-targets rule).
- AC#3 physical foreground half remains approved-deferred (unchanged).
- Guard floors are environment-sensitive by design (documented
  overrides used throughout).

## 11. Actual Defects

**D-01 (sole defect) — loop pending-body resume on a fresh stack
stops instead of continuing. Severity: Low. Safety/data impact:
none (fail-closed STOPPED, findings preserved in order, zero
unconsented executions; loop row terminal, task left RUNNING).**

- Repro: 3+-item loop that guard-pauses mid-body (pending body plan
  persisted) → kill process → `--resume` on a fresh stack with a
  different profile dir.
- Exact: `lakra_do --resume e2694155…` (loop `f537c5ce…`, cursor 3)
  → `status: STOPPED`, `detail: "iteration stopped: resume refused:
  no fresh state (no page open; navigate first)"`, exit 1.
- Expected (per the resume contract): resume establishes fresh
  observation and continues remaining items (as the cursor-0 and
  single-road paths do).
- Actual: STOPPED; the loop row is terminal so a retry refuses.
- Affected component: `LoopRunner.resume` pending-plan branch +
  `resume_info` (carries no URL for the loop road) as consumed by
  `lakra_do --resume`'s observe seam.
- Suspected cause (not patched): the single-road observe seam opens
  the persisted plan URL first, but loop discovery reports no URL, so
  `Runner.resume(pending_plan, observe)` fails its mandatory fresh-
  observation check; `_after_body` maps the resulting ASK-less STOPPED
  to loop-STOPPED. The persisted pending body plan's hints contain
  the URL — discovery could surface it.
- Path stopped after classification; no fix applied, no Slice-42
  created (out of scope for validation).

## 12. Evidence / Audit References

- Phase-0 audit trails: local-tree pytest runs (442) + demo workdir
  `lakra-demo-m2ekz0cf` (30/30).
- Level-1 audit trails: pristine-copy suite batches (442) + demo
  workdir `lakra-demo-r5j2sayq` (30/30); fresh venv freeze recorded
  in §2.
- Level-2/3 audit trails: `Temp\opencode\lvl2\t*.jsonl` databases
  (`t21` COMPLETED loop×4, `t22` COMPLETED + RUNNING-refused,
  `t23` COMPLETED form, `t24` kill→approve→resume COMPLETED,
  `t25` CANCELLED deny, `t31d` RUNNING loop STOPPED@3, `t33`
  PAUSED→COMPLETED, `t34/35/36b` shaped refusals) with per-test
  event counts quoted in §§5–6.
- Task IDs, exit codes, and final states are recorded per row in §7.
- Note: Temp workdirs are removed at cleanup per the validation
  rules; the transcribed outputs above are the preserved evidence.

## 13. Final Validation Status

- Baseline: reproduced (442/442 + 30/30 demo PASS).
- Level 1: passed with one recorded deviation (empty origin; pristine
  copy substituted; setup-docs gap D-02 noted).
- Level 2: 6/6 passed on unseen pages.
- Level 3: 6/6 passed; one minor fail-closed defect recorded (D-01).
- **V1 completed its three-level validation pass**, with findings
  F-01 (publish the tree), D-02 (add requirements.txt), and D-01
  (loop pending-body fresh resume) handed back for separate
  disposition. No product code was changed during validation.
