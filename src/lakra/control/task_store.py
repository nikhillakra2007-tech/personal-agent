"""Slice-08: task persistence (facts, never handles).

Maps Task <-> SQLite rows. Nested structures travel as JSON columns;
storage never imports behavior. All writes go through the caller's
transaction discipline (single statements, commit by the store user).
"""

from __future__ import annotations

import dataclasses
import json

from .tasks import (
    ActionRecord,
    Budget,
    ErrorState,
    Status,
    Task,
    Verification,
)


def task_to_row(task: Task) -> tuple:
    return (
        task.task_id,
        task.goal,
        task.parent_id,
        task.status.value,
        task.priority,
        task.permission_level,
        json.dumps(task.allowed_tools),
        json.dumps(task.allowed_domains),
        json.dumps(task.allowed_paths),
        task.submission_policy,
        json.dumps(dataclasses.asdict(task.budget)),
        task.created_at,
        task.updated_at,
        json.dumps([dataclasses.asdict(h) for h in task.history]),
        json.dumps(dataclasses.asdict(task.verification)),
        json.dumps(dataclasses.asdict(task.error)) if task.error else None,
        task.owner,
    )


def row_to_task(row: tuple) -> Task:
    (task_id, goal, parent_id, status, priority, permission_level,
     tools, domains, paths, submission_policy, budget, created_at,
     updated_at, history, verification, error, owner) = row
    verr = json.loads(verification)
    return Task(
        task_id=task_id,
        goal=goal,
        parent_id=parent_id,
        status=Status(status),
        priority=priority,
        permission_level=permission_level,
        allowed_tools=json.loads(tools),
        allowed_domains=json.loads(domains),
        allowed_paths=json.loads(paths),
        submission_policy=submission_policy,
        budget=Budget(**json.loads(budget)),
        created_at=created_at,
        updated_at=updated_at,
        history=[ActionRecord(**h) for h in json.loads(history)],
        verification=Verification(**verr),
        error=ErrorState(**json.loads(error)) if error else None,
        owner=owner,
    )


COLUMNS = ("task_id, goal, parent_id, status, priority, permission_level,"
           " allowed_tools, allowed_domains, allowed_paths,"
           " submission_policy, budget, created_at, updated_at, history,"
           " verification, error, owner")
PLACEHOLDERS = ",".join(["?"] * 17)


def insert_task(db, task: Task) -> None:
    db.execute(f"INSERT INTO tasks({COLUMNS}) VALUES ({PLACEHOLDERS})",
               task_to_row(task))
    db.commit()


def update_task(db, task: Task) -> None:
    row = task_to_row(task)
    db.execute("UPDATE tasks SET goal=?, parent_id=?, status=?, priority=?,"
               " permission_level=?, allowed_tools=?, allowed_domains=?,"
               " allowed_paths=?, submission_policy=?, budget=?,"
               " created_at=?, updated_at=?, history=?, verification=?,"
               " error=?, owner=? WHERE task_id=?",
               row[1:] + row[:1])
    db.commit()


def load_task(db, task_id: str) -> Task | None:
    cur = db.execute(f"SELECT {COLUMNS} FROM tasks WHERE task_id=?",
                     (task_id,))
    row = cur.fetchone()
    return row_to_task(row) if row else None


def load_all_tasks(db) -> list[Task]:
    cur = db.execute(f"SELECT {COLUMNS} FROM tasks")
    return [row_to_task(r) for r in cur.fetchall()]


def release_all_owners(db) -> int:
    cur = db.execute(
        "UPDATE tasks SET owner=NULL WHERE owner IS NOT NULL")
    db.commit()
    return cur.rowcount


def record_steps(db, task_id: str, steps: int) -> None:
    db.execute("INSERT INTO usage(task_id, steps_used, tokens_used)"
               " VALUES (?,?,0) ON CONFLICT(task_id) DO UPDATE SET"
               " steps_used=excluded.steps_used", (task_id, steps))
    db.commit()


def record_tokens(db, task_id: str, tokens: int) -> None:
    db.execute("INSERT INTO usage(task_id, steps_used, tokens_used)"
               " VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET"
               " tokens_used=excluded.tokens_used", (task_id, 0, tokens))
    db.commit()


def get_usage(db, task_id: str) -> tuple[int, int]:
    cur = db.execute("SELECT steps_used, tokens_used FROM usage"
                     " WHERE task_id=?", (task_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (0, 0)
