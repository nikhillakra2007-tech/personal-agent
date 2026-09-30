# Paperclip — Reference Architecture Analysis

**Source:** https://github.com/paperclipai/paperclip (README, `doc/GOAL.md`,
`doc/PRODUCT.md`, `doc/SPEC-implementation.md` (2026-04-28), `AGENTS.md`,
`packages/adapters/AUTHORING.md`; repo snapshot Sept 2026).
**Purpose:** Understand, not copy. Lakra-relevance verdicts for each section;
final borrow/adapt/reject decisions live in `paperclip-to-lakra.md`.

## 1. What Paperclip Is

Open-source (MIT) **orchestration for teams of AI agents** — "if OpenClaw is an
*employee*, Paperclip is the *company*." A Node.js server + React UI + Postgres
(embedded PGlite in dev) that manages agent companies: org charts, goals,
tasks, heartbeats, budgets, governance, audit. Bring-your-own agents: Claude
Code, Codex, Cursor, Gemini, OpenCode, Pi, bash/CLI, HTTP/webhook bots,
OpenClaw gateways, external adapter plugins. Explicitly **not** a chatbot, agent
framework, workflow builder, prompt manager, single-agent tool, or code-review
tool.

## 2. What Problem It Solves

Coordination collapse with many autonomous agents: 20 open agent terminals with
no tracking, lost context on reboot, reinvented task management per agent,
runaway token spend, recurring jobs needing manual kick-offs, no audit of who
did what. Answer: ticket-based tasks with threaded conversations, goal
ancestry for context, atomic checkout (no double-work), scheduled heartbeats,
budget hard-stops, approvals, immutable activity log.

## 3. What Paperclip Calls the Control Plane

The **central nervous system** (`server/` + `ui/` + `packages/db` +
`packages/shared`): identity & access, org chart & agents, work & tasks,
heartbeat execution, workspaces & runtime, governance & approvals, budget &
costs, routines & schedules, plugins, secrets & storage, activity & events,
company portability. One deployment can run many companies with full data
isolation (`company_id` on every business record). Its mantra: **"control
plane, not execution plane — Paperclip orchestrates; agents run wherever they
run and phone home."**

## 4. Execution Services / Adapters

Adapters are the boundary defining **how a heartbeat is invoked, observed, and
cancelled**. Built-ins: `process`, `http`, local CLI/session adapters
(`claude_local`, `codex_local`, `gemini_local`, `opencode_local`, `pi_local`,
`cursor…`, `hermes…`), `openclaw_gateway`; plus **external adapter plugins**
loaded without forking core. Registry is mutable on both server
(`registerServerAdapter`, `requireServerAdapter`) and UI, with runtime
validation in routes (`assertKnownAdapterType`) — i.e. inputs accept any
string, the live registry is the source of truth. `AUTHORING.md` pins a
**no-remote-git contract**: the local execution-workspace cwd is the only
cross-run persistence boundary; adapters sync state through it (round-trip
helpers for SSH/sandbox), never `git push` from runtime (CI check
`check-no-git-push.mjs` enforces it).

## 5. How Its Agents Work

Agents are **employees**: adapter type + config (identity/behavior, e.g. a
heartbeat prompt), role/reporting (`reports_to` strict tree, no multi-manager),
capabilities paragraph for discovery, `runtime_config` (scheduling/debug),
`context_mode` (`thin|fat`), monthly budget, permissions, status. Minimum
adapter contract is just "be callable." Agent API keys (hashed, shown once,
company-scoped) give agents task read/write, delegation, heartbeat + cost
reporting — never approval bypass, budget mutation, or key management.

## 6. Heartbeat Lifecycle

DB-backed wakeup queue with coalescing → budget check → workspace resolution
(git worktree / execution workspace) → secret injection (refs, redacted) →
skill loading → adapter invocation → structured logs + cost events + session
state + audit trail. Scheduler/worker lives in-process (trigger checks, stuck
run detection, budget checks; no queue infra in V1). Recovery handles orphaned
runs; liveness rules (`doc/execution-semantics.md`) decide whether
todo/in_progress/in_review/blocked issues have a live, waiting, or recovery
path; exhausted repair escalates to a board-owned recovery action.

## 7. Tasks / Issues

Core entity `issues`: `company/project/goal/parent` links, statuses
`backlog|todo|in_progress|in_review|done|blocked|cancelled`, priorities,
`review_policy`, **single assignee**, **atomic checkout + execution locks**
(`checkout_run_id`, `execution_run_id`, `execution_locked_at`) so two agents
never work the same task, first-class blockers, comments, documents
(`plan|design|notes…` keys, revisioned, lockable), attachments (allowlisted
types, inline-safe vs forced-download serving), work products, labels, inbox
state, `work_mode` (`standard|ask|planning`), origin/run provenance.

## 8. Goals vs Tasks

Goals are the **why-chain** (`company|team|agent|task` levels, ≥1 root company
goal); tasks are the **work**. Every task must trace to the company goal via
`goal_id`, `parent_id`, or project-goal linkage. Agents always see ancestry:
"I research X *because* → parent … *because* → company goal." Deep-planning
mode produces revisioned plans with approvals before implementation.

## 9. Agent Hierarchy

Strict tree via `reports_to` (same company, acyclic, root = CEO). Delegation
flows up/down the org chart on heartbeats; managers supervise, reprioritize,
assign. Assignment creates execution authority; board can force-reassign.
Scoped grants (`tasks:assign_scope`: project / target-agent / managed-subtree
constraints) bound delegation without enterprise RBAC.

## 10. Governance and Approvals

`approvals` table (`hire_agent|approve_ceo_strategy|budget_override_required|
request_board_approval`), states `pending|revision_requested|approved|rejected|
cancelled`. Review policies (`anyone|not_creator|human_only`) gate
`in_review → done`; issue-thread interactions carry resolver policies with
server-derived effective audience. Agent lifecycle (`idle|running|error|paused|
pending_approval|terminated`) — terminate is board-only, irreversible. Config
changes revisioned with rollback. Watchdogs are scoped, non-principal
capacities: allowed restorative mutations only, atomic batches ≤3 validated
against a stop fingerprint, bounded retries then human escalation.

## 11. Budgets / Cost Controls

`cost_events` per (company, agent, issue, project, goal, provider, model):
tokens + `cost_cents`, monthly UTC window. Scoped budget policies with warning
thresholds + **hard stops**: overspend auto-pauses agents, cancels queued work.
Rollups are aggregation, never hand-edited. Board answers "what did it cost"
per every dimension.

## 12. Audit Logging

`activity_log` (company, actor `agent|user|system`, action, entity, details) on
**every mutation**; heartbeat state changes, cost events, approvals, comments,
work products recorded as durable activity; `heartbeat_run_events` run log.
Secrets redacted; sensitive values never in prompts/logs/activity payloads.

## 13. MCP Governance

**MCP Tool Gateway**: governed tool access — capability-gated host services,
tool exposure through plugins, per-agent access policy, secret-aware env
binding (secret refs, redaction), log redaction, centralized policy evaluation
with simulation. Skills are **open by default, restrictions opt-in**; core owns
invariants (boundary enforcement, validation, path containment, redaction,
logging) that policy cannot disable.

## 14. Agent Adapters (Authoring Model)

Mutable registries + runtime validation (§4); user-facing guide
(`docs/adapters/creating-an-adapter.md`) + in-repo invariants; no-remote-git
contract; workspace round-trip helpers; static CI checks. Philosophy: thin
core, rich edges — extend via plugins, not forks.

## 15. Pause / Terminate / Control

Company `active|paused|archived`; agent pause (board-only, cancel flow from
running), resume (only grant-gated agent action), terminate (board-only,
irreversible); effective task/ancestor pause swaps the composer for an amber
Resume takeover (drafts survive); recovery actions preserve ownership —
reassignment needs an explicit board decision or policy-defined serious failure.

## 16. Relevant to Lakra

Control/execution-plane split; mutable adapter (tool) registry with runtime
validation; atomic checkout + execution locks; single-assignee ownership;
goal-anchored tasks; heartbeat-style wake/schedule loop; budget hard-stops;
approval gates + resolver policies; pause/resume/terminate semantics;
immutable activity log; secret-ref + redaction discipline; open-by-default
tools with opt-in restrictions; thin core / rich edges; workspace-as-
persistence-boundary; bounded recovery with escalation.

## 17. Unnecessary for Lakra

Multi-company tenancy + `company_id` scoping; org charts with hired agent
employees; CEO/strategy approvals; hiring flows; revenue accounting; skill
studios/stores; company portability (export/import); multi-board governance;
iMessage/Photon channels; agent chat; marketplace/ClipHub; SSO/GRC; per-agent
token salaries as HR metaphor.

## 18. Should Be Adapted

Heartbeat → Lakra's **task-tick / browser-agent loop** (single-user, local,
resource-aware). Issues model → slimmed **task contract** (goal, ownership,
locks, verification state — no company/project/goal FK web). Budgets → **two
budgets**: token-cost + machine-resource. Approvals → **policy levels L0–L4**
with ASK gates. Adapters → **tool router + execution plane** (browser,
computer, filesystem, terminal, git, MCP). Audit → **local event log**.
MCP gateway → **scoped MCP servers with allowlists**. Recovery → bounded
retry/replan with user escalation.

## 19. Should NOT Be Copied

Company/org-chart management; hiring/termination of agent employees; multi-
tenancy isolation; Postgres + server + React stack for V1 (Lakra is a local
Python engine); cron routines engine; plugin marketplace; enterprise RBAC;
anything that makes Lakra resemble an AI-company manager instead of a personal
computer-use agent.

## 20. Architectural Lessons for Lakra

1. **Separate deciding from doing** (control vs execution) — testable, least-
   privilege, portable.
2. **One worker per task, enforced atomically** — Lakra's computer-control lock
   and task checkout need the same atomicity.
3. **Budgets are actuators, not dashboards** — hard-stops that pause work.
4. **Every mutation is an audit event** — transparency is a schema property.
5. **Registries, not hard-coded lists** — tools/adapters resolve at runtime.
6. **No silent control bypasses** — policy denials stay distinct, explicit, and
   logged.
7. **Persistence boundaries explicit** — Lakra: task registry + browser session
   state, not git worktrees.
8. **Thin core, rich edges** — browser/computer/MCP behind a stable tool
   interface, not in the scheduler.
