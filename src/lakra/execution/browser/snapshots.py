"""Slice-16: snapshot store for diff verification (past tense for checks).

Per-task bounded history of lightweight state records (url, body text,
per-selector counts). Redacted by construction: only rendered body text is
stored — input values never appear in inner_text, and the store keeps no
screenshots, no raw trees, no credentials. Cap enforced per task.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

STORE_CAP_PER_TASK = 10


@dataclass
class SnapshotRecord:
    url: str
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()


class SnapshotStore:
    def __init__(self, cap: int = STORE_CAP_PER_TASK) -> None:
        self.cap = cap
        self._history: dict[str, deque] = {}

    def record(self, task_id: str, record: SnapshotRecord) -> None:
        hist = self._history.setdefault(task_id, deque(maxlen=self.cap))
        hist.append(record)

    def latest(self, task_id: str) -> SnapshotRecord | None:
        hist = self._history.get(task_id)
        return hist[-1] if hist else None

    def depth(self, task_id: str) -> int:
        return len(self._history.get(task_id, ()))
