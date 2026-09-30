"""Slice-01 T4: append-only audit log.

Write-only sink: log() appends, replay() re-reads for humans/tests. Nothing
in the control plane may consult replay() to make a decision.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, task_id: str,
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "task_id": task_id,
            "payload": payload or {},
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
        return event

    def replay(self) -> list[dict[str, Any]]:
        """Re-read the stream in order. For inspection/tests only."""
        if not self.path.exists():
            return []
        events = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
