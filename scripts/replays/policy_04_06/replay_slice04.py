"""Slice-04 demo: read-only observation through the router choke point.

Navigates a local fixture page (no network), snapshots it, screenshots it,
proves credential redaction, parks an evil-domain navigate at policy, and
closes cleanly. Run from the project root:

    .\\.venv\\Scripts\\python.exe scripts\\replay_slice04.py
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
from lakra.execution.browser.observer import BrowserObserver  # noqa: E402
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402

PROFILE = ROOT / "var" / "slice04-profile"
SHOTS = ROOT / "var" / "slice04-shots"
LOG_PATH = ROOT / "var" / "audit-slice04.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG_PATH.exists():
    LOG_PATH.unlink()

audit = AuditLog(LOG_PATH)
registry = TaskRegistry()
router = ToolRouter(registry, ToolRegistry(), Approvals(registry), audit,
                    Scheduler(registry))

sessions = BrowserSessions(PROFILE)
sessions.launch()
audit.log("SESSION_LAUNCHED", "-", {"profile": str(PROFILE)})
try:
    obs = BrowserObserver(sessions, SHOTS)
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        router.tools.register(kind, obs)

    task = registry.add(Task.create(
        "List the demo portal courses",
        allowed_tools=["browser"], allowed_domains=["file:"],
        allowed_paths=[str(ROOT)]))
    audit.log("TASK_CREATED", task.task_id, {"goal": task.goal})
    registry.checkout(task.task_id, "exec")
    registry.set_status(task.task_id, Status.RUNNING)

    def act(kind, target, effect="read"):
        return Action(kind=kind, target=target, effect=effect,
                      task_id=task.task_id)

    # 1. navigate (L1) + snapshot (L0): courses visible, secret withheld -----
    res = router.route(task.task_id,
                       act("browser.navigate", FIXTURE, "reversible"))
    assert res.ok, res.error
    assert "Linear Algebra" in res.output and "pending" in res.output
    assert "s3cr3t-nope" not in res.output
    assert "CREDENTIAL FIELD" in res.output
    print("1. navigate+snapshot OK (courses listed, credential redacted)")

    res = router.route(task.task_id, act("browser.snapshot", FIXTURE))
    assert res.ok and "Thermodynamics" in res.output
    print("2. snapshot OK (no new navigation needed)")

    # 3. screenshot lands on disk; only the path enters history/audit --------
    res = router.route(task.task_id, act("browser.screenshot", FIXTURE))
    assert res.ok
    shot_line = [ln for ln in res.output.splitlines()
                 if ln.startswith("screenshot: ")][0]
    shot = Path(shot_line.split("screenshot: ", 1)[1])
    assert shot.exists() and shot.stat().st_size > 0
    print(f"3. screenshot OK ({shot.name}, {shot.stat().st_size} bytes)")

    # 4. evil domain parks at policy: zero browser traffic for it ------------
    parked = router.route(task.task_id, act(
        "browser.navigate", "https://evil.example/courses", "reversible"))
    assert not parked.ok and "PARKED" in parked.error
    print("4. evil-domain navigate parked (policy-first proven)")

    assert task.verification.state == "UNVERIFIED"  # no verifier until s05
finally:
    sessions.close()
    audit.log("SESSION_CLOSED", "-", {})

events = audit.replay()
assert any(e["type"] == "SESSION_LAUNCHED" for e in events)
assert any(e["type"] == "SESSION_CLOSED" for e in events)
print(f"5. audit replay OK: {len(events)} events -> {LOG_PATH}")
print(f"   inspect the PNG yourself: {shot}")
print("REPLAY OK")
