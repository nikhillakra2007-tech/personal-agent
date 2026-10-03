"""Slice-29A: cursor-unrolled repetition over inventoried links.

A RepeatSpec names a bounded item source (links_matching only: link
texts containing a substring, capped) plus a per-item body built by the
existing planner. The LoopRunner unrolls ONE body plan per cycle through
the normal supervised path (TaskLoop.run_plan: policy, guards, router,
verification, per-iteration approvals via the one-shot token path) —
there is no loop step that executes, and no new executor authority.

Identity is by item TEXT (stable across re-queries); each cycle
re-queries fresh inventory and visits the first text not yet in
findings, so vanished items are skipped and an emptied page ends the
loop (first_empty, DONE). Cycles are bounded by max_iters (<=5);
visits by max_items. The worst-case step cost is prechecked against
remaining task budget before anything runs (and rechecked on resume
against REMAINING cycles, so progress never strands a loop).

Persistence: loops table holds status + immutable spec + cursor
(iterations completed) + findings + pending body plan/item (PAUSED
mid-iteration). Resume finishes the pending body plan first (fresh
observation is the runner's own resume contract), then continues the
cursor. Terminal loops refuse resume (history is immutable).

Task lifecycle: body plans run on a non-completing sibling Runner
(complete_task=False), so intermediate DONE never completes the task.
Only LOOP_END DONE completes it (RUNNING -> COMPLETED); STOPPED leaves
it stalled/resumable; PAUSED is owned by the runner's pause path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from .loop import TaskLoop
from .runner import Runner
from .tasks import Status

MAX_ITERS = 5
LOOP_STATUSES = frozenset({"RUNNING", "DONE", "STOPPED", "PAUSED"})


class LoopRefused(ValueError):
    """RepeatSpec shape violation. Fail-closed before anything runs."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RepeatSpec:
    loop_id: str
    task_id: str
    list_url: str
    match_text: str
    max_items: int
    max_iters: int
    body_expect: str

    @classmethod
    def create(cls, task_id: str, list_url: str, match_text: str,
               max_items: int, max_iters: int,
               body_expect: str) -> "RepeatSpec":
        for name, value in (("list_url", list_url),
                            ("match_text", match_text),
                            ("body_expect", body_expect)):
            if not isinstance(value, str) or not value.strip():
                raise LoopRefused(f"{name} must be a non-empty string")
        for name, value in (("max_items", max_items),
                            ("max_iters", max_iters)):
            if type(value) is not int or value < 1:
                raise LoopRefused(f"{name} must be a positive integer")
        if max_iters > MAX_ITERS:
            raise LoopRefused(f"max_iters capped at {MAX_ITERS}")
        if max_items > max_iters:
            raise LoopRefused("max_items cannot exceed max_iters")
        return cls(loop_id=uuid4().hex, task_id=task_id,
                   list_url=list_url, match_text=match_text,
                   max_items=max_items, max_iters=max_iters,
                   body_expect=body_expect)

    def to_json(self) -> str:
        return json.dumps({
            "loop_id": self.loop_id, "task_id": self.task_id,
            "list_url": self.list_url, "match_text": self.match_text,
            "max_items": self.max_items, "max_iters": self.max_iters,
            "body_expect": self.body_expect})


@dataclass
class LoopResult:
    loop_id: str
    status: str  # DONE | STOPPED | PAUSED
    iters_done: int
    findings: list = field(default_factory=list)
    detail: str = ""


# -- persistence (loops table, slice-29A v5 objects) --------------------------

def _save(db, spec: RepeatSpec, status: str) -> None:
    db.execute("INSERT INTO loops(loop_id, task_id, status, spec, cursor,"
               " findings, pending_plan, pending_item, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?,?)",
               (spec.loop_id, spec.task_id, status, spec.to_json(), 0,
                json.dumps([]), None, None, _now()))
    db.commit()


def _load(db, loop_id: str) -> tuple[RepeatSpec, dict]:
    cur = db.execute("SELECT task_id, status, spec, cursor, findings,"
                     " pending_plan, pending_item FROM loops"
                     " WHERE loop_id=?", (loop_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"unknown loop {loop_id}")
    task_id, status, spec_json, cursor, findings_json, pending, item = row
    spec = RepeatSpec(**json.loads(spec_json))
    return spec, {"status": status, "cursor": cursor,
                  "findings": json.loads(findings_json),
                  "pending_plan": pending, "pending_item": item}


def _update(db, loop_id: str, status: str, cursor: int,
            findings: list, pending_plan: str | None,
            pending_item: str | None) -> None:
    db.execute("UPDATE loops SET status=?, cursor=?, findings=?,"
               " pending_plan=?, pending_item=?, updated_at=?"
               " WHERE loop_id=?",
               (status, cursor, json.dumps(findings), pending_plan,
                pending_item, _now(), loop_id))
    db.commit()


# -- driver -------------------------------------------------------------------

class LoopRunner:
    """Drives a RepeatSpec to DONE/STOPPED/PAUSED. Owns the task's
    lifecycle for the loop's duration; body plans run non-completing."""

    def __init__(self, taskloop: TaskLoop, store=None, inventory_fn=None,
                 plan_for=None, observe=None) -> None:
        self.taskloop = taskloop
        self.store = store if store is not None else taskloop.runner.store
        base = taskloop.runner
        # Sibling runner: identical components, but body DONE/STOPPED
        # must not own the task (the loop does, at LOOP_END).
        self.runner = Runner(
            base.controller, base.scheduler, base.audit,
            planner=taskloop.planner, observe=observe or base.observe,
            store=self.store, proposer=base.proposer, guards=base.guards,
            complete_task=False)
        self.loop = TaskLoop(self.runner, taskloop.approvals,
                             base.audit, planner=taskloop.planner)
        # inventory_fn() -> list[str]: fresh link texts (caller-provided,
        # like Runner.observe — control never touches browser internals).
        self.inventory_fn = inventory_fn or (lambda: [])
        self.plan_for = plan_for or self._follow_plan_for

    def _follow_plan_for(self, task, spec: RepeatSpec, item: str):
        from .planner import Planner
        hints = {"url": spec.list_url, "link_text": item,
                 "expect_text": spec.body_expect}
        return Planner().plan(task, hints), hints

    # -- entry points ------------------------------------------------------
    def start(self, task, decider, *, list_url: str, match_text: str,
              max_items: int, max_iters: int, body_expect: str,
              plan_for=None, spec: RepeatSpec | None = None) -> LoopResult:
        if spec is None:
            spec = RepeatSpec.create(task.task_id, list_url, match_text,
                                     max_items, max_iters, body_expect)
        if plan_for is not None:
            self.plan_for = plan_for
        if self.store is not None:
            _save(self.store, spec, "RUNNING")
        self._audit().log("LOOP_START", task.task_id,
                          {"loop_id": spec.loop_id, "list_url": list_url,
                           "match_text": match_text,
                           "max_items": max_items, "max_iters": max_iters})
        return self._drive(spec, task, decider, [], 0)

    def resume(self, loop_id: str, decider) -> LoopResult:
        spec, state = self._state(loop_id)
        if state["status"] in ("DONE", "STOPPED"):
            return LoopResult(loop_id, state["status"], state["cursor"],
                              state["findings"],
                              "loop already terminal; history immutable")
        task = self._task(spec.task_id)
        self._audit().log("LOOP_RESUMED", spec.task_id,
                          {"loop_id": loop_id, "cursor": state["cursor"],
                           "pending_plan": state["pending_plan"]})
        if state["pending_plan"] is not None:
            # Supervised resume (slice-42/D-01): the pending body may
            # need its L3 gate decided, exactly like bodies _drive runs
            # through run_plan — so it drains through the same
            # adopt/decide/mint/execute loop via resume_plan instead of
            # raw runner.resume (which would stop, mislabeled, at the
            # first re-parked gate). Non-ASK outcomes pass through
            # untouched, identical to before.
            res = self.loop.resume_plan(state["pending_plan"],
                                        self.runner.observe, decider)
            out = self._after_body(spec, task, state, res,
                                   state["pending_item"])
            if out is not None:
                return out
            state = self._state(loop_id)[1]  # _after_body persisted
        return self._drive(spec, task, decider, state["findings"],
                           state["cursor"])

    # -- core ---------------------------------------------------------------
    def _drive(self, spec, task, decider, findings, cycles):
        # Invariant: every completed cycle appends exactly one finding,
        # so cycles == len(findings) here and max_items (<= max_iters)
        # always binds first; the cycles < max_iters guard below is a
        # defensive bound, and "iteration cap" its unreachable-by-
        # construction reason.
        while len(findings) < spec.max_items and cycles < spec.max_iters:
            est = self._establish(spec, task, decider)
            if est.status != "DONE":
                if est.status == "PAUSED":
                    self._persist(spec.loop_id, "PAUSED", len(findings),
                                  findings, None, None)
                    return LoopResult(spec.loop_id, "PAUSED",
                                      len(findings), findings, est.detail)
                return self._end(spec, task, "STOPPED", findings,
                                 f"cannot establish list ({est.detail})")
            try:
                texts = [t for t in (self.inventory_fn() or [])
                         if spec.match_text in t]
            except Exception as exc:
                return self._end(spec, task, "STOPPED", findings,
                                 f"inventory failed: {exc}")
            item = next((t for t in texts if t not in findings), None)
            if item is None:
                return self._end(spec, task, "DONE", findings,
                                 "first_empty: no unvisited items")
            try:
                plan, hints = self._build(task, spec, item)
            except Exception as exc:
                return self._end(spec, task, "STOPPED", findings,
                                 f"cannot plan iteration ({exc})")
            # Worst-case precheck against REMAINING cycles (self-
            # consistent across resume: steady progress keeps passing;
            # wasted steps fail closed here instead of mid-flight).
            # The establish leg costs 2 steps; the body is measured,
            # never assumed.
            sched = self.runner.scheduler
            if sched is not None:
                remaining = (task.budget.max_steps
                             - sched.steps_used(task.task_id))
                if ((spec.max_iters - cycles) * (2 + len(plan.steps))
                        > remaining):
                    return self._end(
                        spec, task, "STOPPED", findings,
                        "loop refused: worst-case steps exceed budget")
            res = self.loop.run_plan(plan, hints, decider)
            state = {"findings": findings, "pending_plan": None,
                     "pending_item": item}
            out = self._after_body(spec, task, state, res, item)
            if out is not None:
                return out
            findings = state["findings"]
            cycles += 1
        reason = ("item cap reached" if len(findings) >= spec.max_items
                  else "iteration cap reached")
        return self._end(spec, task, "DONE", findings, reason)

    def _establish(self, spec, task, decider):
        """Re-open the list page before each inventory read (every cycle
        ends on a detail page, so inventory must never be read stale).
        Shape mirrors the observe template (navigate + snapshot); built
        directly so the loop goal's own keywords can never route it
        elsewhere. Returns the establish RunResult for _drive to map."""
        from .planner import Plan, PlannedStep
        from .policy import Action
        from ..execution.browser.verification import Predicate
        steps = [
            PlannedStep(
                action=Action(kind="browser.navigate",
                              target=spec.list_url, effect="reversible",
                              task_id=task.task_id),
                expect=Predicate(kind="url_is", target=spec.list_url),
                max_retries=2,
                rationale=f"open {spec.list_url} so the list is current"),
            PlannedStep(
                action=Action(kind="browser.snapshot",
                              target=spec.list_url, effect="read",
                              task_id=task.task_id),
                expect=Predicate(kind="text_contains",
                                 target=spec.match_text),
                max_retries=1,
                rationale="confirm the list shows matchable items"),
        ]
        plan = Plan(plan_id=uuid4().hex, task_id=task.task_id,
                    goal=f"establish list for {spec.loop_id}", steps=steps,
                    created_at=_now())
        return self.loop.run_plan(plan, {}, decider)

    def _after_body(self, spec, task, state, res, item):
        """Map one body outcome. Returns a LoopResult when the loop ends
        (or pauses); returns None to continue iterating (findings
        appended into state)."""
        findings = state["findings"]
        if res.status == "DONE":
            findings = findings + [item]
            self._audit().log("LOOP_ITERATION", spec.task_id,
                              {"loop_id": spec.loop_id, "n": len(findings),
                               "item": item, "outcome": "done",
                               "plan_id": res.plan_id})
            self._persist(spec.loop_id, "RUNNING", len(findings),
                          findings, None, None)
            state["findings"] = findings
            return None
        if res.status == "PAUSED":
            self._audit().log("LOOP_ITERATION", spec.task_id,
                              {"loop_id": spec.loop_id,
                               "n": len(findings) + 1,
                               "item": item, "outcome": "paused",
                               "plan_id": res.plan_id})
            self._persist(spec.loop_id, "PAUSED", len(findings),
                          findings, res.plan_id, item)
            return LoopResult(spec.loop_id, "PAUSED", len(findings),
                              findings, res.detail)
        if res.status == "ASK_PENDING":
            return self._end(spec, task, "STOPPED", findings,
                             "approval vanished before decision")
        self._audit().log("LOOP_ITERATION", spec.task_id,
                          {"loop_id": spec.loop_id, "n": len(findings) + 1,
                           "item": item, "outcome": "stopped",
                           "detail": res.detail, "plan_id": res.plan_id})
        return self._end(spec, task, "STOPPED", findings,
                         f"iteration stopped: {res.detail}")

    def _end(self, spec, task, status, findings, detail):
        self._persist(spec.loop_id, status, len(findings), findings,
                      None, None)
        self._audit().log("LOOP_END", spec.task_id,
                          {"loop_id": spec.loop_id, "status": status,
                           "iters_done": len(findings), "detail": detail,
                           "findings": findings})
        if status == "DONE":
            try:
                reg = self.taskloop.runner.controller.router.registry
                if reg.get(task.task_id).status == Status.RUNNING:
                    reg.set_status(task.task_id, Status.COMPLETED)
                    self._audit().log("TASK_LIFECYCLE", task.task_id,
                                      {"loop_id": spec.loop_id,
                                       "task_status":
                                           Status.COMPLETED.value})
            except Exception:
                pass  # loop outcome stands; lifecycle is best-effort here
        return LoopResult(spec.loop_id, status, len(findings), findings,
                          detail)

    # -- helpers -------------------------------------------------------------
    def _audit(self):
        return self.taskloop.runner.audit

    def _build(self, task, spec, item):
        return self.plan_for(task, spec, item)

    def _task(self, task_id: str):
        return self.taskloop.runner.controller.router.registry.get(task_id)

    def _state(self, loop_id: str):
        if self.store is None:
            raise ValueError("resume needs a store-backed LoopRunner")
        return _load(self.store, loop_id)

    def _persist(self, loop_id, status, cursor, findings, pending,
                 item):
        if self.store is None:
            return
        try:
            _update(self.store, loop_id, status, cursor, findings,
                    pending, item)
        except Exception:
            pass  # persistence never fails a loop; audit tells truth


# -- composed entry (slice-33, grounded-follow road only) --------------------

def run_follow_task(taskloop: TaskLoop, open_page, read_links,
                    task, goal: dict, decider):
    """Goal -> observe -> ground -> TaskLoop.run_goal (slice-33).

    goal = {"list_url", "goal_text", "body_expect"}. open_page(list_url)
    and read_links() -> [(text, href)] are caller-provided browser seams
    (same precedent as inventory_fn and Runner.observe — control never
    touches browser internals). Grounding derives link_text via the
    existing deterministic grounded_follow(); execution is the standard
    follow-link template through TaskLoop.run_goal (policy, guards,
    verification, persistence, existing audit taxonomy). No new
    executors, predicates, templates, policy, or event types; no model;
    no loop/form/dispatcher logic. Refusals at any pre-plan stage
    return STOPPED RunResult(plan_id="-", steps_done=0) with a single
    PLAN_OUTCOME audit entry (the TaskLoop.run_goal refusal shape) and
    touch nothing else.
    """
    from .analyzer import UnknownGoalError, grounded_follow
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("list_url", "goal_text", "body_expect")
               if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    try:
        open_page(goal["list_url"])
    except Exception as exc:
        return refuse(f"cannot open list ({exc})")
    try:
        inventory = list(read_links() or [])
    except Exception as exc:
        return refuse(f"inventory failed ({exc})")
    try:
        hints = grounded_follow(goal["goal_text"], goal["list_url"],
                                inventory, goal["body_expect"])
    except UnknownGoalError as exc:
        return refuse(f"no groundable link ({exc})")
    return taskloop.run_goal(task, hints, decider)


# -- composed entry (click road only) ----------------------------------------

def run_click_task(taskloop: TaskLoop, open_page, read_clicks,
                   task, goal: dict, decider):
    """Goal -> observe -> ground -> TaskLoop.run_goal (click road).

    goal = {"click_url", "click_text", "expect_text"}.
    open_page(click_url) and read_clicks() -> [(label, ref)] are
    caller-provided browser seams (same precedent as inventory_fn
    and Runner.observe — control never touches browser internals).
    Grounding binds the caller-stated click_text to one observed
    clickable target via the existing deterministic ground_click()
    (unique winner; link text for the executor's text rung or a #id
    selector; submit-kind controls never enter the inventory, so a
    browser.click here can never dodge the L3 browser.submit gate).
    Execution is the standard click template through
    TaskLoop.run_goal (policy, guards, verification, persistence,
    existing audit taxonomy). No new executors, predicates,
    templates, policy, hint keys, or event types; no model; no
    loop/follow/form/observe/dispatcher logic. Refusals at any
    pre-plan stage return STOPPED RunResult(plan_id="-", steps_done=0)
    with a single PLAN_OUTCOME audit entry (the TaskLoop.run_goal
    refusal shape) and touch nothing else. Post-grounding outcomes
    (DONE/STOPPED/PAUSED) pass through untouched.
    """
    from .analyzer import UnknownGoalError, ground_click
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("click_url", "click_text", "expect_text")
               if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    if read_clicks is None:
        return refuse("inventory unavailable: no click inventory seam")
    try:
        open_page(goal["click_url"])
    except Exception as exc:
        return refuse(f"cannot open page ({exc})")
    try:
        inventory = list(read_clicks() or [])
    except Exception as exc:
        return refuse(f"inventory failed ({exc})")
    try:
        selector = ground_click(goal["click_text"], inventory)
    except UnknownGoalError as exc:
        return refuse(f"no groundable click target ({exc})")
    hints = {"url": goal["click_url"], "selector": selector,
             "expect_text": goal["expect_text"]}
    return taskloop.run_goal(task, hints, decider)


# -- composed entry (V2-04, grounded-table road only) ---------------------

def run_table_task(taskloop: TaskLoop, open_page, read_tables,
                   task, goal: dict, decider):
    """Goal -> observe -> ground -> extract -> verify -> run_plan.

    goal = {"table_url", "table_text", "expect_text"}.
    open_page(table_url) and read_tables() -> [((total, rows), ...)]
    are caller-provided browser seams (same precedent as inventory_fn
    and Runner.observe — control never touches browser internals).
    Grounding binds the caller-stated table_text to one observed
    table via the deterministic ground_table() (unique winner; table
    located by inventory position, never by invented CSS); extraction
    renders that table read-only to headers/rows (order, empties, and
    shape preserved; the page is never mutated, no page.evaluate);
    verification confirms the caller-stated expect_text INSIDE the
    extracted cells (never "a table exists"). Credential-shaped
    descriptions, expectations, or extracted content refuse outright
    (existing credential-safety contract: never recorded, never
    transmitted, no findings attached).

    Execution is a directly-built navigate + snapshot plan (the
    LoopRunner._establish precedent: built inline so the task goal's
    own keywords can never route it elsewhere, and so no planner,
    analyzer, or template moves) through TaskLoop.run_plan (policy,
    guards, per-step verification, persistence, existing audit
    taxonomy — both steps are L0 read-only, so no approval ever
    parks). Refusals at any pre-plan stage return STOPPED
    RunResult(plan_id="-", steps_done=0) with a single PLAN_OUTCOME
    audit entry (the TaskLoop.run_goal refusal shape) and touch
    nothing else. On DONE the extracted table rides findings (one
    JSON-serializable dict) with a one-line detail summary; every
    other outcome passes through untouched with no findings.
    """
    from .runner import RunResult
    from .tables import (extract_table, ground_table, table_holds_secret,
                         verify_expected)
    from .planner import UnknownGoalError
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("table_url", "table_text", "expect_text")
               if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    if read_tables is None:
        return refuse("inventory unavailable: no table inventory seam")
    url, desc, expect = (goal["table_url"], goal["table_text"],
                        goal["expect_text"])
    for name, value in (("table_url", url), ("table_text", desc),
                        ("expect_text", expect)):
        if not isinstance(value, str) or not value.strip():
            return refuse(f"malformed goal: {name} must be a non-empty"
                          " string")
    try:
        open_page(url)
    except Exception as exc:
        return refuse(f"cannot open page ({exc})")
    try:
        inventory = list(read_tables() or [])
    except Exception as exc:
        return refuse(f"inventory failed ({exc})")
    try:
        index = ground_table(desc, inventory)
    except UnknownGoalError as exc:
        return refuse(f"no groundable table ({exc})")
    try:
        extracted = extract_table(inventory, index)
    except UnknownGoalError as exc:
        return refuse(f"cannot extract table ({exc})")
    if table_holds_secret(extracted):
        return refuse("credential-shaped table content: refused")
    try:
        verify_expected(extracted, expect)
    except UnknownGoalError as exc:
        return refuse(f"unverified table ({exc})")
    from .planner import Plan, PlannedStep
    from .policy import Action
    from ..execution.browser.verification import Predicate
    steps = [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task.task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so observation starts from a known"
                      " page"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task.task_id),
            expect=Predicate(kind="text_contains", target=expect.strip()),
            max_retries=1,
            rationale=f"read the page and confirm it mentions"
                      f" {expect.strip()!r}"),
    ]
    plan = Plan(plan_id=uuid4().hex, task_id=task.task_id,
                goal=task.goal, steps=steps, created_at=_now())
    hints = {"url": url, "expect_text": expect.strip(),
             "table_text": desc.strip()}
    res = taskloop.run_plan(plan, hints, decider)
    if res.status != "DONE":
        return res
    shape = extracted["shape"]
    done = RunResult(plan_id=res.plan_id, status=res.status,
                     steps_done=res.steps_done,
                     detail=f"table {index + 1}/{extracted['total_tables']}:"
                            f" {shape[0]} rows x {shape[1]} cols;"
                            f" expected {expect.strip()!r} verified")
    done.findings = [extracted]  # RunResult carries no findings field;
    # the CLI reads this attribute for the JSON output line (the same
    # getattr finish() already uses); non-DONE outcomes carry none.
    return done


# -- composed entry (V2-05, download road only) ---------------------------

def _transfer_confine(target: str, root):
    """Sandbox-relative path -> absolute Path, or None on any escape.
    Same realpath-prefix contract as the executors (absolute-outside,
    ../, and symlink escapes all refuse); pure path math, no writes."""
    import os
    from pathlib import Path as _Path
    if root is None:
        return None
    try:
        root_real = os.path.realpath(root)
    except Exception:
        return None
    if os.path.isabs(target):
        full = os.path.realpath(target)
    else:
        full = os.path.realpath(os.path.join(root_real, target))
    if full != root_real and not full.startswith(root_real + os.sep):
        return None
    return _Path(full)


def _secret_basename(path: str) -> bool:
    """True when the file name itself is secret-shaped (filenames are
    not prose: any SECRET_WORD substring of the lowercased basename
    refuses — a "secretary.txt" false refusal beats an exposure)."""
    import os as _os
    from .planner import SECRET_WORDS as _SECRETS
    base = _os.path.basename(path).lower()
    return any(w in base for w in _SECRETS)


def run_download_task(taskloop: TaskLoop, open_page, read_links,
                      task, goal: dict, decider, transfer_root=None):
    """Goal -> observe -> ground -> L3 download -> verify -> run_plan.

    goal = {"download_url", "download_text", "dest_path",
    "expect_text"} (expect_text: page proof shown after the transfer).
    open_page(download_url) and read_links() -> [(text, href)] are
    caller-provided browser seams; transfer_root is the sandbox the
    dest must already confine to (road-level pre-plan refusal, so an
    invalid destination never reaches an approval, let alone an
    execution). Grounding binds download_text to one observed link
    via the existing deterministic ground_link() (unique winner; the
    trigger clicks through the executor's text rung); an existing
    dest refuses (no silent overwrite — including on recovery after
    a post-save crash: the operator clears or re-queues with a fresh
    dest instead of the road guessing which file won).

    Execution is a directly-built navigate + snapshot + download +
    snapshot plan (the LoopRunner._establish precedent: no planner,
    analyzer, or template moves) through TaskLoop.run_plan — policy
    (browser.download is L3: park, one-shot token, execute once),
    guards, per-step verification, persistence, existing audit
    taxonomy. Like every L3 gate, the approval itself stands in for
    the gate predicate; the transfer itself is proven by the
    executor's post-conditions (saved, confined, size-bounded —
    executor failure never reads DONE) and the closing snapshot. The
    gate step never retries (submit-gate precedent). Refusals at any
    pre-plan stage return STOPPED
    RunResult(plan_id="-", steps_done=0) with a single PLAN_OUTCOME
    audit entry and touch nothing else. On DONE the transfer summary
    rides findings; every other outcome (incl. human deny at the
    gate) passes through untouched with no findings.
    """
    from .planner import Plan, PlannedStep, validate_hints
    from .policy import Action
    from .runner import RunResult
    from ..execution.browser.verification import Predicate
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("download_url", "download_text", "dest_path",
                           "expect_text") if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    url, text, dest, expect = (goal["download_url"],
                               goal["download_text"], goal["dest_path"],
                               goal["expect_text"])
    try:
        hints = validate_hints({"url": url, "expect_text": expect,
                                "dest_path": dest})
    except Exception as exc:
        return refuse(f"malformed goal ({exc})")
    url, expect, dest = (hints["url"], hints["expect_text"].strip(),
                         hints["dest_path"].strip())
    if _transfer_confine(dest, transfer_root) is None:
        return refuse(f"invalid destination {dest!r}: outside the"
                      " transfer sandbox")
    if not _transfer_confine(dest, transfer_root).name:
        return refuse("invalid destination: must name a file")
    if _transfer_confine(dest, transfer_root).exists():
        return refuse(f"invalid destination {dest!r}: already exists"
                      " (no silent overwrite)")
    try:
        open_page(url)
    except Exception as exc:
        return refuse(f"cannot open page ({exc})")
    try:
        inventory = list(read_links() or [])
    except Exception as exc:
        return refuse(f"inventory failed ({exc})")
    from .analyzer import UnknownGoalError, ground_link
    try:
        trigger = ground_link(text, inventory)
    except UnknownGoalError as exc:
        return refuse(f"no groundable download trigger ({exc})")
    steps = [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task.task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so observation starts from a known"
                      " page"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task.task_id),
            expect=Predicate(kind="text_contains", target=trigger),
            max_retries=1,
            rationale=f"confirm the page offers {trigger!r} before"
                      " downloading"),
        PlannedStep(
            action=Action(kind="browser.download",
                          target=f"{trigger}\n---\n{dest}",
                          effect="consequential", task_id=task.task_id),
            expect=Predicate(kind="file_nonempty", target=dest),
            max_retries=0,
            rationale=f"download {trigger!r} to {dest} (L3 gate: parks"
                      " for a human, executes once)"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task.task_id),
            expect=Predicate(kind="text_contains", target=expect),
            max_retries=1,
            rationale=f"record the page mentioning {expect!r} after"
                      " the transfer"),
    ]
    plan = Plan(plan_id=uuid4().hex, task_id=task.task_id,
                goal=task.goal, steps=steps, created_at=_now())
    hints = {"url": url, "expect_text": expect, "dest_path": dest}
    res = taskloop.run_plan(plan, hints, decider)
    if res.status != "DONE":
        return res
    try:
        size = _transfer_confine(dest, transfer_root).stat().st_size
    except OSError:
        size = -1
    done = RunResult(plan_id=res.plan_id, status=res.status,
                     steps_done=res.steps_done,
                     detail=f"downloaded {dest} ({size} bytes)")
    done.findings = [{"dest_path": dest, "bytes": size}]
    return done


# -- composed entry (V2-05, upload road only) -----------------------------

def run_upload_task(taskloop: TaskLoop, open_page, read_controls,
                    task, goal: dict, decider, transfer_root=None):
    """Goal -> observe -> ground -> L3 upload -> verify -> run_plan.

    goal = {"upload_url", "upload_text", "src_path", "expect_text"}
    (expect_text: page proof of acceptance, e.g. the shown file
    name). open_page(upload_url) and read_controls() ->
    [(label, kind, selector)] are caller-provided browser seams;
    transfer_root is the sandbox the source must already live in as
    a regular size-bounded file (road-level pre-plan refusal, so a
    bad source never reaches an approval). Grounding binds
    upload_text to one observed file input via ground_upload()
    (unique winner, #id only; ambiguous inputs refuse); the source
    basename must not be secret-shaped (refuse rather than
    transmit credentials).

    Execution is a directly-built navigate + snapshot + upload +
    snapshot plan through TaskLoop.run_plan — same L3/token/guard/
    verification/audit contract as the download road (the gate step
    never retries; like every L3 gate, the approval itself stands in
    for the gate predicate, so page acceptance is verified by the
    CLOSING snapshot on fresh state — never set_input_files()
    success alone). Refusal and findings conventions mirror
    run_download_task.
    """
    from .planner import Plan, PlannedStep, validate_hints
    from .policy import Action
    from .runner import RunResult
    from ..execution.browser.verification import Predicate
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("upload_url", "upload_text", "src_path",
                           "expect_text") if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    url, label, src, expect = (goal["upload_url"], goal["upload_text"],
                               goal["src_path"], goal["expect_text"])
    try:
        hints = validate_hints({"url": url, "expect_text": expect,
                                "src_path": src})
    except Exception as exc:
        return refuse(f"malformed goal ({exc})")
    url, expect, src = (hints["url"], hints["expect_text"].strip(),
                        hints["src_path"].strip())
    confined = _transfer_confine(src, transfer_root)
    if confined is None:
        return refuse(f"invalid source {src!r}: outside the transfer"
                      " sandbox")
    try:
        is_file = confined.is_file()
        size = confined.stat().st_size if is_file else -1
    except OSError as exc:
        return refuse(f"unreadable source ({exc})")
    if not is_file:
        return refuse(f"invalid source {src!r}: not an uploadable file")
    from ..execution.browser.actions import MAX_TRANSFER_BYTES
    if size > MAX_TRANSFER_BYTES:
        return refuse(f"invalid source {src!r}: size {size} exceeds"
                      f" {MAX_TRANSFER_BYTES}")
    if _secret_basename(src):
        return refuse(f"invalid source {src!r}: secret-shaped file"
                      " name refused")
    try:
        open_page(url)
    except Exception as exc:
        return refuse(f"cannot open page ({exc})")
    try:
        controls = list(read_controls() or [])
    except Exception as exc:
        return refuse(f"controls failed ({exc})")
    from .analyzer import UnknownGoalError, ground_upload
    try:
        selector = ground_upload(label, controls)
    except UnknownGoalError as exc:
        return refuse(f"no groundable file input ({exc})")
    steps = [
        PlannedStep(
            action=Action(kind="browser.navigate", target=url,
                          effect="reversible", task_id=task.task_id),
            expect=Predicate(kind="url_is", target=url),
            max_retries=2,
            rationale=f"open {url} so observation starts from a known"
                      " page"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task.task_id),
            expect=Predicate(kind="text_contains", target=label.strip()),
            max_retries=1,
            rationale=f"confirm the page offers {label.strip()!r}"
                      " before uploading"),
        PlannedStep(
            action=Action(kind="browser.upload",
                          target=f"{selector}\n---\n{src}",
                          effect="consequential", task_id=task.task_id),
            expect=Predicate(kind="element_exists", target=selector),
            max_retries=0,
            rationale=f"upload {src} into {selector} (L3 gate: parks"
                      " for a human, executes once)"),
        PlannedStep(
            action=Action(kind="browser.snapshot", target=url,
                          effect="read", task_id=task.task_id),
            expect=Predicate(kind="text_contains", target=expect),
            max_retries=1,
            rationale=f"confirm the page shows {expect!r}: the upload"
                      " was accepted"),
    ]
    plan = Plan(plan_id=uuid4().hex, task_id=task.task_id,
                goal=task.goal, steps=steps, created_at=_now())
    hints = {"url": url, "expect_text": expect, "src_path": src}
    res = taskloop.run_plan(plan, hints, decider)
    if res.status != "DONE":
        return res
    done = RunResult(plan_id=res.plan_id, status=res.status,
                     steps_done=res.steps_done,
                     detail=f"uploaded {src} into {selector}:"
                            f" {expect!r} verified")
    done.findings = [{"src_path": src, "selector": selector}]
    return done


# -- composed entry (slice-43, observe road only) ---------------------------

def run_observe_task(taskloop: TaskLoop, task, goal: dict, decider):
    """Goal -> TaskLoop.run_goal via the observe template (slice-43).

    goal = {"url", "expect_text"}. Both values are caller-stated
    addressing: there is no inventory to ground against, so unlike the
    follow/form roads this entry takes no browser seams — the plan's
    own navigate step opens the page. Execution is the standard
    observe template (navigate + snapshot, both L0 read-only) through
    TaskLoop.run_goal (policy, guards, verification, persistence,
    existing audit taxonomy). No new executors, predicates,
    templates, policy, or event types; no model; no loop/follow/form/
    dispatcher logic. Refusals return STOPPED RunResult(plan_id="-",
    steps_done=0) with a single PLAN_OUTCOME audit entry (the
    TaskLoop.run_goal refusal shape) and touch nothing else. The
    decider is threaded through for interface uniformity but the L0
    road never parks, so it is never consulted.
    """
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("url", "expect_text") if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    return taskloop.run_goal(task, {"url": goal["url"],
                                    "expect_text": goal["expect_text"]},
                             decider)


# -- composed entry (slice-44, search road only) ----------------------------

def run_search_task(taskloop: TaskLoop, open_page, read_controls,
                    task, goal: dict, decider):
    """Goal -> observe -> ground -> TaskLoop.run_goal (slice-44).

    goal = {"search_url", "query": {"text": ...}, "submit_selector",
    "expect_text"}. open_page(search_url) and read_controls() ->
    [(label, kind, selector)] are caller-provided browser seams (same
    precedent as inventory_fn and Runner.observe — control never
    touches browser internals). Grounding binds the fixed "search"
    slot to the observed text control via the existing deterministic
    ground_fields() (unique winner; the query VALUE travels untouched
    caller -> entry, secret-shaped text refused there); the submit
    selector stays caller-stated, as on the form road. Execution is
    the search template through TaskLoop.run_goal (policy, guards,
    per-step verification, the identical L3 submit gate via the
    one-shot token path, persistence, existing audit taxonomy) — so
    every search parks once for approval with the exact query and
    domain visible. No new executors, predicates, templates, policy,
    hint keys, or event types; no model; no loop/follow/form/observe/
    dispatcher logic. Refusals at any pre-plan stage return STOPPED
    RunResult(plan_id="-", steps_done=0) with a single PLAN_OUTCOME
    audit entry (the TaskLoop.run_goal refusal shape) and touch
    nothing else. Post-grounding outcomes pass through untouched.
    """
    from .analyzer import UnknownGoalError, ground_fields
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("search_url", "query", "submit_selector",
                           "expect_text") if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    try:
        open_page(goal["search_url"])
    except Exception as exc:
        return refuse(f"cannot open search page ({exc})")
    try:
        controls = list(read_controls() or [])
    except Exception as exc:
        return refuse(f"controls failed ({exc})")
    try:
        fields = ground_fields({"search": goal["query"]}, controls)
    except UnknownGoalError as exc:
        return refuse(f"no groundable search box ({exc})")
    entry = fields[0]
    hints = {"url": goal["search_url"],
             "selector": entry["selector"], "text": entry["text"],
             "submit_selector": goal["submit_selector"],
             "expect_text": goal["expect_text"]}
    return taskloop.run_goal(task, hints, decider)


# -- composed entry (slice-32, grounded-link road only) ---------------------

def run_linked_task(taskloop: TaskLoop, store, open_page, read_links,
                    task, goal: dict, decider) -> LoopResult:
    """Goal -> observe -> ground -> RepeatSpec -> LoopRunner (slice-32).

    goal = {"list_url", "goal_text", "body_expect", "max_items",
    "max_iters"}. open_page(list_url) and read_links() -> list[str] are
    caller-provided browser seams (same precedent as inventory_fn and
    Runner.observe — control never touches browser internals).
    Grounding derives match_text via ground_match; the loop then runs
    the standard 29A road (establish-per-cycle, guards, per-iteration
    approvals, persistence, LOOP_* audit). Refusals at any pre-loop
    stage return STOPPED with LOOP_START + LOOP_END audit under a fresh
    id and no DB row (nothing runnable exists to resume).
    """
    audit = taskloop.runner.audit
    lid = uuid4().hex

    def refuse(detail: str) -> LoopResult:
        audit.log("LOOP_START", task.task_id,
                  {"loop_id": lid, "composed": "grounded-link",
                   "goal": {k: goal.get(k) for k in
                            ("list_url", "goal_text", "body_expect",
                             "max_items", "max_iters")}})
        audit.log("LOOP_END", task.task_id,
                  {"loop_id": lid, "status": "STOPPED", "iters_done": 0,
                   "detail": detail, "findings": []})
        return LoopResult(lid, "STOPPED", 0, [], detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("list_url", "goal_text", "body_expect",
                           "max_items", "max_iters") if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    try:
        open_page(goal["list_url"])
    except Exception as exc:
        return refuse(f"cannot open list ({exc})")
    try:
        texts = list(read_links() or [])
    except Exception as exc:
        return refuse(f"inventory failed ({exc})")
    from .analyzer import UnknownGoalError, ground_match
    try:
        match = ground_match(goal["goal_text"], texts)
    except UnknownGoalError as exc:
        return refuse(f"no groundable match ({exc})")
    try:
        spec = RepeatSpec.create(task.task_id, goal["list_url"], match,
                                 goal["max_items"], goal["max_iters"],
                                 goal["body_expect"])
    except LoopRefused as exc:
        return refuse(f"invalid loop shape ({exc})")
    lr = LoopRunner(taskloop, store,
                    inventory_fn=lambda: list(read_links() or []))
    return lr.start(task, decider, list_url=goal["list_url"],
                    match_text=match, max_items=goal["max_items"],
                    max_iters=goal["max_iters"],
                    body_expect=goal["body_expect"], spec=spec)


# -- composed entry (slice-34, grounded-form road only) ---------------------

def run_form_task(taskloop: TaskLoop, open_page, read_controls,
                  task, goal: dict, decider):
    """Goal -> observe -> ground -> TaskLoop.run_goal (slice-34).

    goal = {"form_url", "goal_slots", "submit_selector"}.
    open_page(form_url) and read_controls() -> [(label, kind, selector)]
    are caller-provided browser seams (same precedent as inventory_fn
    and Runner.observe — control never touches browser internals).
    Grounding binds caller-named slots to observed controls via the
    existing deterministic ground_fields(); values travel untouched
    caller -> entry, the page supplies only #id selectors. Execution is
    the standard fill-multi template through TaskLoop.run_goal (policy,
    guards, per-field verification, the identical L3 submit gate via
    the one-shot token path, persistence, existing audit taxonomy). No
    new executors, predicates, templates, policy, hint keys, or event
    types; no model; no loop/follow/dispatcher logic. Refusals at any
    pre-plan stage return STOPPED RunResult(plan_id="-", steps_done=0)
    with a single PLAN_OUTCOME audit entry (the TaskLoop.run_goal
    refusal shape) and touch nothing else. Post-grounding outcomes
    (DONE/STOPPED/PAUSED incl. human deny at the gate) pass through
    untouched.
    """
    from .analyzer import UnknownGoalError, ground_fields
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    missing = [k for k in ("form_url", "goal_slots", "submit_selector")
               if k not in goal]
    if missing:
        return refuse(f"malformed goal: missing {missing}")
    try:
        open_page(goal["form_url"])
    except Exception as exc:
        return refuse(f"cannot open form ({exc})")
    try:
        controls = list(read_controls() or [])
    except Exception as exc:
        return refuse(f"controls failed ({exc})")
    try:
        fields = ground_fields(goal["goal_slots"], controls)
    except UnknownGoalError as exc:
        return refuse(f"no groundable fields ({exc})")
    hints = {"url": goal["form_url"], "fields": fields,
             "submit_selector": goal["submit_selector"]}
    return taskloop.run_goal(task, hints, decider)


# -- dispatcher (slice-35, key-presence routing only) -----------------------

FOLLOW_KEYS = ("list_url", "goal_text", "body_expect")
LINK_KEYS = ("list_url", "goal_text", "body_expect",
             "max_items", "max_iters")
FORM_KEYS = ("form_url", "goal_slots", "submit_selector")
CLICK_KEYS = ("click_url", "click_text", "expect_text")
TABLE_KEYS = ("table_url", "table_text", "expect_text")
DOWNLOAD_KEYS = ("download_url", "download_text", "dest_path",
                 "expect_text")
UPLOAD_KEYS = ("upload_url", "upload_text", "src_path", "expect_text")
OBSERVE_KEYS = ("url", "expect_text")
SEARCH_KEYS = ("search_url", "query", "submit_selector", "expect_text",
                "submit_phrase")


def run_task(taskloop: TaskLoop, store, open_page, read_links,
             read_controls, task, goal: dict, decider, read_clicks=None,
             read_tables=None, transfer_root=None):
    """Route one union goal to exactly one composed road (slice-35).

    Routing is key presence only, fixed precedence, never probed and
    never guessed (no content inference, no model):

      goal_slots present (and no link/loop keys) -> run_form_task;
      max_items/max_iters present (and no form keys) -> run_linked_task;
      click_url/click_text present (and no other road keys)
        -> run_click_task;
      table_url/table_text present (and no other road keys)
        -> run_table_task;
      download_url/download_text present (and no other road keys)
        -> run_download_task;
      upload_url/upload_text present (and no other road keys)
        -> run_upload_task;
      list_url/goal_text/body_expect only -> run_follow_task;
      url/expect_text only -> run_observe_task;
      search_url/query/submit_selector/expect_text only
        -> run_search_task;
      anything else (non-mapping, empty, unrecognized-only, or mixed
      road keys such as goal_slots + max_items) -> dispatcher-level
      STOPPED refusal.

    The dispatcher observes nothing itself; the chosen road observes
    exactly once, so each road's steps, result, and audit trail are
    identical to calling that road directly. read_links/read_controls
    are passed through untouched and must satisfy the chosen road's
    own seam contract (pairs for follow, texts for loop, controls
    for form, label/ref pairs for click, pairs for download, controls
    for upload); transfer roads additionally need transfer_root, the
    sandbox their paths confine to. A refused road is never
    retried on another road (no fall-through). Dispatcher-level
    refusals return STOPPED RunResult(plan_id="-", steps_done=0) with
    a single PLAN_OUTCOME audit entry; road results (RunResult or
    LoopResult, incl. LOOP_* audit and the L3 gate) pass through
    untouched. No new executors, predicates, templates, policy, hint
    keys, or event types.
    """
    from .runner import RunResult
    audit = taskloop.runner.audit

    def refuse(detail: str) -> RunResult:
        audit.log("PLAN_OUTCOME", task.task_id,
                  {"plan_id": "-", "status": "STOPPED",
                   "steps_done": 0, "detail": detail})
        return RunResult(plan_id="-", status="STOPPED", steps_done=0,
                         detail=detail)

    if not isinstance(goal, dict):
        return refuse("malformed goal: not a mapping")
    has_form = "goal_slots" in goal
    has_loop = "max_items" in goal or "max_iters" in goal
    has_link = ("list_url" in goal or "goal_text" in goal
                or "body_expect" in goal)
    has_form_addr = "form_url" in goal or "submit_selector" in goal
    has_observe = "url" in goal or "expect_text" in goal
    has_search = ("search_url" in goal or "query" in goal)
    has_click = "click_url" in goal or "click_text" in goal
    has_table = "table_url" in goal or "table_text" in goal
    has_download = "download_url" in goal or "download_text" in goal \
        or "dest_path" in goal
    has_upload = "upload_url" in goal or "upload_text" in goal \
        or "src_path" in goal
    # submit_selector/expect_text are native to the search shape too,
    # so only foreign indicators count here (slice-44).
    if has_search and (("goal_slots" in goal) or ("form_url" in goal)
                       or has_loop or has_link or has_click
                       or has_table or has_download or has_upload
                       or ("url" in goal)):
        return refuse("mixed road keys: search keys cannot combine with"
                      " other road keys")
    if has_search:
        # Routed before the observe branch: submit_selector and
        # expect_text are native here, but the observe-mixed rule
        # below reads them as foreign evidence.
        sub = {k: goal[k] for k in SEARCH_KEYS if k in goal}
        return run_search_task(taskloop, open_page, read_controls,
                               task, sub, decider)
    # Click branch (click road): routed before the link and observe
    # branches because expect_text is native here but would otherwise
    # satisfy has_observe and drop the click target (the Slice-48
    # misroute). Only "url" counts as foreign observe evidence;
    # expect_text alone is native to the click shape.
    if has_click and (has_form or has_loop or has_link or has_search
                      or has_table or has_download or has_upload
                      or has_form_addr or ("url" in goal)):
        return refuse("mixed road keys: click keys cannot combine with"
                      " other road keys")
    if has_click:
        sub = {k: goal[k] for k in CLICK_KEYS if k in goal}
        return run_click_task(taskloop, open_page, read_clicks,
                              task, sub, decider)
    # Table branch (V2-04 table road): routed before the observe
    # branch because expect_text is native here but would otherwise
    # satisfy has_observe and drop the table description (the Slice-48
    # misroute pattern). Only "url" counts as foreign observe
    # evidence; expect_text alone is native to the table shape.
    if has_table and (has_form or has_loop or has_link or has_search
                      or has_click or has_download or has_upload
                      or has_form_addr or ("url" in goal)):
        return refuse("mixed road keys: table keys cannot combine with"
                      " other road keys")
    if has_table:
        sub = {k: goal[k] for k in TABLE_KEYS if k in goal}
        return run_table_task(taskloop, open_page, read_tables,
                              task, sub, decider)
    # Download branch (V2-05 download road): routed before the observe
    # branch because expect_text is native here but would otherwise
    # satisfy has_observe and drop the download description (the
    # Slice-48 misroute pattern). Only "url" counts as foreign
    # observe evidence; expect_text alone is native to the shape.
    if has_download and (has_form or has_loop or has_link or has_search
                         or has_click or has_table or has_upload
                         or has_form_addr or ("url" in goal)):
        return refuse("mixed road keys: download keys cannot combine"
                      " with other road keys")
    if has_download:
        sub = {k: goal[k] for k in DOWNLOAD_KEYS if k in goal}
        return run_download_task(taskloop, open_page, read_links,
                                 task, sub, decider,
                                 transfer_root=transfer_root)
    # Upload branch (V2-05 upload road): same precedence rationale as
    # download; the file-input description must survive observing.
    if has_upload and (has_form or has_loop or has_link or has_search
                       or has_click or has_table or has_download
                       or has_form_addr or ("url" in goal)):
        return refuse("mixed road keys: upload keys cannot combine"
                      " with other road keys")
    if has_upload:
        sub = {k: goal[k] for k in UPLOAD_KEYS if k in goal}
        return run_upload_task(taskloop, open_page, read_controls,
                               task, sub, decider,
                               transfer_root=transfer_root)
    if has_observe and (has_form or has_loop or has_link
                        or has_form_addr):
        return refuse("mixed road keys: observe keys cannot combine with"
                      " other road keys")
    if has_form and (has_loop or has_link):
        return refuse("mixed road keys: form keys cannot combine with"
                      " link/loop keys")
    if has_form:
        sub = {k: goal[k] for k in FORM_KEYS if k in goal}
        return run_form_task(taskloop, open_page, read_controls,
                             task, sub, decider)
    if has_loop and has_form_addr:
        return refuse("mixed road keys: loop keys cannot combine with"
                      " form keys")
    if has_loop:
        sub = {k: goal[k] for k in LINK_KEYS if k in goal}
        return run_linked_task(taskloop, store, open_page, read_links,
                               task, sub, decider)
    if has_link:
        sub = {k: goal[k] for k in FOLLOW_KEYS if k in goal}
        return run_follow_task(taskloop, open_page, read_links,
                               task, sub, decider)
    if has_observe:
        sub = {k: goal[k] for k in OBSERVE_KEYS if k in goal}
        return run_observe_task(taskloop, task, sub, decider)
    return refuse("unroutable goal: no road keys present")
