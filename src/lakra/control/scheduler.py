"""Slice-02: deterministic scheduler + declared-capacity admission.

Decides WHICH task gets an execution turn. Never executes an action itself.

Ordering: higher priority first; ties broken FIFO by enqueue sequence
(deterministic). Terminal / PAUSED / WAITING_APPROVAL tasks are never yielded.
No fairness algorithm: sustained high-priority work can starve low-priority
work. Documented and accepted for single-user V1 scale.

Capacity is DECLARED, not measured: admit() compares a task's stated needs
against assumed headroom. No hardware telemetry exists in this slice and the
Capacity defaults must never be presented as measurements. Slice-06's real
monitor (resources/monitor.py) will feed measured headroom into this same
admit() contract.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field

from .registry import TaskRegistry
from .tasks import TERMINAL, Status, Task


@dataclass
class Capacity:
    """Assumed machine headroom for admission. Placeholders, not telemetry."""
    max_concurrent_turns: int = 1
    ram_mb_claimable: int = 2048
    vram_mb_claimable: int = 1024


@dataclass
class Needs:
    """A task's declared resource needs for one turn."""
    turns: int = 1
    ram_mb: int = 0
    vram_mb: int = 0


@dataclass
class Turn:
    """A granted execution turn. Ownership of the task is NOT transferred;
    the caller must already hold checkout."""
    task: Task
    needs: Needs = field(default_factory=Needs)


class AdmissionDenied(Exception):
    """Task is queued but not currently admissible (budget/capacity/state)."""


class Scheduler:
    def __init__(self, registry: TaskRegistry,
                 capacity: Capacity | None = None,
                 monitor=None, store=None) -> None:
        self.registry = registry
        self.capacity = capacity or Capacity()
        self.monitor = monitor  # ResourceMonitor or None (None = declared only)
        self.store = store  # Database or None (usage write-through)
        self._lock = threading.Lock()
        self._seq = itertools.count()
        self._heap: list[tuple[int, int, str]] = []  # (-priority, seq, id)
        self._queued: set[str] = set()
        self._needs: dict[str, Needs] = {}
        self._steps_used: dict[str, int] = {}
        self._admitted_turns = 0

    def enqueue(self, task_id: str, needs: Needs | None = None) -> None:
        task = self.registry.get(task_id)
        with self._lock:
            if task_id in self._queued:
                return
            heapq.heappush(self._heap, (-task.priority, next(self._seq),
                                       task_id))
            self._queued.add(task_id)
            self._needs[task_id] = needs or Needs()
            self._steps_used.setdefault(task_id, 0)

    def _admissible(self, task: Task, needs: Needs) -> bool:
        # State + capacity gates ONLY. Step-budget exhaustion is decided at
        # grant time (next_turn) and at direct-router entry — never here —
        # so a just-granted turn is always spendable by its holder.
        if task.status in TERMINAL:
            return False
        if task.status in (Status.PAUSED, Status.WAITING_APPROVAL):
            return False
        if task.status not in (Status.QUEUED, Status.RUNNING):
            return False
        if task.owner is None:
            return False  # a turn without an owner can execute nothing
        if needs.turns > self.capacity.max_concurrent_turns:
            return False
        if needs.ram_mb > self.capacity.ram_mb_claimable:
            return False
        if needs.vram_mb > self.capacity.vram_mb_claimable:
            return False
        if self.monitor is not None:
            # Measured headroom overrides declared headroom. Unknown GPU
            # skips the VRAM gate (never assumed safe, never assumed zero).
            snap = self.monitor.sample()
            if needs.ram_mb > snap.ram_available_mb:
                return False
            if (snap.gpu_available
                    and needs.vram_mb > (snap.vram_free_mb or 0)):
                return False
        return True

    def is_admissible(self, task_id: str) -> bool:
        """Read-only visibility into _admissible for the router (slice-03).
        Adds no behavior; admission logic is unchanged."""
        with self._lock:
            task = self.registry.get(task_id)
            return self._admissible(task, self._needs.get(task_id, Needs()))

    def next_turn(self) -> Turn | None:
        """Highest-priority admissible task, or None. Skipped tasks stay queued."""
        with self._lock:
            deferred: list[tuple[int, int, str]] = []
            result: Turn | None = None
            while self._heap:
                entry = heapq.heappop(self._heap)
                needs = self._needs.get(entry[2])
                if needs is None:
                    continue  # stale entry from cancel(); drop lazily
                task = self.registry.get(entry[2])
                if (self._admissible(task, needs)
                        and self._steps_used.get(task.task_id, 0)
                        < task.budget.max_steps):
                    result = Turn(task=task, needs=needs)
                    self._queued.discard(task.task_id)
                    self._steps_used[task.task_id] = \
                        self._steps_used.get(task.task_id, 0) + 1
                    if self.store is not None:
                        from . import task_store
                        task_store.record_steps(
                            self.store, task.task_id,
                            self._steps_used[task.task_id])
                    break
                deferred.append(entry)
            for entry in deferred:
                heapq.heappush(self._heap, entry)
            return result

    def release_turn(self, task_id: str, requeue: bool = True) -> None:
        """Return a task to the queue after its turn (unless terminal)."""
        task = self.registry.get(task_id)
        with self._lock:
            if task.status in TERMINAL:
                self._queued.discard(task_id)
                self._needs.pop(task_id, None)
                return
            if requeue and task_id not in self._queued:
                heapq.heappush(self._heap, (-task.priority, next(self._seq),
                                           task_id))
                self._queued.add(task_id)

    # -- lifecycle: thin wrappers over Task.transition (invariants preserved) --
    def pause(self, task_id: str) -> Task:
        return self.registry.set_status(task_id, Status.PAUSED)

    def resume(self, task_id: str) -> Task:
        task = self.registry.set_status(task_id, Status.RUNNING)
        self.enqueue(task_id, self._needs.get(task_id))
        return task

    def cancel(self, task_id: str) -> Task:
        task = self.registry.set_status(task_id, Status.CANCELLED)
        with self._lock:
            self._queued.discard(task_id)
            self._needs.pop(task_id, None)
        try:
            self.registry.release(task_id, task.owner or "")
        except Exception:
            pass
        return task

    def steps_used(self, task_id: str) -> int:
        return self._steps_used.get(task_id, 0)

    def rebuild(self) -> list[str]:
        """Boot recovery: re-enqueue every non-terminal task and restore
        persisted step counters. Heap order itself is never persisted."""
        from .tasks import TERMINAL
        revived = []
        for task_id in self.registry.ids():
            task = self.registry.get(task_id)
            if task.status in TERMINAL:
                continue
            if self.store is not None:
                from . import task_store
                steps, _ = task_store.get_usage(self.store, task_id)
                if steps:
                    self._steps_used[task_id] = steps
            self.enqueue(task_id)
            revived.append(task_id)
        return revived

    @staticmethod
    def minutes_elapsed(task: Task) -> float:
        from datetime import datetime
        created = datetime.fromisoformat(task.created_at)
        now = datetime.now(created.tzinfo)
        return (now - created).total_seconds() / 60.0

    def time_ok(self, task: Task) -> bool:
        return self.minutes_elapsed(task) < task.budget.max_minutes

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)
