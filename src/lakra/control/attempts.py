"""Slice-17: execution attempt ledger (duplicate suppression, not identity).

Each routed execution opens an attempt row (uuid) and closes it on
completion. A second begin() for the same (task_id, kind, target, effect)
while one is still open raises DuplicateAttempt — this kills the
double-fire shape from slice-16 (two identical executions with no
lineage between them). It is NARROW by design: it does not claim true
exactly-once external execution (impossible across real-world side
effects), and it does not define action identity. Retries carry
parent_attempt_id lineage and are always allowed; only unlinked
duplicates are suppressed.

Without a store the ledger is a memory dict with identical semantics
(single-process only). With a store, open/close are committed writes and
crash-orphaned rows (completed_at NULL, abandoned=0) are found by boot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


class DuplicateAttempt(RuntimeError):
    """An identical execution is already open for this 4-tuple."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AttemptLedger:
    def __init__(self, store=None) -> None:
        self.store = store
        self._open: dict[str, dict] = {}

    @staticmethod
    def _key(task_id: str, kind: str, target: str, effect: str) -> tuple:
        return (task_id, kind, target, effect)

    def begin(self, task_id: str, kind: str, target: str, effect: str,
              plan_id: str | None = None,
              parent_attempt_id: str | None = None) -> str:
        key = self._key(task_id, kind, target, effect)
        if self.store is not None:
            cur = self.store.execute(
                "SELECT attempt_id FROM attempts WHERE task_id=? AND kind=?"
                " AND target=? AND effect=? AND completed_at IS NULL"
                " AND abandoned=0", key)
            if cur.fetchone() is not None:
                raise DuplicateAttempt(
                    f"open attempt already exists for {key}")
        else:
            if key in self._open:
                raise DuplicateAttempt(
                    f"open attempt already exists for {key}")
        attempt_id = uuid4().hex
        row = {"attempt_id": attempt_id, "task_id": task_id,
               "plan_id": plan_id, "kind": kind, "target": target,
               "effect": effect, "parent_attempt_id": parent_attempt_id,
               "started_at": _now()}
        if self.store is not None:
            self.store.execute(
                "INSERT INTO attempts(attempt_id, task_id, plan_id, kind,"
                " target, effect, parent_attempt_id, started_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (attempt_id, task_id, plan_id, kind, target, effect,
                 parent_attempt_id, row["started_at"]))
            self.store.commit()
        else:
            self._open[key] = row
        return attempt_id

    def close(self, attempt_id: str, ok: bool) -> None:
        if self.store is not None:
            self.store.execute(
                "UPDATE attempts SET completed_at=?, ok=? WHERE"
                " attempt_id=? AND completed_at IS NULL",
                (_now(), 1 if ok else 0, attempt_id))
            self.store.commit()
            return
        for key, row in list(self._open.items()):
            if row["attempt_id"] == attempt_id:
                del self._open[key]
                return

    def abandon_stale(self) -> list[str]:
        """Boot recovery: freeze crash-orphaned attempts. Never completes
        them (uncertain rule); marks abandoned so they read distinctly."""
        if self.store is not None:
            cur = self.store.execute(
                "SELECT attempt_id FROM attempts WHERE completed_at IS NULL"
                " AND abandoned=0")
            ids = [r[0] for r in cur.fetchall()]
            self.store.execute(
                "UPDATE attempts SET abandoned=1 WHERE completed_at IS NULL"
                " AND abandoned=0")
            self.store.commit()
            return ids
        ids = [row["attempt_id"] for row in self._open.values()]
        self._open.clear()
        return ids

    def open_count(self) -> int:
        if self.store is not None:
            cur = self.store.execute(
                "SELECT COUNT(*) FROM attempts WHERE completed_at IS NULL"
                " AND abandoned=0")
            row = cur.fetchone()
            return row[0] if row else 0
        return len(self._open)
