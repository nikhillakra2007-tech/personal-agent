# Paperclip → Lakra Mapping

Decision scale: **USE** (take as-is) · **ADAPT** (take the pattern, reshape for
personal computer-use) · **DO NOT USE** (leave out of Lakra V1).

| Paperclip concept | Paperclip responsibility | Lakra requirement | Verdict | Reason |
|---|---|---|---|---|
| Control Plane | Companies, org chart, tasks, heartbeats, budgets, approvals, audit | Task registry, scheduler, policy, budgets, approvals, logs, sessions | **ADAPT** | Keep decide-vs-do split; drop company scoping → single-user local store (SQLite + files) |
| Execution Plane | Adapters invoking external agents (CLI/HTTP/OpenClaw) | Browser, mouse/keyboard, screenshots, terminal, filesystem, git, MCP tools | **ADAPT** | Same boundary idea; Lakra's executors are computer-use tools, not employee agents |
| Agent Registry | Hire/manage agent employees (CEO…engineers) | No hired agents; one assistant acting per task | **DO NOT USE** | Lakra is not a company; a tool/executor registry replaces it |
| Agent Adapters | Mutable server+UI registry, runtime validation, authoring invariants | Tool Router + mutable tool registry, runtime capability checks | **ADAPT** | Registry-not-hardcoded-lists + validation-at-call-time transfer directly |
| Heartbeats | Scheduled wakeups: budget→workspace→secrets→skills→invoke→log | Task tick + browser-agent loop (observe→plan→check→act→verify) | **ADAPT** | Same lifecycle shape; local, single-task-focused, resource-gated instead of cron |
| Task/Issue System | Hierarchical issues, single assignee, atomic checkout, blockers, comments, docs | Slim task contract: goal, status, owner, locks, verification, history | **ADAPT** | Ownership + atomicity + traceability yes; company/project/goal FK web no |
| Goal Hierarchy | company→team→agent→task why-chain | Task goal + optional parent goal; agent always sees the why | **ADAPT** | Keep ancestry idea at task scope; no company mission tree |
| Agent Hierarchy | `reports_to` tree, delegation up/down | None — single assistant; task decomposition via subtasks | **DO NOT USE** | No employees, no managers, no hiring |
| Budgets | Monthly token budgets, warnings + hard-stop auto-pause | Token-cost budget AND machine-resource budget (RAM/VRAM/CPU/GPU) | **ADAPT** | Hard-stops-as-actuators yes; add resource dimension for 16 GB / 6 GB VRAM host |
| Governance | Board approvals, review policies, config revisions + rollback | Policy engine L0–L4 (ALLOW/ASK/BLOCK) + approval gates before L3 actions | **ADAPT** | Same gate semantics; approver is the user, not a board |
| Approval Gates | hire/strategy/budget/request-board-approval flows | ASK verdict pauses execution, requests user decision, resumes/cancels | **ADAPT** | Identical state machine, smaller surface |
| Audit Trail | Immutable `activity_log` on every mutation, redacted secrets | Local append-only event log, same event taxonomy adapted | **USE** | Transparency is non-negotiable; pattern applies unchanged |
| Activity Log | Human-readable operator feed | User-visible timeline of what Lakra did and why | **ADAPT** | Same feed idea; single-user, local |
| MCP Governance | Tool gateway, capability gates, secret refs, open-by-default + opt-in restrictions | Scoped MCP servers, per-task allowlists, secret refs, redaction | **ADAPT** | Open-by-default with non-disableable core invariants fits Lakra exactly |
| Execution Locks | Atomic checkout (`checkout_run_id`), `execution_locked_at`, no double-work | Atomic task checkout + computer-control lock (one mouse/keyboard owner) | **ADAPT** | Same atomicity; Lakra's contended resource is the physical machine |
| Persistent Sessions | Agent session state across heartbeats/reboots | Browser sessions + task state surviving restarts | **ADAPT** | Same need; scope is browser profiles + task registry, not agent identities |
| Agent Status | idle/running/error/paused/terminated + transitions | Task status + executor ownership state | **ADAPT** | Keep explicit state machines; drop employment metaphor |
| Pause | Board pause, amber Resume takeover, drafts survive | User pause/stop/take-control anytime; preemption guaranteed | **ADAPT** | User outranks everything — stronger preemption than Paperclip's board |
| Resume | Grant-gated resume, ownership preserved | Resume restores checkout + session, never steals ownership silently | **ADAPT** | Ownership-preserving resume transfers directly |
| Terminate | Board-only, irreversible agent termination | User stop/cancel task; kill executor, release locks, log | **ADAPT** | Same irreversibility care, no employment framing |
| Recovery | Watchdogs: scoped, bounded retries, stop-fingerprint validation, human escalation | Bounded retry/replan per task, verification-gated, escalate to user | **ADAPT** | Fingerprint-guarded, bounded, escalating recovery is exactly right for browser loops |
| Runtime Abstraction | Workspaces (worktrees), envs, runtime services, secret injection | Browser profiles, sandboxed workspace dirs, env allowlists, tool sandboxes | **ADAPT** | Workspace-as-persistence-boundary + injected-scoped-context transfers |

**Net:** Lakra borrows Paperclip's *control discipline* (split planes, atomic
ownership, gates, budgets-as-actuators, audit, bounded recovery) and rejects
its *company metaphor* (tenancy, org charts, hiring, multi-agent management).
