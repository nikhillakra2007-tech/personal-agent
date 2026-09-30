"""Slice-11: plan persistence (immutable history).

Plans and per-step outcomes persist so a restart resumes mid-plan and the
failure corpus is queryable. Immutability rule (approved clarification 1):
step outcomes are written ONCE (guarded by WHERE outcome IS NULL); a plan
marked SUPERSEDED is frozen — set_plan_status refuses any transition out
of SUPERSEDED/DONE/STOPPED. History is append-only; nothing rewrites it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TERMINAL_PLAN = frozenset({"DONE", "STOPPED", "SUPERSEDED"})


def save_plan(db, plan, hints: dict) -> None:
    db.execute("INSERT INTO plans(plan_id, task_id, goal, status, hints,"
               " supersedes, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (plan.plan_id, plan.task_id, plan.goal, plan.status,
                json.dumps(hints), None, plan.created_at, _now()))
    for i, step in enumerate(plan.steps):
        db.execute("INSERT INTO plan_steps(plan_id, idx, kind, target,"
                   " effect, expect_kind, expect_target, rationale,"
                   " max_retries, outcome, decided_at)"
                   " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (plan.plan_id, i, step.action.kind, step.action.target,
                    step.action.effect, step.expect.kind,
                    step.expect.target, step.rationale, step.max_retries,
                    None, None))
    db.commit()


def set_plan_status(db, plan_id: str, status: str,
                    supersedes: str | None = None) -> None:
    cur = db.execute("SELECT status FROM plans WHERE plan_id=?", (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"unknown plan {plan_id}")
    if row[0] in TERMINAL_PLAN:
        raise ValueError(f"plan {plan_id} is {row[0]}: history immutable")
    db.execute("UPDATE plans SET status=?, supersedes=?, updated_at=?"
               " WHERE plan_id=?", (status, supersedes, _now(), plan_id))
    db.commit()


def set_step_outcome(db, plan_id: str, idx: int, outcome: str) -> bool:
    """Write-once: returns False if the outcome was already recorded."""
    cur = db.execute("UPDATE plan_steps SET outcome=?, decided_at=?"
                     " WHERE plan_id=? AND idx=? AND outcome IS NULL",
                     (outcome, _now(), plan_id, idx))
    db.commit()
    return cur.rowcount == 1


def load_plan(db, plan_id: str):
    """Returns (Plan, hints, outcomes list) with steps in order."""
    from .planner import Plan, PlannedStep
    from .policy import Action
    from ..execution.browser.verification import Predicate
    cur = db.execute("SELECT plan_id, task_id, goal, status, hints,"
                     " supersedes, created_at FROM plans WHERE plan_id=?",
                     (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"unknown plan {plan_id}")
    pid, task_id, goal, status, hints_json, _, created = row
    cur = db.execute("SELECT kind, target, effect, expect_kind,"
                     " expect_target, rationale, max_retries, outcome"
                     " FROM plan_steps WHERE plan_id=? ORDER BY idx",
                     (plan_id,))
    steps, outcomes = [], []
    for (kind, target, effect, ek, et, rat, retries, outcome) in cur.fetchall():
        steps.append(PlannedStep(
            action=Action(kind=kind, target=target, effect=effect,
                          task_id=task_id),
            expect=Predicate(kind=ek, target=et), max_retries=retries,
            rationale=rat))
        outcomes.append(outcome)
    plan = Plan(plan_id=pid, task_id=task_id, goal=goal, steps=steps,
                created_at=created, status=status)
    return plan, json.loads(hints_json), outcomes


def plans_for_task(db, task_id: str) -> list[str]:
    cur = db.execute("SELECT plan_id FROM plans WHERE task_id=? ORDER BY"
                     " created_at", (task_id,))
    return [r[0] for r in cur.fetchall()]


# -- failure corpus (slice-12): observational ONLY --------------------------------
# One row per terminal lineage failure. Hashes + capped detail, never page
# text or secrets. Nothing reads this table to change behavior in this
# slice; it exists so a future slice has evidence instead of archaeology.
DETAIL_CAP = 500
CORPUS_PER_TASK_CAP = 50


def record_failure(db, task_id: str, plan_id: str, step_idx: int,
                   kind: str, verdict: str | None, detail: str,
                   snapshot: str, model_name: str | None = None) -> None:
    import hashlib
    snap_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    db.execute("INSERT INTO failure_records(task_id, plan_id, step_idx,"
               " kind, verdict, detail, snapshot_hash, model_name,"
               " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (task_id, plan_id, step_idx, kind, verdict,
                detail[:DETAIL_CAP], snap_hash, model_name, _now()))
    db.execute("DELETE FROM failure_records WHERE task_id=? AND id NOT IN"
               " (SELECT id FROM failure_records WHERE task_id=?"
               " ORDER BY id DESC LIMIT ?)",
               (task_id, task_id, CORPUS_PER_TASK_CAP))
    db.commit()


def failures_for_task(db, task_id: str) -> list[dict]:
    cur = db.execute("SELECT plan_id, step_idx, kind, verdict, detail,"
                     " snapshot_hash, model_name, created_at"
                     " FROM failure_records WHERE task_id=? ORDER BY id",
                     (task_id,))
    keys = ("plan_id", "step_idx", "kind", "verdict", "detail",
            "snapshot_hash", "model_name", "created_at")
    return [dict(zip(keys, row)) for row in cur.fetchall()]
