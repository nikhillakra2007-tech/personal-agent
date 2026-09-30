"""Slice-02: exclusive computer-control lock.

Single owner for future physical mouse/keyboard/foreground interaction.
Separate from task checkout: a task may run background turns forever without
this lock and acquires it only for a foreground step.

There is deliberately NO steal/force API: the holder releases, or nobody
proceeds. (Slice-02 has no user-input watchdog yet; that arrives with real
foreground execution.)
"""

from __future__ import annotations

import threading


class LockError(RuntimeError):
    """Base computer-lock error."""


class LockBusyError(LockError):
    """Lock already held by another owner."""


class ComputerLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    @property
    def holder(self) -> str | None:
        with self._lock:
            return self._holder

    def acquire(self, owner: str) -> str:
        with self._lock:
            if self._holder is not None and self._holder != owner:
                raise LockBusyError(
                    f"computer control owned by {self._holder}; "
                    f"{owner} denied")
            self._holder = owner
            return owner

    def release(self, owner: str) -> None:
        with self._lock:
            if self._holder != owner:
                raise LockError(
                    f"computer control owned by {self._holder}; "
                    f"{owner} cannot release")
            self._holder = None
