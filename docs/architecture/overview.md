# Architecture Overview (proposed)

## 1. Final Pipeline

```
USER → TASK ANALYZER → PLANNER → POLICY ENGINE → TASK SCHEDULER
  → TOOL ROUTER → EXECUTION (browser | computer | fs | terminal | git | MCP)
  → VERIFIER → { CONTINUE | RETRY | REPLAN | ASK | STOP } → RESULT
```

**Two changes vs the starting sketch, both from Paperclip — made explicitly:**

1. **VERIFY fans out** instead of flowing straight to RESULT. Paperclip's
   liveness/recovery semantics show terminal states must be *decided*, not
   assumed; browser work especially needs retry/replan/ask/stop as first-class
   transitions (§8 of the brief: click ≠ success).
2. **TOOL ROUTER is registry-backed** (mutable, runtime-validated) instead of
   a fixed tool list. Paperclip's mutable adapter registries
   (`registerServerAdapter` + `assertKnownAdapterType`) prove hard-coded tool
   lists rot; Lakra tools register capabilities and the router checks them at
   call time. **Built slice-03** (`src/lakra/execution/`): `ToolRegistry`
   (opaque names, unknown rejected) + `ToolRouter` choke point (resolve →
   policy → budget/admission → execute → history + audit) + sandboxed
   filesystem/terminal executors. Executor trust rule: executors validate
   *shape* (sandbox containment, argv allowlist), policy judges *meaning*.

## 2. Control / Execution Split (adopted, reshaped)

One local Python process, two logical planes. Control decides (registry,
analyzer, planner, policy, scheduler, budgets, approvals, audit, sessions,
locks); execution acts (browser, mouse/keyboard, screenshots, terminal, fs,
git, MCP). Control never touches the machine; execution never approves its own
actions. (Paperclip runs this as server vs adapters; Lakra keeps it in-process
— no Postgres, no HTTP control plane in V1 — because the user, machine, and
agent are all the same box.)

## 3. Folder Structure (proposed; created only after approval)

```
Lakra-2.0/
  .specify/memory/constitution.md
  specs/001-lakra-v1/{spec,plan,tasks,checklists}.md
  docs/{research,architecture,contracts}/
  src/lakra/
    control/{tasks,planner,policy,scheduler,budgets,approvals,audit,locks}.py
    execution/{router,registry,browser,computer,filesystem,terminal,git,mcp}.py
    execution/browser/{controller,observer,sessions,actions,verification,recovery}.py
    models/{base,local,cloud}.py
    resources/monitor.py
  tests/  (from slice-01: test_policy.py, test_tasks.py, test_audit.py …)
  scripts/ (replay/verify helpers)
  .env.example (GROQ/cloud keys; `.env` git-ignored)
```

## 4. Dependency Proposal (minimal; nothing installed in Phase 1)

| Need | Proposal | Why / note |
|---|---|---|
| Browser automation | Playwright (Python), isolated automation profile (Chromium) | Structured (DOM/a11y) first; screenshot built-in; per 2026-09-26 decision the user profile stays untouched (installed Chrome 153 / Edge 154 available only as fallback channels) |
| Visual fallback | Playwright screenshots + (later) lightweight local VLM via Ollama | No model downloads in Phase 1 |
| Local models | Ollama + OpenAI-compatible client | `LocalProvider`; install when slice-06 starts |
| Config/secrets | `python-dotenv` | `.env` pattern you already use |
| Tests | `pytest` (from slice-01) | Replaces ad-hoc snippets |
| Data | stdlib `sqlite3`, `dataclasses` | No ORM/DB server in V1 |

NOT adopted: Postgres/server/React (Paperclip's stack solves multi-user
companies, not a local agent); pnpm/Node services; any LLM SDK in control-plane
imports; large model downloads now.

## 5. Scheduler / Resource / MCP (summaries; detail docs linked)

- **Scheduler** (`docs/architecture/policy-scheduler-resources.md` §2):
  priority queue, resource + lock admission, pause/resume/cancel, per-task
  ownership; physical input is the mutex.
- **Resources** (same doc §3): poll RAM/CPU (psutil-class), GPU (nVidia NVML /
  `nvidia-smi`), gate/throttle/pause/unload; thresholds from measurement, e.g.
  reserve ~4 GB for OS/browser before heavy tasks — to be calibrated, not
  hard-coded now.
- **MCP** (same doc §4): per-task server allowlist, tool allowlist, secret
  refs, redacted logs, core invariants non-disableable.
