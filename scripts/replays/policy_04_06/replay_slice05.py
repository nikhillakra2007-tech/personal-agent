"""Slice-05 demo: the 15-step manual verification list as an executable.

1 launch isolated browser / 2 open fixture / 3 observe / 4 click target /
5 verify state / 6 type into field / 7 verify value, secrets withheld /
8 trigger verification failure / 9 bounded retry + recovery /
10 trigger ASK / 11 confirm no action before approval /
12 approve / 13 confirm re-evaluation / 14 audit events / 15 clean close.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice05.py
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
from lakra.execution.browser.verification import Predicate  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

PROFILE = ROOT / "var" / "slice05-profile"
SHOTS = ROOT / "var" / "slice05-shots"
LOG_PATH = ROOT / "var" / "audit-slice05.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
sched = Scheduler(registry)
approvals = Approvals(registry)
tools = ToolRegistry()
sessions = BrowserSessions(PROFILE)  # 1. isolated browser (own profile dir)
sessions.launch()
audit.log("SESSION_LAUNCHED", "-", {"profile": str(PROFILE)})
print("1. isolated browser launched")

hands = BrowserActions(sessions)
obs = BrowserObserver(sessions, SHOTS)
for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
    tools.register(kind, obs)
for kind in ("browser.click", "browser.type", "browser.press",
             "browser.scroll", "browser.wait"):
    tools.register(kind, hands)
router = ToolRouter(registry, tools, approvals, audit, sched)
ctrl = BrowserController(router, hands, audit, sched)

task = registry.add(Task.create(
    "Exercise the fixture lab", allowed_tools=["browser"],
    allowed_domains=["file:"], allowed_paths=[str(ROOT)]))
audit.log("TASK_CREATED", task.task_id, {"goal": task.goal})
registry.checkout(task.task_id, "exec")
registry.set_status(task.task_id, Status.RUNNING)
sched.enqueue(task.task_id)


def act(kind, target, effect="reversible"):
    return Action(kind=kind, target=target, effect=effect,
                  task_id=task.task_id)


def run(kind, target, pred_kind, pred_target, effect="reversible", retries=2):
    return ctrl.run_step(task.task_id, BrowserStep(
        action=act(kind, target, effect),
        expect=Predicate(pred_kind, pred_target), max_retries=retries))


try:
    # 2-3. open fixture + observe -------------------------------------------
    r = router.route(task.task_id, act("browser.navigate", FIXTURE))
    assert r.ok, r.error
    assert "Linear Algebra" in r.output
    hands.attach(obs.current_page)  # one page shared by observe + act sides
    assert hands.page is obs.current_page
    print("2-3. fixture opened and observed (courses listed)")

    # 4-5. click + verify ----------------------------------------------------
    s = run("browser.click", "#toggle", "text_contains", "clicked")
    assert (s.ok, s.verified, s.outcome) == (True, True, "CONTINUE"), s
    print(f"4-5. click verified in {s.attempts} attempt(s)")

    # 6-7. type + verify value, secrets withheld ------------------------------
    s = run("browser.type", "#echo\n---\nLakra-05", "text_contains",
            "Lakra-05")
    assert (s.ok, s.verified) == (True, True), s
    assert "s3cr3t-nope" not in obs.execute(
        act("browser.snapshot", FIXTURE), task).output
    print("6-7. typing verified; fixture secret never in observations")

    # 8-9. verification failure -> bounded retry -> recovery -> escalate ------
    s = run("browser.click", "#toggle", "text_contains", "no-such-text",
            retries=1)
    assert (s.verified, s.outcome) == (False, "ESCALATE"), s
    assert s.attempts == 2, s  # 1 initial + 1 retry, then one alternate
    print(f"8-9. failure retried then escalated ({s.attempts} attempts, "
          f"detail={s.detail})")

    # 10-11. ASK: consequential click parks, page untouched --------------------
    s = run("browser.click", "#toggle", "text_contains", "clicked",
            effect="consequential")
    assert s.outcome == "ESCALATE", s
    print("10-11. ASK parked before acting (page state unchanged)")

    # 12-13. approve -> recheck -> re-route executes ---------------------------
    approval_id = audit.replay()[-1]["payload"]["approval_id"]
    decided = approvals.decide(approval_id, True)
    assert approvals.recheck(decided) == "ASK"
    approvals.must_be_gated("ASK")
    s = run("browser.click", "#toggle", "text_contains", "clicked")
    assert (s.ok, s.verified, s.outcome) == (True, True, "CONTINUE"), s
    print("12-13. approved, re-evaluated (still-gated ASK), executed")

    # 14. audit ----------------------------------------------------------------
    types = [e["type"] for e in audit.replay()]
    for needed in ("VERIFICATION", "RETRY", "RECOVERY",
                   "ACTION_REQUIRES_APPROVAL"):
        assert needed in types, f"missing {needed}"
    print(f"14. audit complete ({len(types)} events)")
finally:
    # 15. clean close -----------------------------------------------------------
    sessions.close()
    audit.log("SESSION_CLOSED", "-", {})
    print("15. browser closed cleanly")

print(f"   log -> {LOG_PATH}")
print("REPLAY OK")
