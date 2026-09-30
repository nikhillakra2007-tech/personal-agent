# LAKRA 2.0 — Project Constitution

**Status:** Ratified for Phase 1 (research + specification).
**Scope:** `Personal-Agents/Lakra-2.0/` only. Nothing outside this root.
**Method:** Spec-Driven Development (constitution → specify → clarify → plan →
checklist → tasks → analyze → implement → converge). No implementation without
an approved spec + plan + task list.

## 1. What Lakra Is

Lakra 2.0 is a **personal, local-first computer-use agent** for one user on one
Windows machine. Its V1 core capability is **autonomous browser automation**:
the user states a natural-language goal, Lakra observes, plans, checks policy,
acts, verifies, and continues / retries / replans / asks / stops — without
needing per-click direction.

## 2. Non-Negotiable Principles

1. **Browser-first.** Every architectural decision must serve the browser agent
   loop (observe → understand → plan → policy-check → act → observe → verify).
   General agent infrastructure exists to support computer-use, never the
   reverse. "The click happened" is never accepted as "the task succeeded";
   resulting state must be verified.
2. **Local-first, vendor-neutral.** No hard dependency on any single LLM vendor.
   All model access goes through a `ModelProvider` abstraction
   (local / cloud / future). Deterministic code is preferred over model calls
   wherever a rule suffices.
3. **Policy sits between planning and execution.** Every proposed action gets a
   deterministic, explicit verdict: `ALLOW`, `ASK`, or `BLOCK`, across levels
   L0 (observe) → L4 (blocked). Security controls (scoped filesystem, scoped
   domains, approved tools, task contracts, approval gates, execution locks,
   computer-control lock) are never silently bypassed.
4. **One owner for the physical machine.** Only one task at a time may own the
   physical mouse/keyboard (computer-control lock, visibly indicated, always
   released). User activity always preempts. Background work prefers APIs,
   isolated browser sessions, MCP, filesystem, and terminal tools.
5. **Transparent by default.** Every meaningful event is audit-logged
   (task lifecycle, plan, policy checks, tool selection, browser/mouse/keyboard
   actions, screenshots, verification, retry/replan, pause/stop/completion).
6. **Resource-aware.** Host: Windows, 16 GB RAM, RTX 3050 6 GB VRAM, NVMe SSD —
   shared with the user, never fully Lakra's. The scheduler monitors RAM/VRAM/
   CPU/GPU and throttles, pauses, or unloads models instead of degrading the
   user's machine. No hard-coded thresholds without measured reasoning.
7. **Learn while building.** Before each implementation slice: what, why, how,
   module communication, files, data flow, security + resource implications,
   test strategy, manual verification. After: what changed, why this way, how
   it works, tests + results, verification, limitations, next step.
8. **Small slices, verified.** Explain → design → implement small slice → test
   → verify → teach → next slice. Never generate the system at once. Never
   continue on a broken slice.

## 3. Out of Scope for V1

Multi-user operation, company/org management, cloud multi-tenancy, marketplaces,
fine-grained enterprise RBAC, revenue accounting, knowledge-base subsystems.
If a Paperclip-inspired concept does not serve a single user's computer-use, it
stays out (see `docs/research/paperclip-to-lakra.md`).

## 4. Technical Baselines

- Language: Python 3.12 (local engine); Node.js 24 available for tooling.
- Browser automation: Playwright-equivalent structured control preferred;
  screenshot/visual fallback only when structured interaction is insufficient.
- Local models via Ollama-compatible `LocalProvider` (no large downloads in
  Phase 1); cloud via keyed `CloudProvider` (`.env`, never committed).
- Storage: local files + SQLite (task registry, audit log). No server, no
  Postgres, no background services in V1.
- Testing: no framework yet — `compileall` + focused Python snippets +
  script replays until pytest is adopted (see `docs/architecture/testing.md`).

## 5. Governance of This Document

Amendments require an explicit user decision and a version bump with rationale.
On any constitution ↔ spec ↔ plan ↔ tasks contradiction, this constitution
wins until amended.
