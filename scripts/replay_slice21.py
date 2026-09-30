"""Slice-21 V1 acceptance harness (fixture-only live browser), slice-22 live submit.

Scenarios (one shared audit log, per-scenario DBs, one browser session):
  A. docs-search   "List my pending courses" -> DONE/2, decider never called.
  B1 portal-submit approve -> DONE/3 via real approval-token path + LIVE
     submit executor (page-state change proves real execution, no twin).
  B2 portal-submit deny    -> STOPPED + task CANCELLED, page untouched.
  C1 guard pressure -> GUARD_TRIP(pause) -> PAUSED -> relief+resume -> DONE.
  C2 token exhaustion -> GUARD_TRIP(stop) -> STOPPED.
  D. audit replay: fresh re-read of the log must re-derive the live table.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\replay_slice21.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.budgets import TokenLedger  # noqa: E402
from lakra.control.guards import Guards  # noqa: E402
from lakra.control.loop import ScriptedDecider, TaskLoop  # noqa: E402
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
from lakra.resources.monitor import SystemSnapshot  # noqa: E402

VAR = ROOT / "var"
LOG = VAR / "audit-slice21.jsonl"
FIXTURE = (ROOT / "tests" / "fixtures" / "courses.html").as_uri()
if LOG.exists():
    LOG.unlink()
for old in ("slice21-A.db", "slice21-B1.db", "slice21-B2.db",
            "slice21-C1.db", "slice21-C2.db"):
    p = VAR / old
    if p.exists():
        p.unlink()

A_HINTS = {"url": FIXTURE, "expect_text": "pending"}
B_HINTS = {"url": FIXTURE, "selector": "#echo", "text": "hi",
           "submit_selector": "#toggle"}


class ScriptedMonitor:
    """Healthy or starved on demand."""

    def __init__(self, mode="healthy"):
        self.mode = mode

    def sample(self, force=False):
        starved = self.mode == "starved"
        return SystemSnapshot(
            ts=time.time(), ram_total_mb=16384,
            ram_available_mb=64 if starved else 8192,
            cpu_percent=99.0 if starved else 5.0,
            lakra_rss_mb=50.0, browser_rss_mb=0.0, gpu_available=False)

    def under_pressure(self, **kw):
        if self.mode == "starved":
            return True, "low RAM: 64 MB available"
        return False, "ok"


def rig(dbname, monitor, ledger_for=None):
    """Harness-only wiring: existing pieces, no new authority. The harness
    calls TaskLoop/Runner; it never transitions tasks or mints tokens."""
    db = Database(VAR / dbname)
    registry = TaskRegistry(store=db)
    audit = AuditLog(LOG)
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    for kind in ("browser.navigate", "browser.snapshot", "browser.screenshot"):
        tools.register(kind, OBS)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait", "browser.submit"):
        tools.register(kind, HANDS)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, HANDS, audit, sched, observer=OBS)
    guards = Guards(scheduler=sched, ledger_for=ledger_for, monitor=monitor)
    runner = Runner(ctrl, sched, audit,
                    observe=lambda: HANDS.page.locator("body").inner_text(),
                    store=db, guards=guards)
    loop = TaskLoop(runner, approvals, audit)
    ns = {"db": db, "registry": registry, "audit": audit, "sched": sched,
          "approvals": approvals, "router": router, "ctrl": ctrl,
          "guards": guards, "runner": runner, "loop": loop}
    return ns


def start(ns, goal):
    r, s = ns["registry"], ns["sched"]
    task = r.add(Task.create(
        goal, allowed_tools=["browser"], allowed_domains=["file:"],
        allowed_paths=[str(ROOT)]))
    r.checkout(task.task_id, "exec")
    r.set_status(task.task_id, Status.RUNNING)
    s.enqueue(task.task_id)
    return task


def derive_rows(events):
    """Re-derive the verdict table from audit events alone."""
    rows = {}

    def row(tid):
        return rows.setdefault(
            tid, {"plan_status": "-", "steps_done": 0, "guard_trips": 0,
                  "approval": "none", "task_status": "?"})

    for e in events:
        tid, ty, p = e["task_id"], e["type"], e.get("payload", {})
        if tid == "-":
            continue
        r = row(tid)
        if ty == "PLAN_OUTCOME":
            r["plan_status"] = p["status"]
            r["steps_done"] = p["steps_done"]
            if p["status"] == "PAUSED":
                r["task_status"] = "PAUSED"
        elif ty == "GUARD_TRIP":
            r["guard_trips"] += 1
        elif ty == "ACTION_REQUIRES_APPROVAL":
            r["approval"] = "pending"
        elif ty == "APPROVAL_CONSUMED":
            r["approval"] = "approved"
        elif ty == "TASK_LIFECYCLE":
            r["task_status"] = p["task_status"]
        elif ty == "TASK_STALLED":
            r["task_status"] = "RUNNING"
        elif ty == "TASK_CANCELLED":
            r["task_status"] = "CANCELLED"
    for r in rows.values():
        # Denial parks then CANCELs via approvals.decide (no audit of its
        # own); the loop's STOPPED/"human denied" outcome is the marker.
        if r["approval"] == "pending" and r["plan_status"] == "STOPPED":
            r["approval"] = "denied"
            r["task_status"] = "CANCELLED"
    return rows


SESSIONS = BrowserSessions(VAR / "slice21-profile")
SESSIONS.launch()
HANDS = BrowserActions(SESSIONS)
OBS = BrowserObserver(SESSIONS, VAR / "slice21-shots")

live = {}  # scenario -> row, recorded from RunResults + registry reads
fails = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def live_row(ns, tag, task_id, res):
    live[tag] = {"plan_status": res.status, "steps_done": res.steps_done,
                 "guard_trips": sum(
                     1 for e in ns["audit"].replay()
                     if e["type"] == "GUARD_TRIP"
                     and e["task_id"] == task_id),
                 "approval": ("approved"
                              if any(e["type"] == "APPROVAL_CONSUMED"
                                      and e["task_id"] == task_id
                                      for e in ns["audit"].replay())
                              else ("denied"
                                    if "denied" in (res.detail or "")
                                    else "none")),
                 "task_status": ns["registry"].get(task_id).status.value}


# A. docs-search -------------------------------------------------------------
print("A. docs-search (no per-click direction)")
ns = rig("slice21-A.db", ScriptedMonitor("healthy"))
seen = []
decider = ScriptedDecider(True)
decider.decide = lambda item: (seen.append(item), True)[1]
t = start(ns, "List my pending courses")
res = ns["loop"].run_goal(t, dict(A_HINTS), decider)
check("A DONE/2", (res.status, res.steps_done) == ("DONE", 2), str(res))
check("A decider never consulted", seen == [], f"seen={len(seen)}")
check("A zero guard trips",
      not [e for e in ns["audit"].replay()
           if e["type"] == "GUARD_TRIP" and e["task_id"] == t.task_id])
check("A all verifications PASS",
      all(e["payload"].get("result") == "PASS"
          for e in ns["audit"].replay()
          if e["type"] == "VERIFICATION" and e["task_id"] == t.task_id))
live_row(ns, "A", t.task_id, res)
ns["db"].close()

# B1. portal-submit, approved ------------------------------------------------
print("B1. portal-submit (approve -> DONE via approval token)")
ns1 = rig("slice21-B1.db", ScriptedMonitor("healthy"))
t1 = start(ns1, "Submit the demo form")
res1 = ns1["loop"].run_goal(t1, dict(B_HINTS), ScriptedDecider(True))
check("B1 DONE/3", (res1.status, res1.steps_done) == ("DONE", 3), str(res1))
check("B1 token consumed once",
      sum(1 for e in ns1["audit"].replay()
          if e["type"] == "APPROVAL_CONSUMED"
          and e["task_id"] == t1.task_id) == 1)
check("B1 live submit changed page state",
      HANDS.page.locator("#status").inner_text() == "clicked")
live_row(ns1, "B1", t1.task_id, res1)
ns1["db"].close()

# B2. portal-submit, denied --------------------------------------------------
print("B2. portal-submit (deny -> STOPPED/CANCELLED, submit never runs)")
ns2 = rig("slice21-B2.db", ScriptedMonitor("healthy"))
t2 = start(ns2, "Submit the demo form")
res2 = ns2["loop"].run_goal(t2, dict(B_HINTS), ScriptedDecider(False))
check("B2 STOPPED denied", res2.status == "STOPPED"
      and "denied" in res2.detail, str(res2))
check("B2 task CANCELLED",
      ns2["registry"].get(t2.task_id).status == Status.CANCELLED)
check("B2 submit never executed",
      HANDS.page.locator("#status").inner_text() == "not clicked")
check("B2 nothing consumed",
      not [e for e in ns2["audit"].replay()
           if e["type"] == "APPROVAL_CONSUMED"
           and e["task_id"] == t2.task_id])
live_row(ns2, "B2", t2.task_id, res2)
ns2["db"].close()

# C1. pressure -> PAUSED -> relief -> DONE ------------------------------------
print("C1. resource pressure (pause -> resume -> DONE)")
mon = ScriptedMonitor("starved")
nsc = rig("slice21-C1.db", mon)
tc = start(nsc, "List my pending courses")
plan_c = Planner().plan(tc, dict(A_HINTS))
resc = nsc["runner"].run(plan_c, hints=dict(A_HINTS))
check("C1 PAUSED at step 0", resc.status == "PAUSED"
      and resc.steps_done == 0, str(resc))
check("C1 task PAUSED",
      nsc["registry"].get(tc.task_id).status == Status.PAUSED)
mon.mode = "healthy"
nsc["sched"].resume(tc.task_id)
outc = nsc["runner"].resume(plan_c.plan_id,
                            observe=lambda: "fresh snapshot")
check("C1 relief+resume DONE", (outc.status, outc.steps_done) == ("DONE", 2),
      str(outc))
live_row(nsc, "C1", tc.task_id, outc)
nsc["db"].close()

# C2. token exhaustion -> STOPPED ----------------------------------------------
print("C2. token exhaustion (stop, fail-closed)")
spent = TokenLedger(4)
spent.charge(4)
nse = rig("slice21-C2.db", ScriptedMonitor("healthy"),
          ledger_for=lambda tid: spent)
te = start(nse, "List my pending courses")
rese = nse["runner"].run(Planner().plan(te, dict(A_HINTS)),
                         hints=dict(A_HINTS))
check("C2 STOPPED tokens", rese.status == "STOPPED"
      and "tokens" in rese.detail, str(rese))
live_row(nse, "C2", te.task_id, rese)
nse["db"].close()
SESSIONS.close()

# D. audit replay equality ------------------------------------------------------
print("D. audit replay (re-derive table from a fresh read)")
fresh = AuditLog(LOG).replay()
replayed = derive_rows(fresh)
table_ok = True
for tag, task in (("A", t.task_id), ("B1", t1.task_id), ("B2", t2.task_id),
                  ("C1", tc.task_id), ("C2", te.task_id)):
    same = replayed.get(task) == live[tag]
    table_ok &= same
    check(f"D row {tag} replay==live", same,
          f"live={live[tag]} replay={replayed.get(task)}")

print("\nverdict table (live):")
for tag in ("A", "B1", "B2", "C1", "C2"):
    print(f"  {tag}: {live[tag]}")
if fails:
    print(f"\nACCEPTANCE FAIL: {fails}")
    raise SystemExit(1)
print("\nACCEPTANCE PASS")
