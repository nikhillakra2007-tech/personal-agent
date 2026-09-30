"""Slice-01 T1: task contract.

Implements docs/contracts/task-schema.md (draft v0.1) as plain dataclasses.
No I/O, no threads, no dependencies beyond the stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class Status(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({Status.COMPLETED, Status.FAILED, Status.CANCELLED})

# Allowed transitions. Terminal states have no outgoing edges (immutable).
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.QUEUED: frozenset({Status.RUNNING, Status.CANCELLED}),
    Status.RUNNING: frozenset(
        {Status.PAUSED, Status.WAITING_APPROVAL, Status.COMPLETED,
         Status.FAILED, Status.CANCELLED}
    ),
    Status.PAUSED: frozenset({Status.RUNNING, Status.CANCELLED}),
    Status.WAITING_APPROVAL: frozenset({Status.RUNNING, Status.CANCELLED}),
    Status.COMPLETED: frozenset(),
    Status.FAILED: frozenset(),
    Status.CANCELLED: frozenset(),
}


class TransitionError(ValueError):
    """Raised when a status transition violates the contract."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Budget:
    max_steps: int
    max_tokens_cents: int  # dormant in local-only V1; kept for the cloud rung
    max_minutes: int


@dataclass
class Verification:
    state: str  # UNVERIFIED | VERIFIED | FAILED
    detail: str = ""


@dataclass
class ErrorState:
    kind: str
    message: str
    retries_used: int = 0
    escalated: bool = False


@dataclass
class ActionRecord:
    action_kind: str
    verdict: str
    observation: str = ""
    verification: str = "UNVERIFIED"


@dataclass
class Task:
    task_id: str
    goal: str
    parent_id: str | None
    status: Status
    priority: int  # 0 (lowest) .. 3 (critical)
    permission_level: int  # max pre-authorized policy level: 1 or 2
    allowed_tools: list[str]
    allowed_domains: list[str]
    allowed_paths: list[str]
    submission_policy: str
    budget: Budget
    created_at: str
    updated_at: str
    history: list[ActionRecord] = field(default_factory=list)
    verification: Verification = field(
        default_factory=lambda: Verification(state="UNVERIFIED"))
    error: ErrorState | None = None
    owner: str | None = None  # executor checkout id; None = unowned

    @classmethod
    def create(
        cls,
        goal: str,
        *,
        parent_id: str | None = None,
        priority: int = 1,
        permission_level: int = 1,
        allowed_tools: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        submission_policy: str = "stop-before-submit",
        budget: Budget | None = None,
    ) -> Task:
        if priority not in (0, 1, 2, 3):
            raise ValueError(f"priority must be 0..3, got {priority}")
        if permission_level not in (1, 2):
            raise ValueError(
                f"permission_level must be 1 or 2, got {permission_level}")
        now = _utcnow()
        return cls(
            task_id=uuid4().hex,
            goal=goal,
            parent_id=parent_id,
            status=Status.QUEUED,
            priority=priority,
            permission_level=permission_level,
            allowed_tools=list(allowed_tools or []),
            allowed_domains=list(allowed_domains or []),
            allowed_paths=list(allowed_paths or []),
            submission_policy=submission_policy,
            budget=budget or Budget(
                max_steps=50, max_tokens_cents=0, max_minutes=30),
            created_at=now,
            updated_at=now,
        )

    def transition(self, new_status: Status) -> None:
        """Move status; raises TransitionError on contract violation."""
        if self.status in TERMINAL:
            raise TransitionError(
                f"task {self.task_id} is terminal ({self.status.value}); "
                "terminal states are immutable")
        if new_status not in TRANSITIONS[self.status]:
            raise TransitionError(
                f"illegal transition {self.status.value} -> {new_status.value}")
        if new_status == Status.RUNNING and self.owner is None:
            raise TransitionError("RUNNING requires an owner (checkout first)")
        self.status = new_status
        self.updated_at = _utcnow()

    def record(self, entry: ActionRecord) -> None:
        self.history.append(entry)
        self.updated_at = _utcnow()
