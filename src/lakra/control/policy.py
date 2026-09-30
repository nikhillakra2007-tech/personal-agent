"""Slice-01 T2: policy engine.

Implements docs/contracts/policy-schema.md (draft v0.1).

evaluate() is a pure function: same (action, task) -> same verdict, no I/O,
no clock, no randomness. Rules apply first-match in contract order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .tasks import Task

Verdict = str
ALLOW: Verdict = "ALLOW"
ASK: Verdict = "ASK"
BLOCK: Verdict = "BLOCK"

# Action kinds whose *effect class* is always consequential (L3), regardless
# of what the caller claims in Action.effect.
L3_KINDS = frozenset({
    "browser.submit",
    "mail.send",
    "message.send",
    "publish",
    "purchase",
    "payment",
    "upload.private",
    "fs.delete.important",
    "settings.change",
    "git.push.important",
})

# Action kinds that are never executed (L4). Kept as full kind strings so a
# caller cannot smuggle them past by labelling effect="read".
L4_KINDS = frozenset({
    "auth.bypass",
    "captcha.defeat",
    "security.bypass",
    "access.unauthorized",
    "safeguard.evade",
    "destroy.out_of_bounds",
})

# Explicitly denied targets (domains, paths, command summaries). Empty by
# default; callers pass their own set. Matching is exact on the raw target
# plus, for browser kinds, on the extracted domain.
DENY_TARGETS: frozenset[str] = frozenset()


@dataclass
class Action:
    kind: str  # e.g. "browser.navigate" | "browser.submit" | "fs.write"
    target: str  # URL, domain, path, or command summary
    effect: str  # read | reversible | bounded-mutation | consequential | blocked
    task_id: str


@dataclass
class Approval:
    approval_id: str
    action: Action
    requested_at: str
    decided: str | None = None  # None | "approved" | "denied"
    decided_at: str | None = None
    expires_at: str | None = None  # parked approvals fail closed past this

    @classmethod
    def request(cls, action: Action, ttl_s: int = 900) -> Approval:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        return cls(
            approval_id=uuid4().hex,
            action=action,
            requested_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl_s)).isoformat(),
        )

    def expired(self, now: str | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc).isoformat()
        return now >= self.expires_at


def _tool_of(action: Action) -> str:
    return action.kind.split(".")[0]


def _domain_of(target: str) -> str:
    """Best-effort host extraction for bounds checking (no network)."""
    t = target.strip().lower()
    for scheme in ("https://", "http://"):
        if t.startswith(scheme):
            t = t[len(scheme):]
            break
    return t.split("/")[0].split("?")[0]


def _in_bounds(action: Action, task: Task) -> bool:
    tool = _tool_of(action)
    if tool not in task.allowed_tools:
        return False
    if tool == "browser":
        # Only navigation crosses trust boundaries: selectors/keys/scroll
        # targets act on the already-gated open page, so they inherit its
        # trust (L3-kind and L4 rules above still apply to every kind).
        if action.kind != "browser.navigate":
            return True
        return _domain_of(action.target) in task.allowed_domains
    if tool == "fs":
        return any(action.target.startswith(p) for p in task.allowed_paths)
    if tool == "mcp":
        # MCP tools are namespaced capabilities (mcp.<server>.<tool>):
        # server scoping lives in the per-task MCPManager (slice-07), so
        # membership in allowed_tools is the whole bounds check here.
        return True
    return True


def evaluate(
    action: Action,
    task: Task,
    *,
    deny_targets: frozenset[str] = DENY_TARGETS,
) -> Verdict:
    """Deterministic first-match verdict for one concrete action."""
    # Rule 1 — L4 blocklist.
    if (action.effect == "blocked"
            or action.kind in L4_KINDS
            or action.target in deny_targets
            or _domain_of(action.target) in deny_targets):
        return BLOCK
    # Rule 2 — L3 approval-required.
    if action.kind in L3_KINDS:
        return ASK
    if (action.effect == "consequential"
            and task.permission_level < 3):
        return ASK
    # Rule 3 — bounds check: out-of-scope is ASK, never silent ALLOW.
    if not _in_bounds(action, task):
        return ASK
    # Rule 4 — L2 controlled, inside bounds.
    if action.effect == "bounded-mutation":
        return ALLOW
    # Rule 5 — L0/L1.
    return ALLOW
