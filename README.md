# Lakra 2.0 — Personal Browser Automation Agent

Lakra is a local-first, single-user browser automation agent. It takes a
natural-language goal, shapes it into a deterministic plan, enforces
policy (L0–L4), and executes browser actions with per-action verification.
It never equates "click happened" with "task succeeded."

## Architecture

```
User → Analyzer → Planner → Policy → Scheduler → Router → Execution → Verify → Result
```

**Control plane** (decides, never touches the machine): task registry,
analyzer, planner, policy engine, scheduler, budgets, approvals, audit,
sessions, locks.

**Execution plane** (acts, never decides): browser observer/actions/
verification/recovery, filesystem, terminal — all behind a `ToolRouter`
with runtime capability checks.

### Key modules

| Module | Purpose |
|--------|---------|
| `src/lakra/control/` | Analyzer, planner, policy, scheduler, approvals, audit, shaping, chains, loops |
| `src/lakra/execution/` | Browser observer/actions/verification, filesystem, terminal, router |
| `src/lakra/models/` | Model provider interface (deterministic default) |
| `src/lakra/resources/` | Resource monitor (RAM/CPU/GPU) |
| `scripts/lakra_do.py` | CLI: `--road`, `--goal`, `--chain-file`, `--resume`, `--list`, `--status` |
| `scripts/demo_v1.py` | V1 acceptance harness (30/30 checks) |

## Roads

| Road | Description | NL via `--goal` |
|------|-------------|-----------------|
| **observe** | Navigate + snapshot + verify text | Yes |
| **follow** | Ground link → click → verify arrival | Yes |
| **search** | Ground query box → type → L3 submit → verify results | Yes |
| **form** | Ground fields → fill → L3 submit | No (explicit flags) |
| **loop** | Bounded cursor-unrolled link matching | No (explicit flags) |
| **chain** | 2–4 single-road legs under one task | No (`--chain-file`) |

## Policy (L0–L4)

- **L0**: read-only (navigate, snapshot)
- **L1**: reversible (click, type, scroll)
- **L2**: bounded-mutation (check, select)
- **L3**: consequential — **requires human approval** (submit, send, publish)
- **L4**: never executes (auth bypass, CAPTCHA defeat, security bypass)

## Quick start

```powershell
# Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install playwright psutil pytest
playwright install chromium

# Run
.\.venv\Scripts\python.exe scripts\lakra_do.py --road observe --url "file:///page.html" --text "Content" --expect "Content" --yes
.\.venv\Scripts\python.exe scripts\lakra_do.py --goal "Show me the records page at file:///page.html" --yes
.\.venv\Scripts\python.exe scripts\lakra_do.py --goal "Web search for cats at file:///search.html and confirm results" --yes

# Demo
.\.venv\Scripts\python.exe scripts\demo_v1.py

# Tests
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Safety

- Deterministic, refusal-first: ambiguity refuses, never guesses
- No model calls in the default path
- L3 gates park for human approval (cross-process via `approve.py`)
- Credential-laden goals refuse
- Browser profile isolation (user profile untouched)
- Audit log is append-only and replayable

## Status

- 543/543 tests passing
- V1 demo 30/30 PASS
- Slice-46 (NL observe/follow) and Slice-47 (NL search) validated
