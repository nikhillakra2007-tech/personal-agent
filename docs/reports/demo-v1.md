# Lakra V1 Demo Runbook (Slice-41 closeout)

This is the V1 demonstration: one story, start to finish, on a clean
checkout, using frozen fixtures and the frozen CLI. It proves the
buddy loop — do, park, approve, die, discover, resume, done, replay —
with no developer hands inside the run. The runnable version is
`scripts/demo_v1.py`, which executes exactly the commands below and
self-checks every step (exit codes, output markers, audit replay
equality). This document is the narration; the script is the proof.

Scope: fixtures only (`tests/fixtures/loop-index.html`,
`tests/fixtures/courses.html` served over `file://`). Live-web
behavior is not demoed and not claimed.

## 0. Prerequisites

- Windows host, Python 3.12, this repository checked out clean.
- Virtual environment with dependencies installed (the same
  `pip install -r requirements.txt` the test suite uses), Playwright
  Chromium available (as used by the pytest live suite).
- No prior state required: the demo uses an isolated workdir
  (`LAKRA_DB`, audit, profiles, shots all under one temp dir, printed
  at start). Nothing under the repo is written except the workdir you
  point at (default: fresh temp dir).
- Reference machine for timing: 16 GB RAM / 6 GB VRAM Windows laptop.
  On loaded machines the documented `--ram-floor-mb` / `--cpu-ceiling`
  overrides apply (same values as the test suite); they change guard
  floors, nothing else. Total runtime target: under ~10 minutes.

## Act 0 — Setup (clean-checkout baseline)

```powershell
.\.venv\Scripts\python.exe -m compileall src tests scripts
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --co
.\.venv\Scripts\python.exe scripts\demo_v1.py
```

Expected: compileall warning-free; collection reports the recorded
baseline (**442 tests**, see `specs/001-lakra-v1/tasks.md`); then the
driver takes over. The driver aborts on the first failed act.

## Act 1 — Follow DONE (no approvals, no direction)

```powershell
$env:LAKRA_DB = "<workdir>\demo.db"
.\.venv\Scripts\python.exe scripts\lakra_do.py --road follow `
  --url <loop-index.html uri> --text "Follow the Beta record" `
  --expect "detail record: Beta" --yes `
  --audit <workdir>\a.jsonl --profile-dir <workdir>\prof `
  --shots-dir <workdir>\shots --ram-floor-mb 0 --cpu-ceiling 100
```

Expected: exit `0`, `status: DONE`, `steps: 4`, task `COMPLETED`,
detail page for the Beta record opened. No approval prompt appears
(the follow road has no L3 step).

## Act 2 — Form parks at L3; visibility narrates it

```powershell
.\.venv\Scripts\python.exe scripts\lakra_do.py --road form `
  --url <courses.html uri> `
  --slots-json '{"name": {"text": "Ada"}, "city": {"text": "Lagos"}, "zip": {"text": "10001"}, "news": {"checked": true}, "country": {"select": "ng"}}' `
  --submit "#m-submit" --poll 120 [same --audit/--profile/--shots/floors]
```

While it waits, in a second terminal (same `LAKRA_DB`):

```powershell
.\.venv\Scripts\python.exe scripts\lakra_do.py --list --audit <workdir>\a.jsonl
.\.venv\Scripts\python.exe scripts\lakra_do.py --status <TASK_ID> --audit <workdir>\a.jsonl
.\.venv\Scripts\python.exe scripts\lakra_do.py --status <TASK_ID> --format json --audit <workdir>\a.jsonl
```

Expected: `--list` shows the task `WAITING_APPROVAL`; `--status`
shows the pending approval id, `not resumable: undecided approval …`,
and the safe prefix already applied in the audit tail (fields
verified, submit parked at `ACTION_REQUIRES_APPROVAL`). The JSON form
parses and agrees field-for-field. Then kill the waiter (Ctrl-C /
terminate) — the task strands mid-gate, exactly the real-life case.

## Act 3 — Approve, resume, deny, pause (recovery in all directions)

```powershell
.\.venv\Scripts\python.exe scripts\approve.py --yes          # cross-process approve
.\.venv\Scripts\python.exe scripts\lakra_do.py --resume <TASK_ID> --yes [same dirs/floors]
```

Expected: exit `0`, `DONE/7`, `#m-status` reads `submitted`, task
`COMPLETED`. The pre-death approval is adopted through the unchanged
token path (exactly-once); the resumed run re-parks fresh and the
second consent executes it.

Deny twin (second form task, parked and killed the same way). First,
`--resume` while the park is undecided refuses naming the approval id
(exit `1`, zero new rows). Then deny in `approve.py`: the task goes
`CANCELLED`, and `--resume` correctly refuses as terminal
(`history immutable`, exit `1`) — a decided denial is an end state,
not something to resume. The exit-`2` deny path itself is shown live
at the gate: a fresh form run with `--no` stops with
`STOPPED "human denied approval"`, task `CANCELLED`, submit held
(`#m-status` stays `not submitted`).

```powershell
.\.venv\Scripts\python.exe scripts\lakra_do.py --resume <TASK_ID_2> --yes
# -> exit 1, names the undecided approval id, zero new approval rows
.\.venv\Scripts\python.exe scripts\approve.py --no
# -> task CANCELLED (decided denial is an end state)
.\.venv\Scripts\python.exe scripts\lakra_do.py --resume <TASK_ID_2> --yes
# -> exit 1, refused: terminal history is immutable (correct: nothing to resume)
.\.venv\Scripts\python.exe scripts\lakra_do.py --road form ... --submit "#m-submit" --no
# -> exit 2, STOPPED "human denied approval", task CANCELLED, submit held
```

Guard twin (loop road, pressure-induced pause, no kill needed):

```powershell
.\.venv\Scripts\python.exe scripts\lakra_do.py --road loop ... --ram-floor-mb 999999
# -> PAUSED ("paused: low RAM …"), loop row persisted at cursor 0
.\.venv\Scripts\python.exe scripts\lakra_do.py --resume <TASK_ID_3> --yes --ram-floor-mb 512
# -> DONE, findings [Alpha record, Beta record, Gamma record], COMPLETED
```

## Act 4 — Audit replay (believe nothing; re-read everything)

```powershell
.\.venv\Scripts\python.exe scripts\lakra_do.py --list --state all --audit <workdir>\a.jsonl
```

Expected: DONE / CANCELLED / COMPLETED rows for every act above.
The driver additionally re-reads the audit file with a fresh reader
and asserts event-for-event equality with the live-observed trail,
plus the required type subsequence (`PLAN_CREATED` →
`ACTION_REQUIRES_APPROVAL` → `APPROVAL_CONSUMED` →
`APPROVED_STEP_EXECUTED` → `PLAN_OUTCOME`, `LOOP_*` where loops ran,
no new types ever).

## Acceptance-criteria mapping

| Spec AC | Demo coverage | Honest boundary |
|---|---|---|
| AC#1 docs-search follow without per-click direction | Act 1: goal in, detail page out, decider never consulted | Fixture pages stand in for live docs sites; live-web search is not demoed or claimed |
| AC#2 portal workflow stops at submission, resumes on decision | Acts 2–3: safe prefix runs, submit gates, approve → submitted; deny twin holds | Single-form scope; longer portal journeys are not demoed |
| AC#3 physical lock exclusive/indicated/released, instant takeover | **DEFERRED as approved 2026-09-28** (tasks.md C-A/C-B): the execution plane is background-only on an isolated Playwright profile; the lock contract is enforced in code for the day foreground steps exist. Stated here verbatim, demoed nowhere, by design | No foreground input is exercised |
| AC#4 overspend/resource pressure pauses automatically | Act 3 guard twin: pressure → `PAUSED` → relief → resume → `DONE`; budgets enforced on every step throughout | Mechanism demo on fixture timings, not a live OOM |
| AC#5 every run replayable from the audit log | Act 4: fresh-reader replay equality + terminal review | — |

## Clean-checkout reviewer run

Reviewer, on a machine that has never built this tree: clone → venv
→ install → Act 0. Then `demo_v1.py` with no arguments (it builds its
own isolated workdir and prints it; keep the dir for inspection).
Any step requiring author-local state (paths, caches, profiles, env
beyond `LAKRA_DB`) is a demo defect: stop and file a Slice-42
proposal per the rule below — do not work around it.

## Defect hatch (binding)

If the demo exposes a genuine behavioral defect: **STOP. Do not fix
it here.** File a Slice-42 proposal (symptom, exact demo step +
command, minimal repro, affected component, scoped fix sketch) and
mark Slice-41 BLOCKED pending its approval. Only trivial
documentation typos/path wording inside the new `docs/demo-v1.md`
itself may be fixed inline. Anything touching behavior, tests,
fixtures, or prior docs takes the Slice-42 route.

## Closeout records

- `specs/001-lakra-v1/tasks.md`: one closeout line (final count,
  demo PASS date, pointer here).
- `specs/001-lakra-v1/checklists/requirements.md`: V1 sign-off
  section (marks only; original lines untouched).
- `docs/VERSION`: `0.1.0`, green baseline count, demo PASS date, and
  the verified revision (or an honest statement that no VCS is
  present and the tree was verified by clean-state demo run).
