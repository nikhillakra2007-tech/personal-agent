# Policy, Scheduler, Resources, MCP (proposed)

## 1. Policy Engine

Pure function: `evaluate(action, task) → ALLOW | ASK | BLOCK`, checked between
plan and execution AND re-checked per concrete action (intent ≠ effect).

| Level | Meaning | Examples | Verdict |
|---|---|---|---|
| L0 observe | Read-only | read page, snapshot, screenshot, inspect repo, read approved files | ALLOW (logged) |
| L1 safe/reversible | Ordinary reversible | navigate, search, click, scroll, type ordinary text, tabs, ordinary downloads, tests, course navigation | ALLOW (logged) |
| L2 controlled/sandboxed | Bounded mutation | create/edit/move/rename/delete inside approved workspace, dev commands, builds, allowed git ops | ALLOW iff inside task bounds, else ASK |
| L3 approval-required | Consequential/irreversible | send mail/message, submit assignment/exam, publish, purchase/pay, important push, settings change, large delete, upload private data | ASK → pause, request decision, resume/cancel |
| L4 blocked | Never | auth bypass, CAPTCHA defeat, security-control bypass, unauthorized access, safeguard evasion, out-of-boundary destruction | BLOCK + log + stop-philosophy (report, don't work around) |

Full JSON schema: `docs/contracts/policy-schema.md`.

## 2. Scheduler

Priority queue over tasks; admission per task requires: resource headroom
(§3), needed locks free (task checkout + computer-control lock), policy-clean
next action, budget remaining. Transitions: queued→running→paused→running,
→completed/failed/cancelled; pause/resume/cancel anytime (user or policy).
Physical mouse/keyboard is a mutex: at most one holder; others wait or use
background executors. Concurrency example: browser task + terminal task run
together; two foreground tasks serialize.

## 3. Resource Monitor

Polls (no hard-coded limits until calibrated on this host: 16 GB DDR5,
RTX 3050 6 GB, i5-13450HX 10C/16T, ~65 GB free on C:):

- RAM available/total, per-process RSS of Lakra workers
- CPU % + load; GPU util % + used/free VRAM (`nvidia-smi`/NVML); GPU temp when exposed
- Model memory (per loaded Ollama model)

Policy: below reserve → refuse heavy starts; pressure → throttle
(concurrency 1, longer Observe waits) → pause low-priority → unload idle
models. Calibration step (slice-06): measure idle OS+browser footprint first,
then set reserves with reasoning recorded.

## 4. MCP

One MCP client per approved server, lifespan bound to the task; tool calls go
through POLICY CHECK with the task's server+tool allowlist. Secrets via refs;
values redacted in logs. Core invariants (boundary, validation, containment,
redaction, audit) cannot be disabled by task config — Paperclip's
open-by-default/opt-in-restriction model. No MCP servers wired in Phase 1;
interface defined in slice-07.
