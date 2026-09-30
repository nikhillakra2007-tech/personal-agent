"""Slice-45: chained multi-template runs (sequential supervised legs).

A chain is an ordered list of 2-4 legs, each naming one single-result
road (follow | observe | form | search) with that road's exact existing
goal dict. run_chain() drives the legs in order through the unchanged
dispatcher, stopping at the first leg that cannot continue.

Composition notes (all precedents, no new machinery):

* Like LoopRunner, legs run on a sibling non-completing runner
  (complete_task=False): intermediate DONE never completes the task.
  Only the chain driver maps the final outcome, mirroring
  LoopRunner._end (best-effort TASK_LIFECYCLE on DONE).
* Like every composed entry, each leg re-observes/re-grounds from the
  live page; nothing flows between legs except the shared task
  (budgets, guards, approvals) and the caller's decider. There is no
  cross-leg data piping by design: a leg's findings never become the
  next leg's hints.
* Planner dispatches on task.goal text, so the driver sets a routing
  goal per leg (explicit leg "goal" wins, else a road default) and
  restores the original afterwards. The persisted DB row is never
  rewritten by this: only the in-memory object moves, and only for
  the duration of the run.
* Loop legs are refused at validation: LoopRunner owns task lifecycle
  at LOOP_END, which would complete the task mid-chain. Repetition
  inside chains is deferred, not smuggled.
* No chain cursor is persisted: resume is per-leg --resume, or a
  fresh --chain-file run with --from-leg N. L3 legs re-park for fresh
  consent on any rerun (crash rule), never resumed-to-execute.

No new executors, predicates, templates, policy, hint keys, audit
event types, or migrations. Terminal history stays immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .loop import TaskLoop
from .runner import Runner
from .tasks import Status

MAX_LEGS = 4
MIN_LEGS = 2
SINGLE_ROADS = ("follow", "observe", "form", "search")

LEG_KEYS = {
    "follow": ("list_url", "goal_text", "body_expect"),
    "observe": ("url", "expect_text"),
    "form": ("form_url", "goal_slots", "submit_selector"),
    "search": ("search_url", "query", "submit_selector", "expect_text"),
}


class ChainRefused(ValueError):
    """Chain shape violation. Fail-closed before anything launches."""


@dataclass
class ChainResult:
    chain_id: str
    status: str  # DONE | STOPPED | PAUSED
    legs_done: int
    steps_done: int = 0
    detail: str = ""


def leg_goal_text(leg: dict) -> str:
    """Routing text for one leg: explicit leg "goal" wins, else a road
    default built from the leg's own fields (never invented content —
    each default echoes caller-stated addressing)."""
    if isinstance(leg.get("goal"), str) and leg["goal"].strip():
        return leg["goal"]
    road = leg.get("road")
    if road == "follow":
        return f"Follow {leg.get('goal_text', 'the link')}"
    if road == "observe":
        return f"Show {leg.get('expect_text', 'the page')}"
    if road == "form":
        return "Submit the form"
    if road == "search":
        query = leg.get("query")
        text = query.get("text") if isinstance(query, dict) else query
        return f"Web search {text or 'the query'}"
    return "Chained leg"


def validate_chain(chain: dict) -> list:
    """Normalize a chain mapping to its leg list. Raises ChainRefused
    for non-mappings, missing/empty/out-of-range leg lists, unknown
    roads, loop roads, and legs missing their road's required keys.
    Every leg is checked before anything launches."""
    if not isinstance(chain, dict):
        raise ChainRefused("chain must be a mapping with a legs list")
    legs = chain.get("legs")
    if not isinstance(legs, list) or not (MIN_LEGS <= len(legs) <= MAX_LEGS):
        raise ChainRefused(
            f"chain needs {MIN_LEGS}..{MAX_LEGS} legs,"
            f" got {len(legs) if isinstance(legs, list) else legs!r}")
    for n, leg in enumerate(legs, 1):
        if not isinstance(leg, dict):
            raise ChainRefused(f"leg {n} must be a mapping")
        road = leg.get("road")
        if road not in SINGLE_ROADS:
            raise ChainRefused(
                f"leg {n} road must be one of"
                f" {'|'.join(SINGLE_ROADS)}"
                + (" (loop legs: use run_linked_task, not chains)"
                   if road == "loop" or "max_items" in leg
                   or "max_iters" in leg else ""))
        missing = [k for k in LEG_KEYS[road] if k not in leg]
        if missing:
            raise ChainRefused(
                f"leg {n} ({road}) missing {missing}")
    return legs


def run_chain(taskloop: TaskLoop, store, open_page, read_links,
              read_controls, task, chain: dict, decider,
              observe=None, from_leg: int = 1):
    """Drive a validated chain to DONE/STOPPED/PAUSED.

    Returns ChainResult; preconditions fail as shaped STOPPED results
    (single PLAN_OUTCOME audit entry, plan_id "-", like every composed
    entry). from_leg is 1-based: resume an explicit rerun at leg N;
    legs_done counts absolutely (skipped legs asserted done by the
    operator's explicit rerun).
    """
    from .loops import run_task
    audit = taskloop.runner.audit
    chain_id = uuid4().hex

    def refuse(detail: str) -> ChainResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return ChainResult(chain_id=chain_id, status="STOPPED",
                           legs_done=0, steps_done=0, detail=detail)

    try:
        legs = validate_chain(chain)
    except ChainRefused as exc:
        return refuse(f"invalid chain ({exc})")
    if not isinstance(from_leg, int) or isinstance(from_leg, bool) \
            or not (1 <= from_leg <= len(legs)):
        return refuse(f"invalid from_leg {from_leg!r} for"
                      f" a {len(legs)}-leg chain")

    base = taskloop.runner
    sibling = Runner(
        base.controller, base.scheduler, base.audit,
        planner=taskloop.planner, observe=observe or base.observe,
        store=store if store is not None else base.store,
        proposer=base.proposer, guards=base.guards,
        complete_task=False)
    loop = TaskLoop(sibling, taskloop.approvals, base.audit,
                    planner=taskloop.planner)

    original_goal = task.goal
    done = from_leg - 1
    steps = 0
    try:
        for n, leg in enumerate(legs[from_leg - 1:], from_leg):
            task.goal = leg_goal_text(leg)
            res = run_task(loop, sibling.store, open_page, read_links,
                           read_controls, task, leg, decider)
            steps += res.steps_done
            if res.status == "DONE":
                done = n
                continue
            if res.status == "PAUSED":
                return ChainResult(
                    chain_id=chain_id, status="PAUSED", legs_done=done,
                    steps_done=steps,
                    detail=f"leg {n}/{len(legs)} ({leg['road']})"
                           f" paused: {res.detail}")
            return ChainResult(
                chain_id=chain_id, status="STOPPED", legs_done=done,
                steps_done=steps,
                detail=f"leg {n}/{len(legs)} ({leg['road']})"
                       f" stopped: {res.detail}")
    finally:
        task.goal = original_goal
    try:
        reg = base.controller.router.registry
        if reg.get(task.task_id).status == Status.RUNNING:
            reg.set_status(task.task_id, Status.COMPLETED)
            audit.log("TASK_LIFECYCLE", task.task_id,
                      {"chain_id": chain_id,
                       "task_status": Status.COMPLETED.value})
    except Exception:
        pass  # chain outcome stands; lifecycle is best-effort here
    return ChainResult(chain_id=chain_id, status="DONE",
                       legs_done=len(legs), steps_done=steps,
                       detail=f"{len(legs)} of {len(legs)} legs done")
