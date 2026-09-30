# Lakra 2.0 — V1 Specification (001-lakra-v1)

**Phase:** Research + specification only. No implementation authorized.
**Constitution:** `../../.specify/memory/constitution.md` (wins on conflict).

## 1. Goal

A personal local-first computer-use agent whose V1 core is **autonomous browser
automation**: user states a goal in natural language; Lakra loops
observe → understand → plan → policy-check → act → observe → verify until it
continues, retries, replans, asks, or stops. Never equate "click happened" with
"task succeeded."

## 2. Users & Scope

Single user, single Windows host (16 GB RAM, RTX 3050 6 GB, NVMe SSD, shared
with the user). In scope V1: browser agent, computer control (fore-/background),
task scheduler, policy engine L0–L4, tool router, resource monitor, model
provider abstraction, scoped MCP, audit log. Out of scope: multi-user,
companies/orgs, cloud tenancy, marketplaces, enterprise RBAC, revenue
accounting, knowledge base.

## 3. Functional Requirements

- **FR-1 Task intake:** natural-language goal → task contract (id, goal,
  status, priority, permission level, allowed tools/domains/paths, submission
  policy, budget, timestamps, action history, verification + error state).
- **FR-2 Pipeline:** TASK ANALYZER → PLANNER → POLICY ENGINE → SCHEDULER →
  TOOL ROUTER → EXECUTION → VERIFIER → RESULT, with replan/retry/ask/stop
  transitions. (Change from the starting sketch: explicit VERIFY→decision
  fan-out and a TOOL ROUTER backed by a mutable registry — Paperclip lessons.)
- **FR-3 Browser loop:** per-action observe→verify; structured (DOM/a11y)
  first, screenshot/visual fallback second; sessions, tabs, waits, popups,
  downloads, error detection, retry, recovery.
- **FR-4 Computer control:** exclusive computer-control lock for foreground
  mouse/keyboard (visible indicator, always released); background prefers APIs/
  isolated sessions/MCP/terminal/filesystem; user preempts everything; pause/
  stop/take-control always available.
- **FR-5 Policy:** L0 observe … L4 blocked; deterministic ALLOW/ASK/BLOCK
  between plan and execution; L3 pauses for user approval; L4 never executes
  (auth bypass, CAPTCHA defeat, security-control bypass, unauthorized access,
  destructive out-of-boundary acts).
- **FR-6 Scheduler:** concurrent tasks where resources allow; resource +
  lock-availability checks; priorities; pause/resume/cancel.
- **FR-7 Resources:** monitor RAM/VRAM/CPU/GPU (+temp when available); gate
  heavy starts, throttle/pause low priority, unload idle models. Thresholds
  set only after measurement (Sec. 6).
- **FR-8 Models:** `ModelProvider` (local Ollama-compatible / cloud keyed /
  future); route deterministic-code → light local → larger local → cloud.
- **FR-9 MCP:** scoped servers per task allowlist; secret refs + redaction;
  open-by-default tools with non-disableable core invariants.
- **FR-10 Audit:** append-only local log of the full event taxonomy (task,
  plan, policy, tool, browser/mouse/keyboard, screenshot, verification,
  retry/replan, pause/stop/completion/failure).

## 4. Non-Functional Requirements

- **NFR-1 Safety:** scoped domains/paths/tools; sandboxed workspaces; no silent
  bypasses; secrets never in prompts/logs.
- **NFR-2 Responsiveness:** user machine stays usable; Lakra yields under
  contention.
- **NFR-3 Transparency:** user can answer "what is it doing, does it need me,
  what do I do" at any moment.
- **NFR-4 Learnability:** every slice ships what/why/how + tests + manual
  verification notes.
- **NFR-5 Locality:** full value without network model calls; cloud is
  escalation, not requirement.

## 5. Acceptance Criteria (V1)

1. "Search the official docs for X" completes without per-click direction.
2. Multi-step portal/course workflow runs safe portions, stops at
   assessment/submission for approval (L3 gate fires, resumes on decision).
3. Physical input lock is exclusive, indicated, released; user takeover instant.
4. Overspend (tokens) or resource pressure pauses work automatically.
5. Every run is replayable from the audit log.

## 6. Decisions (user, 2026-09-26)

1. Browser profile: **isolated Playwright profile** (user browsing untouched).
2. Filesystem bounds: **dedicated `~/LakraWorkspace` root**; all else needs approval.
3. Cloud fallback: **local-only V1** — provider interface designed, unwired, no keys.
4. Course/portal approvals: per-item approval retained as default (standing scopes deferred).

## 7. First Slice (authorized for planning only — NOT to build)

`slice-01`: local task contract (dataclass) + policy engine (pure function
L0–L4 → ALLOW/ASK/BLOCK) + in-memory registry + audit event writer + scripted
replay test. No browser, no models, no UI. Details in `tasks.md`.
