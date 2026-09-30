"""Slice-19 demo: measured hint suggestions with deterministic supremacy.

Live model only if the daemon answers, else a recorded fake (green either
way). Shows: baseline derivation, model-added keys with provenance, an
overruled conflict, and a merged-hints plan executed to DONE.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice19.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.analyzer import analyze  # noqa: E402
from lakra.control.analyzer_model import AnalyzerProposer  # noqa: E402
from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.budgets import TokenLedger  # noqa: E402
from lakra.control.planner import Planner  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.models.base import ModelProvider, ModelResponse  # noqa: E402
from lakra.models.local import LocalProvider  # noqa: E402

LOG = ROOT / "var" / "audit-slice19.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG.exists():
    LOG.unlink()

live = LocalProvider("qwen2.5:0.5b")
if live.available():
    provider, source = live, "live qwen2.5:0.5b"
else:
    class _Recorded(ModelProvider):
        @property
        def name(self):
            return "recorded/hints-demo"

        def complete(self, prompt, *, budget_tokens):
            text = json.dumps({"selector": "#toggle",
                               "expect_text": "WRONG"})
            return ModelResponse(text=text, prompt_tokens=10,
                                 completion_tokens=30)

    provider, source = _Recorded(), "recorded (daemon down)"
print(f"provider: {source}")

GOAL = f"List my pending courses at {FIXTURE}"
baseline = analyze(GOAL)
print(f"baseline (deterministic): {sorted(baseline)}")
ledger = TokenLedger(2000)
pp = AnalyzerProposer(provider, ledger=ledger)
merged, attempt = pp.suggest(GOAL, dict(baseline))
added = sorted(set(merged) - {k for k in baseline if k != "intent"})
print(f"attempt: parsed={attempt.parsed} overruled={attempt.overruled} "
      f"tokens={attempt.tokens_used} fallback={attempt.fallback_used}")
print(f"merged: baseline keys kept; model added: {added or 'nothing new'}")
assert all(k in merged for k in baseline if k != "intent")
print("1. deterministic baseline survives every merge")

# Execute the merged plan end to end (planner re-validates regardless).
db = Database(ROOT / "var" / "slice19.db")
registry = TaskRegistry(store=db)
audit = AuditLog(LOG)
sched = Scheduler(registry, store=db)
tools = ToolRegistry()
sessions = BrowserSessions(ROOT / "var" / "slice19-profile")
sessions.launch()
hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, ROOT / "var" / "slice19-shots")
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
ctrl = BrowserController(router, hands, audit, sched, observer=obs)
runner = Runner(ctrl, sched, audit,
                observe=lambda: hands.page.locator("body").inner_text(),
                store=db)
task = registry.add(Task.create(
    GOAL, allowed_tools=["browser"], allowed_domains=["file:"],
    allowed_paths=[str(ROOT)]))
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)
plan = Planner().plan(task, merged)
res = runner.run(plan, hints=merged)
assert (res.status, res.steps_done) == ("DONE", 2), res
print("2. merged hints -> planned -> executed DONE")
sessions.close()
db.close()
print(f"ledger used: {ledger.used_tokens} tokens")
print("REPLAY OK")
