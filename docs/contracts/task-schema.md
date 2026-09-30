# Task Contract Schema (draft v0.1 — slice-01 target)

Adapted from Paperclip's `issues` model: kept ownership, atomicity,
traceability, verification; dropped company/project/goal FK web, hiring,
billing, workspaces-as-worktrees.

```python
@dataclass
class Task:
    task_id: str            # uuid4 hex
    goal: str               # natural-language objective (the "why")
    parent_id: str | None   # subtask ancestry (goal chain, Paperclip §8 pattern)
    status: Status          # QUEUED|RUNNING|PAUSED|WAITING_APPROVAL|COMPLETED|FAILED|CANCELLED
    priority: int           # 0 (lowest) .. 3 (critical)
    permission_level: int   # max policy level pre-authorized: 1 or 2 (3+ always ASKs)
    allowed_tools: list[str]
    allowed_domains: list[str]
    allowed_paths: list[str]     # sandboxed filesystem roots
    submission_policy: str       # "stop-before-submit" | "submit-with-approval" | "never-submit"
    budget: Budget               # { max_steps, max_tokens_cents, max_minutes }
    created_at: str              # ISO-8601 UTC
    updated_at: str
    history: list[ActionRecord]  # action, verdict, observation, verification outcome
    verification: Verification   # { state: UNVERIFIED|VERIFIED|FAILED, detail }
    error: ErrorState | None     # { kind, message, retries_used, escalated }
    owner: str                   # executor checkout id; None = unowned
```

**Invariants:** single owner (atomic checkout — second checkout fails);
`RUNNING` requires owner; terminal states immutable; every mutation appends an
audit event and bumps `updated_at`; `verification.state` may only move
UNVERIFIED→VERIFIED/FAILED per action (never assumed).
