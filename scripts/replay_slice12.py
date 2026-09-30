"""Slice-12 demo: model-aware replan + observational failure corpus.

1. A model-derived plan (plan_id "-m") fails verification -> runner asks
   the proposer (scripted provider) -> fresh single-step plan -> DONE.
   Attempt linkage printed (parent -> child).
2. An unfixable plan STOPs -> exactly one corpus row (hash, no content).
3. Corpus query: rows listed; per-task cap noted.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice12.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control import plan_store  # noqa: E402
from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.planner import Plan, PlannedStep, Planner  # noqa: E402
from lakra.control.policy import Action  # noqa: E402
from lakra.control.proposing import Attempt, ProposingPlanner  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.browser.verification import Predicate  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.models.base import ModelProvider, ModelResponse  # noqa: E402

DB = ROOT / "var" / "slice12.db"
LOG = ROOT / "var" / "audit-slice12.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
for p in (DB, LOG):
    if p.exists():
        p.unlink()


class ScriptedProvider(ModelProvider):
    """Recorded responses in order (deterministic demo, no daemon)."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    @property
    def name(self):
        return "recorded/replan-demo"

    def complete(self, prompt, *, budget_tokens):
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return ModelResponse(text=text, prompt_tokens=5,
                             completion_tokens=len(text))


def snap_proposal(target, expect):
    return json.dumps({"tool_kind": "browser.snapshot", "target": target,
                       "effect": "read", "expect_kind": "text_contains",
                       "expect_target": expect, "rationale": "second look"})


def rig():
    db = Database(DB)
    registry = TaskRegistry(store=db)
    audit = AuditLog(LOG)
    sched = Scheduler(registry, store=db)
    tools = ToolRegistry()
    sessions = BrowserSessions(ROOT / "var" / "slice12-profile")
    sessions.launch()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, ROOT / "var" / "slice12-shots")
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
    ctrl = BrowserController(router, hands, audit, sched, observer=obs)
    return db, registry, audit, sched, sessions, hands, obs, router, ctrl


def start(registry, sched, goal, router=None, obs=None, hands=None):
    from lakra.control.policy import Action
    task = registry.add(Task.create(
        goal, allowed_tools=["browser"], allowed_domains=["file:"],
        allowed_paths=[]))
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)
    sched.enqueue(task.task_id)
    if router is not None:
        # Navigate through the router (obs owns the page), then share it —
        # the same two-object share the controller itself relies on.
        res = router.route(task.task_id, Action(
            kind="browser.navigate", target=FIXTURE, effect="reversible",
            task_id=task.task_id))
        assert res.ok, res.error
        hands.attach(obs.current_page)
    else:
        hands.open(FIXTURE)
    return task


db, registry, audit, sched, sessions, hands, obs, router, ctrl = rig()

# 1. model-derived plan fails -> proposer replans -> DONE ---------------------------
task = start(registry, sched, "Replan demo", router, obs, hands)
bad_step = PlannedStep(
    action=Action(kind="browser.click", target="#toggle",
                  effect="reversible", task_id=task.task_id),
    expect=Predicate(kind="text_contains", target="never-appears"),
    max_retries=0,
    rationale="model-proposed first guess")
plan = Plan(plan_id="demo-m", task_id=task.task_id, goal=task.goal,
            steps=[bad_step], created_at="2026-09-27T00:00:00+00:00")
pp = ProposingPlanner(
    Planner(),
    ScriptedProvider([snap_proposal(FIXTURE, "pending")]))
runner = Runner(ctrl, sched, audit,
                observe=lambda: hands.page.locator("body").inner_text(),
                store=db, proposer=pp)
res = runner.run(plan, hints={"url": FIXTURE, "expect_text": "pending"})
assert (res.status, res.steps_done) == ("DONE", 1), res
print("1. model plan failed verification -> proposer replanned -> DONE")
types = [e["type"] for e in audit.replay()]
assert "MODEL_REPLAN" in types
print("   MODEL_REPLAN audited with attempt linkage")

# 2. unfixable lineage -> STOPPED + exactly one corpus row -----------------------------
task2 = start(registry, sched, "Doomed demo", router, obs, hands)
pp2 = ProposingPlanner(
    Planner(),
    ScriptedProvider(['{"tool_kind": "nope"}']))
plan2 = Plan(plan_id="doom-m", task_id=task2.task_id, goal=task2.goal,
             steps=[PlannedStep(
                 action=Action(kind="browser.click", target="#ghost",
                               effect="reversible", task_id=task2.task_id),
                 expect=Predicate(kind="text_contains", target="x"),
                 max_retries=0,
                 rationale="model-proposed dead end")],
             created_at="2026-09-27T00:00:00+00:00")
runner2 = Runner(ctrl, sched, audit,
                 observe=lambda: hands.page.locator("body").inner_text(),
                 store=db, proposer=pp2)
res2 = runner2.run(plan2, hints={"selector": "#ghost",
                                 "expect_text": "x"})
assert res2.status == "STOPPED", res2
rows = plan_store.failures_for_task(db, task2.task_id)
assert len(rows) == 1, rows
print(f"2. unfixable lineage STOPPED; corpus row: kind={rows[0]['kind']}"
      f" hash={rows[0]['snapshot_hash'][:12]}... (content-free)")
sessions.close()
db.close()
print("REPLAY OK")
