"""Slice-36 thin browser CLI: one composed browser task, end to end.

Shapes explicit flags into a run_task() union goal and runs it once
through the real stack (registry/scheduler/router + browser
observer/hands on an isolated Playwright profile + guards). The CLI
names the road via --road and shapes keys; run_task() re-validates by
key presence and routes â€” defense in depth, no inference anywhere.

Deciders (same contract as lakra_run.py):
  --yes / --no : scripted answer (tests, demos)
  --poll SECS  : cross-process decider (approve.py decides, fail-closed)
  (no flag)    : interactive y/n prompt â€” the ONLY TTY touchpoint.

Exit codes (parallel to lakra_run.py):
  0 : road DONE (RunResult or LoopResult)
  2 : human-denied approval at an L3 gate
  1 : usage error, refusal, or any other STOPPED outcome

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road follow
        --url <page> --text "Follow the Beta record"
        --expect "detail record: Beta" [--yes | --no | --poll SECS]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road loop
        --url <page> --text "Follow every batch record"
        --expect "detail record" --max-items 3 --max-iters 3 [--yes]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road form
        --url <page> --slots-json '{"name": {"text": "Ada"}}'
        --submit "#m-submit" [--yes | --no | --poll SECS]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road observe
        --url <page> --text "Records index" --expect "Records"
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road search
        --url <search page> --query "cathedrals" --submit "#q-go"
        --expect "Cathedral results" [--yes | --no | --poll SECS]

Natural-language goal shaping (slice-46/47: deterministic prose -> road
goal dict; observe, follow, and search shape, all other intents refuse
with guidance; no model calls, policy untouched):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Show me the records page at file:///l.html" [--yes]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Follow the Beta record on file:///l.html and show me the
        detail record" [--yes | --no | --poll SECS]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Web search for cats at file:///s.html and confirm
        results for cats" [--yes | --no | --poll SECS]

Chained multi-template runs (slice-45: sequential supervised legs,
one task; loop legs refused; first non-DONE leg ends the chain):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --chain-file chain.json [--from-leg N] [--yes | --no | --poll SECS]

Chained multi-template runs (one task, sequential supervised legs):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --chain-file chain.json [--from-leg N] [--yes | --no | --poll SECS]

Resume a stranded task after process death (explicit per-task only;
no auto-resume, no daemon):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --resume TASK_ID
        [--yes | --no | --poll SECS]

Read-only visibility (no browser, no writes, no deciders):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --list
        [--state QUEUED|RUNNING|PAUSED|WAITING_APPROVAL|COMPLETED|FAILED|CANCELLED|all]
        [--format text|json]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --status TASK_ID
        [--last N] [--format text|json]

Env/overrides (tests, portability):
    LAKRA_DB    : database path (else the shared local lakra.db)
    --audit PATH, --profile-dir PATH, --shots-dir PATH
    --ram-floor-mb N, --cpu-ceiling PCT: guard pressure floors
      (defaults 2048 MB / 90 pct, the monitor defaults; lowered only
      for loaded test boxes, never raised silently)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.guards import Guards  # noqa: E402
from lakra.control import task_store  # noqa: E402
from lakra.control.resume import ResumeRefused, resume_info  # noqa: E402
from lakra.control.loop import (  # noqa: E402
    PollingDecider,
    ScriptedDecider,
    TaskLoop,
)
from lakra.control.loops import run_task  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.runner import Runner  # noqa: E402
from lakra.control.scheduler import Scheduler  # noqa: E402
from lakra.control.store import Database  # noqa: E402
from lakra.control.tasks import Status, Task  # noqa: E402
from lakra.execution.browser.actions import BrowserActions  # noqa: E402
from lakra.execution.browser.controller import (  # noqa: E402
    BrowserController,
)
from lakra.execution.browser.observer import (  # noqa: E402
    BrowserObserver,
    collect_controls,
    collect_links,
)
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.resources.monitor import ResourceMonitor  # noqa: E402

USAGE = (__doc__ or "").strip()

ROADS = ("follow", "loop", "form", "observe", "search")


class UsageError(ValueError):
    """Flag shape violation. Fail-closed before anything launches."""


def parse_args(argv: list[str]) -> dict:
    """Pure flag parser. Repeatable --allow-domain accumulates."""
    opts: dict = {"allow_domains": []}
    i = 0
    take_value = {"--road", "--url", "--text", "--expect", "--max-items",
                  "--max-iters", "--slots-json", "--submit", "--goal",
                  "--poll", "--audit", "--profile-dir", "--shots-dir",
                  "--allow-domain", "--ram-floor-mb", "--cpu-ceiling",
                  "--resume", "--status", "--state", "--last", "--format",
                  "--query", "--chain-file", "--from-leg"}
    flags = {"--yes", "--no", "--list"}
    while i < len(argv):
        tok = argv[i]
        if tok in flags:
            opts[tok[2:]] = True
            i += 1
        elif tok in take_value:
            if i + 1 >= len(argv):
                raise UsageError(f"{tok} needs a value")
            key = tok[2:].replace("-", "_")
            if tok == "--allow-domain":
                opts["allow_domains"].append(argv[i + 1])
            else:
                opts[key] = argv[i + 1]
            i += 2
        else:
            raise UsageError(f"unknown flag {tok!r}")
    return opts


def _forbid(opts: dict, road: str, *names: str) -> None:
    present = [n for n in names if opts.get(n) is not None]
    if present:
        raise UsageError(
            f"--road {road} takes no {', '.join('--' + p.replace('_', '-') for p in present)}")


def _need(opts: dict, road: str, *names: str) -> None:
    missing = [n for n in names if not opts.get(n)]
    if missing:
        raise UsageError(
            f"--road {road} needs {', '.join('--' + m.replace('_', '-') for m in missing)}")


def _parse_int(opts: dict, name: str) -> int:
    try:
        value = int(opts[name])
    except (TypeError, ValueError):
        raise UsageError(f"--{name.replace('_', '-')} must be an integer")
    return value


def build_goal(opts: dict) -> dict:
    """Pure shaper: flags -> run_task() union goal. Raises UsageError.

    Shapes exactly one road's key set; cross-road flag mixes are usage
    errors here (nothing launches), mirroring the dispatcher's own
    mixed-keys refusal one layer down.
    """
    if not isinstance(opts, dict):
        raise UsageError("options must be a mapping")
    road = opts.get("road")
    if road not in ROADS:
        raise UsageError(
            "--road must be one of follow|loop|form|observe|search")
    url = opts.get("url")
    if not url:
        raise UsageError(f"--road {road} needs --url")
    if road == "follow":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit")
        _need(opts, road, "text", "expect")
        return {"list_url": url, "goal_text": opts["text"],
                "body_expect": opts["expect"]}
    if road == "loop":
        _forbid(opts, road, "slots_json", "submit")
        _need(opts, road, "text", "expect", "max_items", "max_iters")
        return {"list_url": url, "goal_text": opts["text"],
                "body_expect": opts["expect"],
                "max_items": _parse_int(opts, "max_items"),
                "max_iters": _parse_int(opts, "max_iters")}
    if road == "observe":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "query")
        _need(opts, road, "text", "expect")
        return {"url": url, "expect_text": opts["expect"]}
    if road == "search":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "text")
        _need(opts, road, "query", "submit", "expect")
        return {"search_url": url, "query": {"text": opts["query"]},
                "submit_selector": opts["submit"],
                "expect_text": opts["expect"]}
    _forbid(opts, road, "text", "expect", "max_items", "max_iters")
    _need(opts, road, "slots_json", "submit")
    try:
        slots = json.loads(opts["slots_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise UsageError(f"--slots-json must be valid JSON ({exc})")
    if not isinstance(slots, dict) or not slots:
        raise UsageError("--slots-json must be a non-empty object")
    return {"form_url": url, "goal_slots": slots,
            "submit_selector": opts["submit"]}


def default_task_goal(road: str, opts: dict) -> str:
    if opts.get("goal"):
        return opts["goal"]
    if road in ("follow", "loop"):
        return f"Follow {opts.get('text', 'the link')}"
    if road == "observe":
        return f"Observe {opts.get('text', 'the page')}"
    if road == "search":
        return f"Web search {opts.get('query', 'the query')}"
    return "Submit the form"


class CLIMonitor(ResourceMonitor):
    """ResourceMonitor with CLI-overridable pressure floors.

    Defaults equal the monitor defaults (genuine guard behavior); the
    CLI passes explicit floors only so loaded test boxes can run the
    smoke suite deterministically. Scripts-side only; src/ untouched.
    """

    def __init__(self, ram_floor_mb: int = 2048,
                 cpu_ceiling: float = 90.0) -> None:
        super().__init__()
        self.ram_floor_mb = ram_floor_mb
        self.cpu_ceiling = cpu_ceiling

    def under_pressure(self, **kw):
        return super().under_pressure(
            ram_floor_mb=self.ram_floor_mb,
            cpu_ceiling=self.cpu_ceiling, **kw)


def monitor_for(opts: dict) -> ResourceMonitor:
    try:
        floor = int(opts.get("ram_floor_mb", 2048))
    except (TypeError, ValueError):
        raise UsageError("--ram-floor-mb must be an integer")
    try:
        ceiling = float(opts.get("cpu_ceiling", 90.0))
    except (TypeError, ValueError):
        raise UsageError("--cpu-ceiling must be a number")
    return CLIMonitor(ram_floor_mb=floor, cpu_ceiling=ceiling)


def pick_decider(args: list[str], approvals: Approvals,
                 task_id: str):
    if "--yes" in args:
        return ScriptedDecider(True)
    if "--no" in args:
        return ScriptedDecider(False)
    if "--poll" in args:
        i = args.index("--poll")
        try:
            timeout = float(args[i + 1])
        except (IndexError, ValueError):
            raise UsageError("--poll needs a numeric timeout in seconds")
        return PollingDecider(approvals, task_id, timeout_s=timeout)
    try:
        answer = input("approve once? [y/n] ").strip().lower()
    except EOFError:
        answer = "n"
    return ScriptedDecider(answer == "y")


def build_stack(sessions, db, audit_path, shots_dir, monitor):
    """Assemble the real browser stack on live sessions + database.

    The exact construction the CLI always used; shared by the fresh
    and resume paths so both run the identical policy/guard/router
    machinery.
    """
    registry = TaskRegistry(store=db)
    audit = AuditLog(audit_path)
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    hands = BrowserActions(sessions)
    obs = BrowserObserver(sessions, shots_dir)
    for kind in ("browser.navigate", "browser.snapshot",
                 "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait", "browser.submit",
                 "browser.check", "browser.select"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched,
                             observer=obs)
    guards = Guards(scheduler=sched, monitor=monitor)
    runner = Runner(
        ctrl, sched, audit,
        observe=lambda: hands.page.locator("body").inner_text(),
        store=db, guards=guards)
    taskloop = TaskLoop(runner, approvals, audit)
    return {"db": db, "registry": registry, "audit": audit, "sched": sched,
            "approvals": approvals, "router": router, "ctrl": ctrl,
            "guards": guards, "runner": runner, "taskloop": taskloop,
            "hands": hands}


def finish(res, road, task_id, registry, audit_path) -> int:
    findings = getattr(res, "findings", None)
    steps = getattr(res, "steps_done", None)
    if steps is None:
        steps = getattr(res, "iters_done", "?")
    print(f"road: {road}")
    print(f"status: {res.status}")
    print(f"steps: {steps}")
    if findings:
        print(f"findings: {findings}")
    print(f"detail: {res.detail or '(empty)'}")
    print(f"task: {registry.get(task_id).status.value}")
    print(f"audit: {audit_path}")
    if res.status == "DONE":
        return 0
    if res.status == "STOPPED" and "denied" in (res.detail or ""):
        return 2
    return 1


# -- read-only visibility (slice-39) -----------------------------------------

GOAL_CAP = 80
EVENT_VALUE_CAP = 60
AUDIT_TAIL_DEFAULT = 10
LIST_STATES = ("QUEUED", "RUNNING", "PAUSED", "WAITING_APPROVAL",
               "COMPLETED", "FAILED", "CANCELLED")
OPEN_STATES = ("QUEUED", "RUNNING", "PAUSED", "WAITING_APPROVAL")

# Audit payload keys safe to echo (addressing + verdicts). Everything
# else â€” field values, hints, slots, token material â€” is never printed.
EVENT_KEYS = ("kind", "target", "effect", "status", "steps_done",
              "plan_status", "task_status", "plan_id", "approval_id",
              "detail", "reason", "result", "action")


def short_goal(goal: str, cap: int = GOAL_CAP) -> str:
    text = " ".join(str(goal or "").split())
    return text if len(text) <= cap else text[:cap - 3] + "..."


def summarize_event(event: dict) -> str:
    payload = event.get("payload", {}) or {}
    bits = []
    for key in EVENT_KEYS:
        if key in payload and payload[key] not in (None, ""):
            value = " ".join(str(payload[key]).split())
            if len(value) > EVENT_VALUE_CAP:
                value = value[:EVENT_VALUE_CAP - 3] + "..."
            bits.append(f"{key}={value}")
    tail = (" " + " ".join(bits)) if bits else ""
    return f"{event.get('ts', '?')} {event.get('type', '?')}{tail}"


# -- machine-readable visibility (slice-40) ------------------------------------
# Closed schemas: every key below is named explicitly, so a future src/
# field can never leak through output code that doesn't name it. The
# same EVENT_KEYS allowlist + value caps as text mode apply.

TASK_JSON_KEYS = ("task_id", "goal", "status", "owner", "allowed_tools",
                  "allowed_domains", "budget", "created_at", "updated_at")
BUDGET_JSON_KEYS = ("max_steps", "max_tokens_cents", "max_minutes")


def _capped(value):
    text = " ".join(str(value).split())
    if len(text) > EVENT_VALUE_CAP:
        text = text[:EVENT_VALUE_CAP - 3] + "..."
    return text


def task_to_json(task) -> dict:
    """Explicit closed keys: future src/ fields cannot leak through."""
    return {"task_id": task.task_id,
            "goal": str(task.goal or ""),
            "status": task.status.value,
            "owner": task.owner,
            "allowed_tools": list(task.allowed_tools or []),
            "allowed_domains": list(task.allowed_domains or []),
            "budget": {k: getattr(task.budget, k) for k in
                       BUDGET_JSON_KEYS},
            "created_at": task.created_at,
            "updated_at": task.updated_at}


def resume_to_json(info_or_reason) -> dict:
    """resume_info() narrowed to closed JSON keys, mirroring the text
    renderer: blocked units report resumable:false with the reason
    (never a unit line beside a blocker). IDs are full-length â€”
    machines feed them back to --resume."""
    if not isinstance(info_or_reason, dict):
        return {"resumable": False, "reason": str(info_or_reason)}
    info = info_or_reason
    blockers = []
    if info.get("pending_approvals"):
        blockers.append("undecided approval "
                        + ", ".join(info["pending_approvals"]))
    if info.get("uncertain"):
        kinds = ", ".join(u["kind"] for u in info["uncertain"])
        blockers.append(f"uncertain execution ({kinds})")
    if blockers:
        return {"resumable": False, "reason": "; ".join(blockers)}
    out: dict = {"resumable": True, "road": info["road"],
                 "detail": str(info.get("detail", ""))}
    if info["road"] == "loop":
        out.update({"loop_id": info["loop_id"],
                    "loop_status": info["loop_status"],
                    "cursor": info["cursor"],
                    "findings": list(info.get("findings") or [])})
    else:
        out.update({"plan_id": info["plan_id"],
                    "plan_status": info["plan_status"],
                    "first_open": info["first_open"],
                    "steps": info["steps"],
                    "url": info.get("url")})
    return out


def event_to_json(event: dict) -> dict:
    payload = event.get("payload", {}) or {}
    kept: dict = {}
    for key in EVENT_KEYS:
        if key in payload and payload[key] not in (None, ""):
            value = payload[key]
            if isinstance(value, str):
                value = _capped(value)
            elif not isinstance(value, (int, float, bool)):
                continue
            kept[key] = value
    return {"ts": event.get("ts", "?"), "type": event.get("type", "?"),
            "payload": kept}


def render_list_json(tasks) -> str:
    rows = sorted(tasks, key=lambda t: (t.updated_at, t.task_id))
    return json.dumps({"tasks": [task_to_json(t) for t in rows]})


def render_status_json(task, info_or_reason, pending_ids, tail) -> str:
    return json.dumps({"task": task_to_json(task),
                       "resume": resume_to_json(info_or_reason),
                       "pending_approvals": list(pending_ids),
                       "audit_tail": [event_to_json(e) for e in tail]})


def check_format(opts: dict) -> str:
    """Validate --format (text default). Returns the format name."""
    format_name = opts.get("format", "text")
    if format_name not in ("text", "json"):
        raise UsageError("--format must be one of text|json")
    return format_name


def render_list(tasks) -> str:
    """One line per task, oldest first. Pure formatting (no reads)."""
    rows = sorted(tasks, key=lambda t: (t.updated_at, t.task_id))
    if not rows:
        return "no tasks"
    return "\n".join(
        f"{t.task_id}  {t.status.value:<16} {short_goal(t.goal)}"
        f"  owner={t.owner or '-'}  updated={t.updated_at}"
        for t in rows)


def render_status(task, info_or_reason, pending_ids, tail) -> str:
    """Full one-task picture. info_or_reason is the resume_info() dict
    or its refusal text; tail is already task-filtered audit events."""
    lines = [
        f"task: {task.task_id}",
        f"goal: {short_goal(task.goal, 200)}",
        f"status: {task.status.value}  owner={task.owner or '-'}",
        f"scopes: tools={','.join(task.allowed_tools) or '-'}"
        f" domains={','.join(task.allowed_domains) or '-'}",
        f"budget: steps={task.budget.max_steps}"
        f" tokens={task.budget.max_tokens_cents}"
        f" minutes={task.budget.max_minutes}",
        f"updated: {task.updated_at}",
    ]
    if isinstance(info_or_reason, dict):
        info = info_or_reason
        blockers = []
        if pending_ids:
            blockers.append("undecided approval "
                            + ", ".join(pending_ids))
        if info.get("uncertain"):
            kinds = ", ".join(u["kind"] for u in info["uncertain"])
            blockers.append(f"uncertain execution ({kinds})")
        if not blockers:
            if info["road"] == "loop":
                unit = (f"loop={info['loop_id'][:8]}"
                        f" cursor={info['cursor']}"
                        f" findings={len(info['findings'])}")
            else:
                unit = (f"plan={info['plan_id'][:8]}"
                        f" step={info['first_open']}/{info['steps']}")
            lines.append(f"resumable: {info['road']} {unit}"
                         f" ({info['detail']})")
        else:
            lines.append("not resumable: " + "; ".join(blockers))
    else:
        lines.append(f"not resumable: {info_or_reason}")
    if pending_ids:
        lines.append("pending approvals: " + ", ".join(pending_ids))
    else:
        lines.append("pending approvals: none")
    lines.append(f"audit tail ({len(tail)}):")
    lines.extend("  " + summarize_event(e) for e in tail)
    return "\n".join(lines)


def run_list(opts: dict, db_path=None) -> int:
    """List tasks. Pure reads: DB and audit bytes are never written,
    and no browser is launched. Exit 0 even when empty."""
    try:
        format_name = check_format(opts)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    state = opts.get("state")
    if state is None:
        wanted = set(OPEN_STATES)
    else:
        name = str(state).upper()
        if name == "ALL":
            wanted = set(LIST_STATES)
        elif name in LIST_STATES:
            wanted = {name}
        else:
            print(f"usage error: --state must be one of"
                  f" {', '.join(LIST_STATES + ('all',))}")
            return 1
    db = Database(db_path) if db_path is not None else Database()
    try:
        tasks = [t for t in task_store.load_all_tasks(db)
                 if t.status.value in wanted]
    finally:
        try:
            db.close()
        except Exception:
            pass
    if format_name == "json":
        print(render_list_json(tasks))
    else:
        print(render_list(tasks))
    return 0


def run_status(opts: dict, audit_path, db_path=None) -> int:
    """Describe one task: row, resume summary, pending approvals, audit
    tail. Pure reads; see run_list for the no-writes contract."""
    task_id = opts.get("status")
    try:
        format_name = check_format(opts)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    try:
        last = int(opts.get("last", AUDIT_TAIL_DEFAULT))
        if last < 1:
            raise ValueError
    except (TypeError, ValueError):
        print("usage error: --last must be a positive integer")
        return 1
    db = Database(db_path) if db_path is not None else Database()
    try:
        task = task_store.load_task(db, task_id)
        if task is None:
            if format_name == "json":
                print(json.dumps({"error": f"unknown task {task_id}"}))
            else:
                print(f"refused: unknown task {task_id}")
            return 1
        events = AuditLog(audit_path).replay()
        try:
            info = resume_info(db, task_id, events)
        except ResumeRefused as exc:
            info = str(exc)
        approvals = Approvals(TaskRegistry(store=db), store=db)
        pending_ids = [a["approval_id"] for a in approvals.pending()
                       if a["task_id"] == task_id]
        tail = [e for e in events if e.get("task_id") == task_id][-last:]
        if format_name == "json":
            print(render_status_json(task, info, pending_ids, tail))
        else:
            print(render_status(task, info, pending_ids, tail))
        return 0
    finally:
        try:
            db.close()
        except Exception:
            pass


def run_resume(argv, opts, audit_path, profile_dir, shots_dir,
               monitor) -> int:
    """Explicit per-task resume after process death (slice-38).

    Rebuilds the stack, discovers the stranded unit, enforces the
    fail-closed preconditions (no duplicate parks, no blind continues
    past uncertain steps), and resumes through Runner.resume /
    LoopRunner.resume with supervised ASK handling. No auto-resume,
    no daemon: one task, one invocation.
    """
    from lakra.control.resume import ResumeRefused, resume_info, resume_task
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "query",
                 "chain_file", "from_leg"):
        if opts.get(flag) is not None:
            print(f"usage error: --resume takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    if opts.get("allow_domains"):
        print("usage error: --resume takes no --allow-domain")
        return 1
    sessions = BrowserSessions(profile_dir)
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor)
        try:
            info = resume_info(db, opts["resume"], ns["audit"].replay())
        except ResumeRefused as exc:
            print(f"refused: {exc}")
            return 1
        print(f"resume: task {info['task_id']} ({info['status']})"
              f" -> {info['detail']}")
        task = ns["registry"].get(info["task_id"])
        try:
            decider = pick_decider(argv, ns["approvals"], task.task_id)
        except UsageError as exc:
            print(f"usage error: {exc}")
            return 1
        hands = ns["hands"]

        def observe():
            # Fresh stack, fresh process: no page is open yet, and
            # Runner.resume() mandates a fresh observation before
            # anything moves. Establish from the persisted addressing,
            # then observe the live page (mirrors what the original
            # run's first navigate did). Failures surface through
            # resume()'s shaped "no fresh state" refusal.
            try:
                return hands.page.locator("body").inner_text()
            except Exception:
                pass
            start_url = info.get("url")
            if not start_url:
                raise RuntimeError("no page open; navigate first")
            hands.open(start_url)
            return hands.page.locator("body").inner_text()

        res = resume_task(
            ns["taskloop"], db, task, info, decider,
            observe=observe,
            inventory_fn=lambda: [text for text, _ in
                                  collect_links(hands.page)])
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    finally:
        try:
            sessions.close()
        except Exception:
            pass
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return finish(res, info["road"], info["task_id"], ns["registry"],
                  audit_path)


def run_chain_file(argv, opts, audit_path, profile_dir, shots_dir,
                   monitor) -> int:
    """Run a chain file: an ordered list of single-road legs under one
    task (slice-45). The file holds {"legs": [{road, ...road keys}]};
    --from-leg N restarts explicitly at leg N (1-based). Each leg
    routes through the unchanged dispatcher; the first non-DONE leg
    ends the chain. Exit codes mirror single runs: 0 DONE, 2 denied,
    1 anything else."""
    from lakra.control.chain import run_chain
    if opts.get("resume") is not None:
        print("usage error: --chain-file takes no --resume")
        return 1
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "query"):
        if opts.get(flag) is not None:
            print(f"usage error: --chain-file takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    try:
        with open(opts["chain_file"], encoding="utf-8") as fh:
            chain = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"usage error: cannot read chain file ({exc})")
        return 1
    try:
        from_leg = int(opts.get("from_leg", 1))
    except (TypeError, ValueError):
        print("usage error: --from-leg must be an integer")
        return 1
    sessions = BrowserSessions(profile_dir)
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor)
        registry, taskloop, hands = (ns["registry"], ns["taskloop"],
                                     ns["hands"])
        approvals = ns["approvals"]
        legs = chain.get("legs") if isinstance(chain, dict) else None
        total = len(legs) if isinstance(legs, list) else 0
        task = registry.add(Task.create(
            f"Chain of {total} legs", allowed_tools=["browser"],
            allowed_domains=opts["allow_domains"] or ["file:"],
            allowed_paths=[]))
        registry.checkout(task.task_id, "cli")
        registry.set_status(task.task_id, Status.RUNNING)
        ns["sched"].enqueue(task.task_id)
        try:
            decider = pick_decider(argv, approvals, task.task_id)
        except UsageError as exc:
            print(f"usage error: {exc}")
            return 1

        def read_pairs():
            return list(collect_links(hands.page))

        def read_controls():
            return list(collect_controls(hands.page))

        res = run_chain(taskloop, db, hands.open, read_pairs,
                        read_controls, task, chain, decider,
                        from_leg=from_leg)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    finally:
        try:
            sessions.close()
        except Exception:
            pass
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    return finish(res, "chain", task.task_id, registry, audit_path)


def run_visibility(opts: dict) -> int:
    """Read-only task visibility (slice-39). No browser, no writes,
    no deciders: --list/--status never mix with execution flags."""
    if opts.get("list") and opts.get("status") is not None:
        print("usage error: --list and --status are mutually exclusive")
        return 1
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "resume",
                 "query", "chain_file", "from_leg"):
        if opts.get(flag) is not None:
            print(f"usage error: --list/--status take no"
                  f" --{flag.replace('_', '-')}")
            return 1
    audit_path = Path(opts.get("audit") or (ROOT / "var" / "audit-do.jsonl"))
    if opts.get("list"):
        if opts.get("last") is not None:
            print("usage error: --list takes no --last")
            return 1
        return run_list(opts)
    if opts.get("state") is not None:
        print("usage error: --status takes no --state")
        return 1
    return run_status(opts, audit_path)


def run_goal(argv, opts, audit_path, profile_dir, shots_dir,
             monitor) -> int:
    """Run a natural-language goal: shape prose to a road goal dict
    (slice-46/47). The shaper is deterministic and refusal-first;
    observe, follow (with arrival phrase), and search (with
    submit-control grounding) shape. The shaped dict flows through
    the same run_task() as the explicit path, so policy, guards, and
    the L3 gate are untouched. Exit codes mirror single runs: 0 DONE,
    2 denied, 1 anything else."""
    from lakra.control.analyzer import ground_submit
    from lakra.control.planner import UnknownGoalError
    from lakra.control.shaping import shape_goal
    from lakra.execution.browser.observer import collect_submits
    if opts.get("resume") is not None:
        print("usage error: --goal takes no --resume")
        return 1
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "query",
                 "chain_file", "from_leg"):
        if opts.get(flag) is not None:
            print(f"usage error: --goal takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    prose = opts.get("goal")
    if not prose or not prose.strip():
        print("usage error: --goal needs non-empty prose")
        return 1
    try:
        shaped = shape_goal(prose)
    except UnknownGoalError as exc:
        print(f"refused: {exc}")
        return 1
    sessions = BrowserSessions(profile_dir)
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor)
        registry, taskloop, hands = (ns["registry"], ns["taskloop"],
                                     ns["hands"])
        approvals = ns["approvals"]
        if "submit_phrase" in shaped:
            try:
                hands.open(shaped["search_url"])
                submits = list(collect_submits(hands.page))
                shaped["submit_selector"] = ground_submit(
                    shaped["submit_phrase"], submits)
            except UnknownGoalError as exc:
                print(f"refused: {exc}")
                return 1
            except Exception as exc:
                print(f"error: cannot ground submit ({exc})")
                return 1
            del shaped["submit_phrase"]
        print(f"shaped goal: {json.dumps(shaped)}")
        task = registry.add(Task.create(
            prose, allowed_tools=["browser"],
            allowed_domains=opts["allow_domains"] or ["file:"],
            allowed_paths=[]))
        registry.checkout(task.task_id, "cli")
        registry.set_status(task.task_id, Status.RUNNING)
        ns["sched"].enqueue(task.task_id)
        try:
            decider = pick_decider(argv, approvals, task.task_id)
        except UsageError as exc:
            print(f"usage error: {exc}")
            return 1

        def read_pairs():
            return list(collect_links(hands.page))

        def read_controls():
            return list(collect_controls(hands.page))

        res = run_task(taskloop, db, hands.open, read_pairs,
                       read_controls, task, shaped, decider)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    finally:
        try:
            sessions.close()
        except Exception:
            pass
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    return finish(res, "goal", task.task_id, registry, audit_path)


def main(argv: list[str]) -> int:
    try:
        opts = parse_args(argv)
    except UsageError as exc:
        print(f"usage error: {exc}\n\n{USAGE}")
        return 1

    if opts.get("list") or opts.get("status") is not None:
        return run_visibility(opts)

    if opts.get("format") is not None:
        print("usage error: --format applies only to --list/--status")
        return 1

    try:
        monitor = monitor_for(opts)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1

    audit_path = Path(opts.get("audit") or (ROOT / "var" / "audit-do.jsonl"))
    profile_dir = Path(opts.get("profile_dir") or (ROOT / "var" / "do-profile"))
    shots_dir = Path(opts.get("shots_dir") or (ROOT / "var" / "do-shots"))
    if opts.get("resume"):
        return run_resume(argv, opts, audit_path, profile_dir, shots_dir,
                          monitor)
    if opts.get("chain_file") is not None or opts.get("from_leg") is not None:
        if opts.get("chain_file") is None:
            print("usage error: --from-leg needs --chain-file")
            return 1
        return run_chain_file(argv, opts, audit_path, profile_dir,
                              shots_dir, monitor)
    if opts.get("chain_file") is not None or opts.get("from_leg") is not None:
        if opts.get("chain_file") is None:
            print("usage error: --from-leg needs --chain-file")
            return 1
        return run_chain_file(argv, opts, audit_path, profile_dir,
                              shots_dir, monitor)
    if opts.get("goal") is not None:
        return run_goal(argv, opts, audit_path, profile_dir,
                        shots_dir, monitor)

    try:
        goal = build_goal(opts)
    except UsageError as exc:
        print(f"usage error: {exc}\n\n{USAGE}")
        return 1
    road = opts["road"]
    allow_domains = opts["allow_domains"] or ["file:"]

    sessions = BrowserSessions(profile_dir)
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor)
        registry, taskloop, hands = (ns["registry"], ns["taskloop"],
                                     ns["hands"])
        approvals = ns["approvals"]
        task = registry.add(Task.create(
            default_task_goal(road, opts), allowed_tools=["browser"],
            allowed_domains=allow_domains, allowed_paths=[]))
        registry.checkout(task.task_id, "cli")
        registry.set_status(task.task_id, Status.RUNNING)
        ns["sched"].enqueue(task.task_id)
        try:
            decider = pick_decider(argv, approvals, task.task_id)
        except UsageError as exc:
            print(f"usage error: {exc}")
            return 1

        def read_pairs():
            return list(collect_links(hands.page))

        def read_texts():
            return [text for text, _ in collect_links(hands.page)]

        def read_controls():
            return list(collect_controls(hands.page))

        read_links = read_texts if road == "loop" else read_pairs
        res = run_task(taskloop, db, hands.open, read_links,
                       read_controls, task, goal, decider)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    finally:
        try:
            sessions.close()
        except Exception:
            pass
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    return finish(res, road, task.task_id, registry, audit_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
