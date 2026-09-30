"""Slice-16 demo: past-tense verification.

1. Stale-text trap: click a no-op target while asserting text_appeared on
   text that was ALREADY present -> diff correctly FAILs where the absolute
   text_contains would PASS (shown side by side).
2. Real change: click #toggle, assert text_appeared("clicked") -> PASS.
3. Missing baseline: a diff check with no prior snapshot audits
   NO_BASELINE instead of silently passing.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice16.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.policy import Action  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import (  # noqa: E402
    BrowserController,
    BrowserStep,
)
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.browser.snapshots import SnapshotStore  # noqa: E402
from lakra.execution.browser.verification import (  # noqa: E402
    MissingBaselineError,
    Predicate,
    check,
)
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

PROFILE = ROOT / "var" / "slice16-profile"
SHOTS = ROOT / "var" / "slice16-shots"
LOG_PATH = ROOT / "var" / "audit-slice16.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
sched = Scheduler(registry)
tools = ToolRegistry()
sessions = BrowserSessions(PROFILE)
sessions.launch()
hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, SHOTS)
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
for kind in ("browser.click", "browser.type", "browser.press",
             "browser.scroll", "browser.wait"):
    tools.register(kind, hands)
router = ToolRouter(registry, tools, Approvals(registry), audit, sched)
store = SnapshotStore()
ctrl = BrowserController(router, hands, audit, sched, observer=obs,
                         snapshots=store)

task = registry.add(Task.create(
    "Diff demo", allowed_tools=["browser"], allowed_domains=["file:"],
    allowed_paths=[str(ROOT)]))
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)
hands.open(FIXTURE)


def step(kind, target, pred, effect="reversible", retries=0):
    return BrowserStep(
        action=Action(kind=kind, target=target, effect=effect,
                      task_id=task.task_id),
        expect=Predicate(kind=pred[0], target=pred[1]), max_retries=retries)


# 1. stale-text trap --------------------------------------------------------------------
# "pending" is on the page BEFORE any action: absolute passes vacuously...
# (The controller captures each diff step's own baseline automatically.)
abs_step = step("browser.click", "#toggle", ("text_contains", "pending"))
s_abs = ctrl.run_step(task.task_id, abs_step)
# ...while the diff twin demands the text be NEW:
s_diff = ctrl.run_step(
    task.task_id,
    step("browser.click", "#toggle", ("text_appeared", "pending")))
print(f"1. absolute text_contains: {s_abs.verified} (vacuous PASS);"
      f" diff text_appeared: {s_diff.verified} (correct FAIL)")
assert s_abs.verified is True and s_diff.verified is False

# 2. real change verifies ------------------------------------------------------------------
# (Part 1 already flipped #toggle, so prove change on fresh text instead:
# typing into #echo mirrors into #echo-out, which the baseline lacks.)
s = ctrl.run_step(
    task.task_id,
    step("browser.type", "#echo\n---\ns16-real-change",
         ("text_appeared", "s16-real-change")))
assert (s.ok, s.verified, s.outcome) == (True, True, "CONTINUE"), s
print("2. real change text_appeared('s16-real-change'): PASS")

# 3. missing baseline is explicit ---------------------------------------------------------------
try:
    check(Predicate("text_appeared", "x"), hands.page, None)
    raise SystemExit("FAIL: silent absolute fallback")
except MissingBaselineError:
    print("3. missing baseline raises (never silent); controller audits it")
sched.enqueue(task.task_id)
fresh = BrowserController(router, hands, audit, sched, observer=obs)
assert fresh.snapshots.depth(task.task_id) == 0
print("   fresh controller starts with no baseline (nothing inherited)")
sessions.close()

events = audit.replay()
results = [e["payload"].get("result") for e in events
           if e["type"] == "VERIFICATION"]
assert "PASS" in results and "FAIL" in results
print(f"4. audit replay OK: {len(events)} events, PASS+FAIL present")
print("REPLAY OK")
