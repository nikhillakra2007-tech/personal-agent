<div align="center">

# 🤖 Lakra 2.0 — Personal Browser Automation Agent

**Local-first, deterministic browser automation engineered with strict dual-plane isolation and L0–L4 policy enforcement.**

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Tests Passing](https://img.shields.io/badge/Tests-546%20Passing-success?style=for-the-badge&logo=pytest&logoColor=white)](https://github.com/nikhillakra2007-tech/personal-agent)
[![V1 Demo](https://img.shields.io/badge/V1%20Demo-30%2F30%20PASS-brightgreen?style=for-the-badge)](docs/reports/demo-v1.md)
[![Safety Model](https://img.shields.io/badge/Safety-L0--L4%20Refusal--First-blueviolet?style=for-the-badge)](docs/contracts/policy-schema.md)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

[Architecture](#-system-architecture) •
[Execution Roads](#-execution-roads) •
[Safety & Policy](#%EF%B8%8F-policy--safety-model) •
[Quickstart](#-quickstart) •
[Directory Structure](#-repository-structure) •
[Verification](#-testing--validation)

</div>

---

## 💡 Overview

**Lakra 2.0** is an autonomous, local-first browser automation agent built for reliability, security, and human oversight. Unlike naive automation scripts or unconstrained LLM web agents, Lakra adheres to a core engineering principle: **it never equates *"click happened"* with *"task succeeded"***.

Every action undergoes multi-phase ground verification, runtime resource checking, and explicit safety gating before and after execution.

### Key Highlights
- 🛡️ **Refusal-First Determinism**: Ambiguous or ungrounded instructions result in explicit refusal—never hallucinated actions.
- 🚦 **L0–L4 Policy Engine**: Enforces strict tiers ranging from read-only inspection (L0) to human-in-the-loop approval gates (L3) and hard blocks (L4).
- 🔄 **Dual-Plane Separation**: The **Control Plane** solely reasons and decides; the **Execution Plane** solely acts under strict capability tokens.
- 🔐 **Cross-Process Approvals**: Consequential actions halt in pending state with an atomic approval token until settled via an independent CLI process (`approve.py`).
- 📜 **Append-Only Auditing**: Every event, plan, transition, and verdict is preserved in a tamper-evident audit ledger.

---

## 🏛️ System Architecture

Lakra strictly isolates reasoning from side-effects across two architectural planes:

```mermaid
flowchart TD
    User([User Goal / CLI]) --> Analyzer[Goal Analyzer & Grounding]
    Analyzer --> Planner[Deterministic Planner]
    Planner --> Policy{Policy Engine L0-L4}
    
    subgraph ControlPlane [Control Plane — Decides, Never Touches Machine]
        Policy -->|L0-L2 Allowed| Scheduler[Task Scheduler & Budgets]
        Policy -->|L3 Consequential| ApprovalGate[Approval Gate & CAS Token]
        ApprovalGate -.->|approve.py| Scheduler
        Policy -->|L4 Prohibited| Refusal[Hard Refusal]
        Scheduler --> TaskReg[(Task Registry & Audit Log)]
    end

    subgraph ExecutionPlane [Execution Plane — Acts, Never Decides]
        Scheduler --> Router[Tool Router & Capability Check]
        Router --> Actions[Browser Actions / DOM Interactor]
        Actions --> Verifier{Post-Action Verifier}
        Verifier -->|Verified Mutation| Success[Return Result]
        Verifier -->|Failed Verification| Recovery[Recovery & Replan]
    end
```

| Plane | Responsibilities | Primary Modules |
|---|---|---|
| **Control Plane** | Task registry, goal shaping, planning, policy evaluation, concurrency locks, approval settlements, audit ledger | `src/lakra/control/` |
| **Execution Plane** | Headless/headed browser controller, DOM observers, actions, mutation verification, filesystem/terminal wrappers | `src/lakra/execution/` |
| **Governance** | Real-time RAM/CPU/GPU sampling, token ledgers, failure backoff | `src/lakra/resources/` |

---

## 🛣️ Execution Roads

Lakra organizes browser workflows into specialized, robust execution pathways:

| Road | Description | Natural Language via `--goal` |
|---|---|:---:|
| **`observe`** | Navigates to target, takes a snapshot, and verifies content presence | ✅ Yes |
| **`follow`** | Grounds target link/button, performs navigation click, and verifies destination | ✅ Yes |
| **`search`** | Locates query input, types search term, triggers submit, and verifies result listings | ✅ Yes |
| **`form`** | Resolves form fields, populates inputs, and gates submission behind human approval | ⚙️ Explicit Flags |
| **`loop`** | Bounded iteration across repeating DOM structures (e.g. paginated links) | ⚙️ Explicit Flags |
| **`chain`** | Sequential multi-leg execution composing 2–4 roads into a coordinated task | ⚙️ `--chain-file` |

---

## 🛡️ Policy & Safety Model

Every plan step is scored against the **L0–L4 Safety Hierarchy**:

```
[L0: Read-Only]   → Allowed automatically (DOM snapshots, text reading, navigation)
[L1: Reversible]  → Allowed (typing, scrolling, standard element clicking)
[L2: Bounded]     → Allowed (checking boxes, selecting dropdowns)
[L3: Consequential]→ GATED: Halts execution, yields approval token, awaits human verification
[L4: Prohibited]  → REFUSED: Hard-blocked immediately (auth bypass, credential capture, CAPTCHA defeat)
```

### Human-in-the-Loop Approval Workflow
When an action reaches **L3** (e.g., submitting payment, ordering, publishing, deleting):
1. The agent enters `WAITING_APPROVAL` status and records an atomic approval token.
2. The user inspects the pending action via `scripts/approve.py --list`.
3. The user approves (`approve.py <token>`) or rejects (`approve.py --deny <token>`).
4. The executing task loop resumes immediately upon settlement.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.11 or 3.12
- Chromium browser (installed via Playwright)

### 1. Installation
```powershell
# Clone the repository
git clone https://github.com/nikhillakra2007-tech/personal-agent.git
cd personal-agent

# Set up virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install package dependencies
pip install -e .
pip install pytest playwright psutil

# Install browser runtime
playwright install chromium
```

### 2. Basic Execution Examples

#### Natural Language Goal (Observe)
```powershell
python scripts/lakra_do.py --goal "Show me the records page at file:///c:/data/index.html" --yes
```

#### Natural Language Search & Verification
```powershell
python scripts/lakra_do.py --goal "Search for robotics at file:///c:/data/search.html and confirm results" --yes
```

#### Explicit Road Execution
```powershell
python scripts/lakra_do.py --road observe --url "https://example.com" --expect "Example Domain" --yes
```

### 3. Human Approval Process
In a separate terminal while an L3 task is waiting:
```powershell
# List pending approval requests
python scripts/approve.py --list

# Approve the pending action token
python scripts/approve.py <APPROVAL_ID>
```

---

## 📁 Repository Structure

The project follows a systematic, modular organization where non-code directories adhere to a clean 2–3 file grouping:

```
personal-agent/
├── pyproject.toml              # Build & dependency metadata
├── README.md                   # Repository documentation & guide
├── PROFILE.md                  # GitHub Profile template (nikhillakra2007-tech)
│
├── docs/                       # Specifications & System Documentation (2 files)
│   ├── VERSION                 # Current release version
│   ├── capability-matrix.md    # Capability & feature matrix
│   ├── architecture/           # Architecture Blueprints (2 subfolders)
│   │   ├── system/             # Core architecture (3 files: overview, browser, testing)
│   │   └── governance/         # Policy & thresholds (3 files: consistency, policy, resources)
│   ├── contracts/              # Schema & Interface contracts (3 files)
│   ├── reports/                # V1 demo & real-world validation reports (3 files)
│   └── research/               # Agent research & competitive analyses (2 files)
│
├── scripts/                    # Primary Executable Tools (3 files)
│   ├── lakra_do.py             # Main CLI execution entry point
│   ├── approve.py              # Out-of-band approval settlement CLI
│   ├── demo_v1.py              # Full V1 acceptance test harness (30/30 checks)
│   ├── tools/                  # Auxiliary CLI utilities (2 files)
│   └── replays/                # Historical development slice verification harnesses
│
├── specs/                      # Formal Feature Specifications
│   └── 001-lakra-v1/           # V1 formal spec, plan, and task checklists (3 files)
│
├── src/lakra/                  # Core Python Package
│   ├── control/                # Analyzer, Planner, Policy Engine, Scheduler, Store
│   ├── execution/              # Browser Controller, Observers, Actions, Tool Router
│   ├── models/                 # Model provider abstractions & deterministic proposal
│   └── resources/              # RAM, CPU, and GPU resource monitors
│
└── tests/                      # Full Pytest Test Suite (546 tests)
    └── fixtures/               # HTML fixture environments for browser test cases
```

---

## 🧪 Testing & Validation

Lakra maintains comprehensive test coverage verifying every state machine transition, guard, and DOM action:

```powershell
# Run the complete test suite (546 tests)
pytest tests -q

# Run CLI and Acceptance integration tests
pytest tests/test_cli_do.py tests/test_cli_visibility.py tests/test_acceptance.py -q

# Run the full V1 end-to-end acceptance demo (30/30 PASS)
python scripts/demo_v1.py
```

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  <sub>Engineered by <a href="https://github.com/nikhillakra2007-tech">Nikhil Lakra</a>. Built for safe, deterministic agentic autonomy.</sub>
</div>
