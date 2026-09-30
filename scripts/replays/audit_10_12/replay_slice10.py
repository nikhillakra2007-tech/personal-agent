"""Slice-10 demo: measured model proposals with deterministic fallback.

Uses the LIVE model only if the Ollama daemon answers; otherwise a recorded
fake (suite stays green without the daemon either way). Shows: attempt
measurement separate from plan outcome, fallback on rejection, and — when
the model proposes a valid fixture step — parity execution through the
normal policy path.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice10.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.budgets import TokenLedger  # noqa: E402
from lakra.control.planner import Planner  # noqa: E402
from lakra.control.proposing import ProposingPlanner  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import BrowserController  # noqa: E402
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.models.base import ModelProvider, ModelResponse  # noqa: E402
from lakra.models.local import LocalProvider  # noqa: E402

LOG_PATH = ROOT / "var" / "audit-slice10.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
live = LocalProvider("qwen2.5:0.5b")
if live.available():
    provider, source = live, "live qwen2.5:0.5b"
else:
    class _Recorded(ModelProvider):
        @property
        def name(self):
            return "recorded/fixture-demo"

        def complete(self, prompt, *, budget_tokens):
            text = json.dumps({
                "tool_kind": "browser.snapshot",
                "target": FIXTURE,
                "effect": "read",
                "expect_kind": "text_contains",
                "expect_target": "pending",
                "rationale": "recorded valid proposal"})
            return ModelResponse(text=text, prompt_tokens=10,
                                 completion_tokens=40)

    provider, source = _Recorded(), "recorded fixture (daemon down)"
print(f"provider: {source}")

registry = TaskRegistry()
sched = Scheduler(registry)
tools = ToolRegistry()
sessions = BrowserSessions(ROOT / "var" / "slice10-profile")
sessions.launch()
hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, ROOT / "var" / "slice10-shots")
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
for kind in ("browser.click", "browser.type", "browser.press",
             "browser.scroll", "browser.wait"):
    tools.register(kind, hands)
router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
ctrl = BrowserController(router, hands, audit, sched, observer=obs)
runner = Runner(ctrl, sched, audit,
                observe=lambda: hands.page.locator("body").inner_text())

task = registry.add(Task.create(
    "Observe the demo courses page", allowed_tools=["browser"],
    allowed_domains=["file:"], allowed_paths=[str(ROOT)]))
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)
ledger = TokenLedger(2000)
pp = ProposingPlanner(Planner(), provider, ledger=ledger)

plan, attempt = pp.propose(task, "page shows courses", {"url": FIXTURE,
                                                        "expect_text": "pending"})
print(f"attempt: parsed={attempt.parsed} verdict={attempt.verdict} "
      f"tokens={attempt.tokens_used} fallback={attempt.fallback_used} "
      f"reason={attempt.rejection_reason}")
audit.log("PROPOSAL_MEASURED", task.task_id,
          {"model": attempt.model_name, "parsed": attempt.parsed,
           "verdict": attempt.verdict,
           "fallback_used": attempt.fallback_used})
hands.open(FIXTURE)
# Navigate through the router so obs owns the page, then share it.
from lakra.control.policy import Action  # noqa: E402
router.route(task.task_id, Action(
    kind="browser.navigate", target=FIXTURE, effect="reversible",
    task_id=task.task_id))
hands.attach(obs.current_page)
res = runner.run(plan)
print(f"run: {res.status} steps={res.steps_done} ({res.detail})")
sessions.close()
print(f"ledger used: {ledger.used_tokens} tokens")
print(f"log -> {LOG_PATH}")
print("REPLAY OK")
