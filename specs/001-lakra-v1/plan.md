# Lakra 2.0 — V1 Plan (001-lakra-v1)

## 1. Architecture Decision (control vs execution)

**Adopt the split, reshape the contents** (Paperclip lesson 1). Single Python
process, two logical planes:

- **Control plane** (decides, never touches the machine): task registry,
  analyzer, planner, policy engine, scheduler, budgets (tokens + resources),
  approvals, activity/audit log, session state, tool registry, execution locks.
- **Execution plane** (acts, never decides): browser controller/observer/
  actions/verification/recovery, mouse/keyboard, screenshots, terminal,
  filesystem, git, MCP clients — all behind a `ToolRouter` + mutable registry
  with runtime capability checks.

Change from the starting sketch (§5 of the brief): the linear pipeline gains
(1) an explicit **VERIFY → continue/retry/replan/ask/stop fan-out**, and
(2) a **registry-backed TOOL ROUTER** instead of a hard-coded tool list.
Both changes come directly from the Paperclip study and are explained in
`docs/architecture/system/overview.md`.

## 2. Build Order (thin vertical slices)

1. **slice-01** — task contract + policy engine + registry + audit writer
   (pure Python, no I/O side effects). Proves the control core.
2. **slice-02** — scheduler + execution/computer locks + pause/resume/cancel,
   plus the `ModelProvider` interface (`models/base.py`: deterministic ↔ model
   routing contract, no SDKs, no downloads) and minimal ASK mechanics
   (approval record + CLI-prompt resume, `WAITING_APPROVAL` transitions).
   Rationale (fixed 2026-09-26): browser slices need a brain contract and an
   ask path before they arrive; the interface is cheap, the runtime is not.
3. **slice-03** — tool router + filesystem/terminal executors (sandboxed).
4. **slice-04** — browser observer (read-only: navigate, snapshot, screenshot).
5. **slice-05** — browser actions + verification loop + recovery. The slice-02
   ASK resume path is *required* here (L3 gates can fire on real pages), not
   deferred to slice-07.
6. **slice-06** — wire `LocalProvider` (Ollama install + OpenAI-compatible
   client) + resource monitor + budgets. No large model downloads before this
   slice.
7. **slice-07** — scoped MCP + polished approvals UX (mechanics already exist
   since slice-02).

Rule: no slice starts while the previous one is red.

## 3. Data & Storage

SQLite (tasks, audit, sessions, budgets) + workspace dirs + browser profiles.
Schemas: `docs/contracts/task-schema.md`, `docs/contracts/policy-schema.md`.
Secrets: local `.env` (git-ignored), refs — never values — in task contracts.

## 4. Dependency Direction

`execution → router → scheduler → policy → tasks`; `audit` is a sink everyone
may append to, nobody reads for control flow. Browser `controller` orchestrates
`observer/actions/verification/recovery/sessions`; nothing else imports browser
internals. `ModelProvider` is an interface defined in slice-02 and wired to a runtime in
slice-06; control plane never imports an SDK.

## 5. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Site structure drift breaks selectors | Structured-first + visual fallback + verification-gated retry |
| Runaway browser loops | Per-task step + token budgets with hard-stop; verification fan-out |
| Resource contention (16 GB / 6 GB VRAM) | Gate heavy starts; throttle/pause; unload idle models |
| Accidental irreversible action | L3 ASK gates; L4 blocklist; scoped paths/domains; dry-run defaults |
| User preemption failure | Lock watchdog: heartbeat + forced release on user input |

## 6. Test Strategy (summary)

`compileall` + focused snippet replays now; pytest from slice-01 (contract +
policy truth tables, lock atomicity, audit replay); scripted browser replays
from slice-04 ( seed `docs/architecture/system/testing.md`). Manual verification
checklist per slice in `tasks.md`.
