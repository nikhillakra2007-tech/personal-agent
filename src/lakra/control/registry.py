"""Slice-01 T3: in-memory task registry.

Single-owner checkout, atomic under a lock: at most one holder per task, so
a second checkout fails instead of silently sharing ownership. This is the
miniature of the future computer-control lock.

The registry never touches the audit log: audit is a write-only sink and no
control-flow decision here may depend on it (plan.md section 4).
"""

from __future__ import annotations

import threading

from .tasks import Status, Task


class RegistryError(KeyError):
    """Unknown task_id."""


class CheckoutError(RuntimeError):
    """Task already owned by another executor."""


class TaskRegistry:
    def __init__(self, store=None) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self.store = store  # task_store module (insert/update/load) or None

    def add(self, task: Task) -> Task:
        with self._lock:
            if task.task_id in self._tasks:
                raise RegistryError(f"duplicate task_id {task.task_id}")
            self._tasks[task.task_id] = task
            if self.store is not None:
                from . import task_store
                task_store.insert_task(self.store, task)
            return task

    def get(self, task_id: str) -> Task:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError:
                pass
            if self.store is not None:
                from . import task_store
                task = task_store.load_task(self.store, task_id)
                if task is not None:
                    self._tasks[task_id] = task
                    return task
            raise RegistryError(f"unknown task_id {task_id}")

    def checkout(self, task_id: str, owner: str) -> Task:
        """Atomically claim ownership. Second checkout raises CheckoutError."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RegistryError(f"unknown task_id {task_id}")
            if task.owner is not None and task.owner != owner:
                raise CheckoutError(
                    f"task {task_id} already owned by {task.owner}")
            task.owner = owner
            self._persist(task)
            return task

    def release(self, task_id: str, owner: str) -> Task:
        """Release ownership; only the holder may release."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RegistryError(f"unknown task_id {task_id}")
            if task.owner != owner:
                raise CheckoutError(
                    f"task {task_id} owned by {task.owner}, "
                    f"not {owner}; cannot release")
            task.owner = None
            self._persist(task)
            return task

    def set_status(self, task_id: str, status: Status) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RegistryError(f"unknown task_id {task_id}")
            task.transition(status)
            self._persist(task)
            return task

    def _persist(self, task: Task) -> None:
        if self.store is not None:
            from . import task_store
            task_store.update_task(self.store, task)

    def refresh(self, task_id: str) -> Task:
        """Resync one task from shared truth (slice-37 adopt path).

        Overwrites the in-memory copy with the persisted row so
        cross-process status moves (approve.py deciding) become visible
        to this process's gates (redeem()'s RUNNING re-check). Read-only
        toward the database; no transition is performed (like load_all
        boot recovery). store=None or a missing row behaves like get().
        """
        if self.store is None:
            return self.get(task_id)
        from . import task_store
        with self._lock:
            task = task_store.load_task(self.store, task_id)
            if task is None:
                raise RegistryError(f"unknown task_id {task_id}")
            self._tasks[task_id] = task
            return task

    def load_all(self) -> list[Task]:
        """Boot recovery: load every persisted task into memory."""
        if self.store is None:
            return list(self._tasks.values())
        from . import task_store
        with self._lock:
            for task in task_store.load_all_tasks(self.store):
                self._tasks.setdefault(task.task_id, task)
            return list(self._tasks.values())

    def release_stale_owners(self) -> int:
        """Boot recovery: ownership is runtime-only; invalidate it all.
        Returns distinct tasks released (memory and DB mirror each other)."""
        mem_owned = 0
        with self._lock:
            for task in self._tasks.values():
                if task.owner is not None:
                    task.owner = None
                    mem_owned += 1
        if self.store is not None:
            from . import task_store
            db_released = task_store.release_all_owners(self.store)
            return max(mem_owned, db_released)
        return mem_owned

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._tasks.keys())
