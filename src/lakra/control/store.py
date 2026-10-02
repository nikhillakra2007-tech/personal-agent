"""Slice-08: SQLite store foundation.

One local file, WAL mode, 5 s busy timeout, versioned schema. Default path
is OUTSIDE synced folders (%LOCALAPPDATA%\\Lakra\\lakra.db); LAKRA_DB env
overrides for portability/tests. Unknown NEWER versions fail closed (refuse
to open) — an old binary must never misread a newer schema.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CODE_VERSION = 6
BUSY_TIMEOUT_MS = 5000

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    parent_id TEXT,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    permission_level INTEGER NOT NULL,
    allowed_tools TEXT NOT NULL,
    allowed_domains TEXT NOT NULL,
    allowed_paths TEXT NOT NULL,
    submission_policy TEXT NOT NULL,
    budget TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    history TEXT NOT NULL,
    verification TEXT NOT NULL,
    error TEXT,
    owner TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    action_json TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided TEXT,
    decided_at TEXT,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
    token_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    effect TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    task_id TEXT PRIMARY KEY,
    steps_used INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);
"""


# v1 -> v2 migration: ONLY the new objects. Never re-run v1 statements
# against a v1-shaped database (older v1 tables may lack columns that
# later v1 index statements assume). Fresh databases get the full script.
MIGRATE_V1_TO_V2 = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    hints TEXT NOT NULL,
    supersedes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    effect TEXT NOT NULL,
    expect_kind TEXT NOT NULL,
    expect_target TEXT NOT NULL,
    rationale TEXT NOT NULL,
    max_retries INTEGER NOT NULL,
    outcome TEXT,
    decided_at TEXT,
    UNIQUE(plan_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_plans_task ON plans(task_id);
"""


# v3 -> v4: execution attempt ledger (slice-17). Observability + narrow
# duplicate suppression; never a permission mechanism.
MIGRATE_V3_TO_V4 = """
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_id TEXT,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    effect TEXT NOT NULL,
    parent_attempt_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    ok INTEGER,
    abandoned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_attempts_open ON attempts(task_id, kind,
    target, effect);
"""


# v4 -> v5: repeat-loop state (slice-29A). Cursor + findings per loop;
# the spec itself is immutable history (never updated, only superseded
# by a new loop_id).
MIGRATE_V4_TO_V5 = """
CREATE TABLE IF NOT EXISTS loops (
    loop_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    spec TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT 0,
    findings TEXT NOT NULL,
    pending_plan TEXT,
    pending_item TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loops_task ON loops(task_id);
"""
MIGRATE_V2_TO_V3 = """
CREATE TABLE IF NOT EXISTS failure_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    step_idx INTEGER NOT NULL,
    kind TEXT NOT NULL,
    verdict TEXT,
    detail TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    model_name TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failures_task ON failure_records(task_id);
"""


# v5 -> v6: persisted work queue + run ledger (V2-02). Work items
# survive process death and are claimed atomically across processes;
# the ledger records which process claimed what and how each run
# ended, so stale claims are detectable and reclaimable while
# completed work can never run again. Existing tables untouched.
MIGRATE_V5_TO_V6 = """
CREATE TABLE IF NOT EXISTS work_items (
    work_id TEXT PRIMARY KEY,
    input_prose TEXT,
    legs_json TEXT,
    from_leg INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL,
    owner TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    task_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    lease_until INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_state ON work_items(state,
    created_at);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_work ON runs(work_id);
"""


class SchemaError(RuntimeError):
    """Unknown/newer schema version: fail closed."""


def default_db_path() -> Path:
    override = os.environ.get("LAKRA_DB")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Lakra" / "lakra.db"


class Database:
    """Single-connection SQLite handle with versioned migrations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(str(self.path))
        except sqlite3.Error as exc:
            raise SchemaError(f"cannot open database: {exc}") from exc
        try:
            self.conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.migrate()
        except SchemaError:
            self.conn.close()
            raise
        except Exception as exc:
            self.conn.close()
            raise SchemaError(f"database unusable: {exc}") from exc

    def migrate(self) -> None:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='meta'")
        if cur.fetchone() is None:
            self.conn.executescript(SCHEMA_V1)
            self.conn.executescript(MIGRATE_V1_TO_V2)
            self.conn.executescript(MIGRATE_V2_TO_V3)
            self.conn.executescript(MIGRATE_V3_TO_V4)
            self.conn.executescript(MIGRATE_V4_TO_V5)
            self.conn.executescript(MIGRATE_V5_TO_V6)
            self.conn.execute("INSERT INTO meta(key, value) VALUES ('v', '6')")
            self.conn.commit()
            return
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='v'").fetchone()
        try:
            version = int(row[0]) if row else 1
        except (ValueError, TypeError):
            raise SchemaError("unreadable schema version")
        if version > CODE_VERSION:
            raise SchemaError(
                f"database schema v{version} newer than code v{CODE_VERSION};"
                " refusing to open")
        if version < 1:
            raise SchemaError(f"unknown schema version {version}")
        # Chained upgrades: each step adds only its own objects.
        if version == 1:
            self.conn.executescript(MIGRATE_V1_TO_V2)
            version = 2
        if version == 2:
            self.conn.executescript(MIGRATE_V2_TO_V3)
            version = 3
        if version == 3:
            self.conn.executescript(MIGRATE_V3_TO_V4)
            version = 4
        if version == 4:
            self.conn.executescript(MIGRATE_V4_TO_V5)
            version = 5
        if version == 5:
            self.conn.executescript(MIGRATE_V5_TO_V6)
            version = 6
        if version == 6:
            # Already current: objects exist by construction of the chain
            # above. No safety-net re-runs: re-executing old DDL against
            # diverged historic tables breaks (found via migration test).
            self.conn.execute("UPDATE meta SET value='6' WHERE key='v'")
            self.conn.commit()
            return
        raise SchemaError(f"unreachable schema version {version}")

    def execute(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()
