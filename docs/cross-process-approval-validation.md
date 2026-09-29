# Cross-Process Approval Validation (FORM road, real Windows processes)

Follow-up to `docs/v1-real-world-validation.md`. That pass proved the
mechanism on fixtures with scripted timing; this pass proves the
*interactive-shaped* workflow — a live `--poll` waiter plus a separate
`approve.py` process, synchronized on DB/audit state rather than sleeps
— including denial, stale approvals, cross-task isolation,
double-decide safety, death mid-wait, and completed-task behavior.

Rule of the pass: fix real bugs properly, change nothing otherwise.
Outcome: **no product bug found; no product file changed.**

## 1. Environment

- Windows PowerShell 5.1, Python 3.12.10, project `.venv`
  (playwright 1.63.0, psutil 7.2.2, pytest 9.1.1).
- Chromium 153.0.8010.12 (Playwright headless, isolated profiles per
  run, all under `Temp`).
- Fixture: `tests/fixtures/courses.html` over `file://`, slots
  name/city/zip(text) + news(checked) + country(select `ng`),
  submit `#m-submit`. Guards disabled per the documented validation
  convention (`--ram-floor-mb 0 --cpu-ceiling 100`).
- Fake credentials only (`hunter2` in refusal probes); no real
  secrets, no live sites, nothing destructive.

## 2. Architecture involved

`browser.submit` → policy L3 → router parks (`ACTION_REQUIRES_APPROVAL`,
row in `approvals`, task `WAITING_APPROVAL`) → `ASK_PENDING` →
`PollingDecider` (read-only wait on shared DB truth) → external
`approve.py` CAS-decides the row → waiter adopts (Slice-37; no
re-decide) → `mint_token` → router `redeem` (binding, expiry, task
`RUNNING`, policy still `ASK`) → `burn` (CAS) → execute → per-field
verification → `PLAN_OUTCOME` → lifecycle. Denial follows the same
path to a clean `STOPPED`, task `CANCELLED`.

## 3. Method (no millisecond races)

`Temp/xval/xvalidate.py` (scratch automation, not committed):
Process A = `lakra_do.py --road form … --poll 150`; the harness polls
the audit file for `ACTION_REQUIRES_APPROVAL` and asserts the pending
DB row + `WAITING_APPROVAL` task *before* Process B
(`approve.py --yes/--no`) runs. Process A must still be alive at that
point (asserted via `poll() is None`). Edge battery in `xedge.py`
(same discipline) covers CASE 1/3–8. All state under fresh `Temp`
dirs; repo `var/` untouched.

## 4. Positive approval result

- Process A parked (audit + DB row asserted while A alive).
- Approval `b29eec34d09649fa83725805fe745604` on task
  `f5c59e1543e34b9b99aeea413d9528a4`.
- Process B approved; Process A adopted, resumed, exited **0**,
  `status: DONE`, task **COMPLETED**.
- Submit executions: **1** (`APPROVED_STEP_EXECUTED` ×1,
  `APPROVAL_CONSUMED` ×1, token row `used=1`); **0** before approval.
- Verifications: 6/6 `PASS` (url + 5 fields/gate checks).
- No stale pending rows; no extra tasks.

## 5. Denial result

- Parked, then `approve.py --no`: Process A exited **2**,
  `STOPPED "human denied approval"`, task **CANCELLED**.
- `APPROVAL_CONSUMED`: **0**. `APPROVED_STEP_EXECUTED`: **0**.
- Later `--resume` on the task refused immutable (history cannot
  silently re-execute a denied submit).

## 6. Process-boundary proof

A and B are separate `python.exe` processes (separate PIDs,
separate SQLite connections, one shared DB file in WAL mode).
Visibility both directions proven: B lists A's parked approval with
task goal + action bytes; A observes B's decision via poll and
completes. No in-process handles, tokens, or registries are shared —
only committed database rows.

## 7. Edge cases (all executed, not inferred)

- **CASE 1** kill→approve→resume: `DONE`, task `3e3b8bbd…` COMPLETED.
- **CASE 3** stale vs new task: stale pending row byte-identical
  after an unrelated task ran to `DONE` in the same DB.
- **CASE 4** two parked tasks, selective approve of A only (via the
  same CAS API `approve.py` uses; note §9): A `DONE` through its live
  poller, B still parked with its row pending, B waiter alive.
- **CASE 5** empty DB: `approve.py` → "no pending approvals", exit 0.
- **CASE 6** double approve: second `approve.py --yes` finds nothing
  (`no pending approvals`); sole waiter still completed once.
- **CASE 7** kill mid-wait: `PRAGMA integrity_check` = `ok`;
  `--list`/`--status` work; `--resume` refuses naming the undecided
  approval (no bypass, no crash).
- **CASE 8** completed task: `approve.py` changes nothing; `--resume`
  refuses immutable.

## 8. Audit proof (positive run)

`VERIFICATION(url_is…PASS)` + per-field verifications PASS *before*
`ACTION_REQUIRES_APPROVAL`; then `APPROVAL_CONSUMED` →
`APPROVED_STEP_EXECUTED` → `PLAN_OUTCOME DONE`; `TASK_LIFECYCLE →
COMPLETED`. Ordering proves verify-before-gate and consume-once.

## 9. Findings during validation

1. **approve.py has no selective mode** (`--only-id`): with two tasks
   parked, `--yes` approves everything pending. Worked as designed
   (no approve-all *bypass* — each item is still individually
   CAS-decided and executed only through tokens), but selective
   approval required the API directly. Observation only; no change
   made. A future `--only-id` would be a CLI addition, not a fix.
2. **Count note:** Phase-0 reads of this tree said 442 collected;
   current definitive collection is 453 = 442 V1 files + 7
   `test_loop_pending.py` (Slice-42, present in-tree) + 4 new here.
   The `tasks.md` line for Slice-42 was never recorded (only V1
   closeout). No test was modified to reconcile this; counts below
   use the definitive collection. Recommend a one-line `tasks.md`
   amendment out-of-band (spec edits are out of scope for this pass).
3. **No product defect found.** The prior manual confusion ("no
   pending approvals") reproduces exactly when the waiter is dead
   before parking — correct behavior, confirmed by CASE 7 + the
   empty case, not an implementation bug.

## 10. Regression-test result

New `tests/test_cross_process_approval.py` (4 tests, real
subprocesses, state-synced): approve-completes, deny-holds,
kill→approve→resume, empty-clean. All pass.
Full suite: **453/453 green** (union of batches, all post-change).
`compileall` warning-free. V1 demo: **30/30 PASS** (baseline pin
442→453 updated as required fallout of the added tests).

## 11. Known limitations

- Public-web and real-credential flows deliberately unattempted.
- Wall-clock mid-execution kills remain impractical to stage at
  fixture speed (prior finding, unchanged); kill-at-park and
  deterministic pause/mid-body resume cover the semantics.
- `approve.py` selective approval needs the API (finding 1).

## 12. Files changed

- `tests/test_cross_process_approval.py` (new, 4 regression tests).
- `scripts/demo_v1.py` (1 constant: baseline pin 442→453).
- `docs/cross-process-approval-validation.md` (this report).
- Nothing else. No `src/`, policy, approval, persistence, browser,
  planner, grounding, dispatcher, guard, CLI-behavior, fixture,
  schema, or existing-test changes.
