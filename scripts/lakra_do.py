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
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road click
        --url <page> --text "Continue" --expect "Welcome" [--yes]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road table
        --url <page> --table "courses" --expect "Linear Algebra" [--yes]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road download
        --url <page> --download "Monthly report" --dest report.txt
        --expect "Monthly report" [--yes | --no | --poll SECS]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --road upload
        --url <page> --upload "Expense receipt" --src receipt.txt
        --expect "received receipt.txt" [--yes | --no | --poll SECS]

Natural-language goal shaping (slice-46/47 + NL click: deterministic
prose -> road dict; observe, follow, search, and click shape, all
other intents refuse with guidance; no model calls, policy untouched):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Show me the records page at file:///l.html" [--yes]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Follow the Beta record on file:///l.html and show me the
        detail record" [--yes | --no | --poll SECS]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Web search for cats at file:///s.html and confirm
        results for cats" [--yes | --no | --poll SECS]
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --goal "Open file:///c.html, click the Continue button, and
        verify Welcome" [--yes]

Chained multi-template runs (slice-45: sequential supervised legs,
one task; loop legs refused; first non-DONE leg ends the chain):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --chain-file chain.json [--from-leg N] [--yes | --no | --poll SECS]

Chained multi-template runs (one task, sequential supervised legs):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --chain-file chain.json [--from-leg N] [--yes | --no | --poll SECS]

Resume an EXISTING chain task from leg N (slice-51: same task id,
fresh re-execution of legs N..total, stale live plans superseded;
a bare --chain-file --from-leg run always creates a NEW task):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py --resume TASK_ID
        --chain-file chain.json --from-leg N [--yes | --no | --poll SECS]

Decompose a high-level goal into a chain (V2-01: deterministic
prose -> 2-4 road legs through the unchanged chain machinery; each
leg must be independently shapable, search submits grounded at the
execution edge like --goal):
    .\\.venv\\Scripts\\python.exe scripts\\lakra_do.py
        --decompose "Show me the records at URL with Records, then
        follow the Beta record on URL and show me detail record"
        [--from-leg N] [--yes | --no | --poll SECS]

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
    --transfers-dir PATH: sandbox for browser.download destinations
      and browser.upload sources (default var/transfers; every path
      is realpath-confined under it, no absolute-outside, no ../,
      no symlink escape)
    --session-profile NAME: opt-in persistent browser profile
      (default: existing ephemeral profile behavior, unchanged;
      named profiles live under --sessions-dir, default
      var/sessions; names are validated, never paths)
    --sessions-dir PATH: root for named session profiles
    --clear-session-profile NAME: delete one named profile and exit
    --planner deterministic|model: decomposition planner for
      --decompose only (default deterministic; model uses a local
      provider and falls back on any rejection)
    --model NAME, --model-url URL, --model-timeout-s SECS: local
      model configuration (loopback Ollama only)
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
    collect_clicks,
    collect_controls,
    collect_links,
    collect_tables,
)
from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402
from lakra.execution.registry import ToolRegistry  # noqa: E402
from lakra.execution.router import ToolRouter  # noqa: E402
from lakra.resources.monitor import ResourceMonitor  # noqa: E402

USAGE = (__doc__ or "").strip()

ROADS = ("follow", "loop", "form", "observe", "search", "click", "table",
         "download", "upload")


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
                   "--query", "--chain-file", "--from-leg", "--decompose",
                   "--table", "--download", "--dest", "--upload", "--src",
                   "--transfers-dir", "--session-profile",
                   "--sessions-dir", "--clear-session-profile",
                   "--planner", "--model", "--model-url",
                   "--model-timeout-s"}
    flags = {"--yes", "--no", "--list", "--daemon", "--once"}
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
            "--road must be one of"
            " follow|loop|form|observe|search|click|table|download|upload")
    url = opts.get("url")
    if not url:
        raise UsageError(f"--road {road} needs --url")
    if road == "table":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "query", "text", "download", "dest",
                "upload", "src")
        _need(opts, road, "table", "expect")
        return {"table_url": url, "table_text": opts["table"],
                "expect_text": opts["expect"]}
    if road == "download":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "query", "text", "table", "upload", "src")
        _need(opts, road, "download", "dest", "expect")
        return {"download_url": url,
                "download_text": opts["download"],
                "dest_path": opts["dest"],
                "expect_text": opts["expect"]}
    if road == "upload":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "query", "text", "table", "download", "dest")
        _need(opts, road, "upload", "src", "expect")
        return {"upload_url": url, "upload_text": opts["upload"],
                "src_path": opts["src"],
                "expect_text": opts["expect"]}
    if road == "follow":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "table", "download", "dest", "upload", "src")
        _need(opts, road, "text", "expect")
        return {"list_url": url, "goal_text": opts["text"],
                "body_expect": opts["expect"]}
    if road == "loop":
        _forbid(opts, road, "slots_json", "submit", "table", "download",
                "dest", "upload", "src")
        _need(opts, road, "text", "expect", "max_items", "max_iters")
        return {"list_url": url, "goal_text": opts["text"],
                "body_expect": opts["expect"],
                "max_items": _parse_int(opts, "max_items"),
                "max_iters": _parse_int(opts, "max_iters")}
    if road == "observe":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "query", "table", "download", "dest",
                "upload", "src")
        _need(opts, road, "text", "expect")
        return {"url": url, "expect_text": opts["expect"]}
    if road == "search":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "text", "table", "download", "dest", "upload", "src")
        _need(opts, road, "query", "submit", "expect")
        return {"search_url": url, "query": {"text": opts["query"]},
                "submit_selector": opts["submit"],
                "expect_text": opts["expect"]}
    if road == "click":
        _forbid(opts, road, "max_items", "max_iters", "slots_json",
                "submit", "query", "table", "download", "dest",
                "upload", "src")
        _need(opts, road, "text", "expect")
        return {"click_url": url, "click_text": opts["text"],
                "expect_text": opts["expect"]}
    _forbid(opts, road, "text", "expect", "max_items", "max_iters",
            "table", "download", "dest", "upload", "src")
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
    if road == "click":
        return f"Click {opts.get('click_text', 'the control')}"
    if road == "table":
        return f"Extract table {opts.get('table', 'the table')}"
    if road == "download":
        return f"Download {opts.get('download', 'the file')}"
    if road == "upload":
        return f"Upload {opts.get('upload', 'the file')}"
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


def transfers_dir_for(opts: dict) -> Path:
    """Sandbox root for browser transfers (V2-05). One root only:
    download destinations and upload sources confine under it."""
    return Path(opts.get("transfers_dir")
                or (ROOT / "var" / "transfers"))


def sessions_root_for(opts: dict) -> Path:
    """Lakra-controlled root for named session profiles (V2-06).
    Profiles never address arbitrary directories: names resolve
    under this root only."""
    return Path(opts.get("sessions_dir")
                or (ROOT / "var" / "sessions"))


def select_session(opts: dict, profile_dir: Path):
    """Resolve the browser profile directory for a run (V2-06).

    Returns (name_or_None, directory, reused). Without
    --session-profile the directory passes through untouched
    (existing ephemeral behavior, byte-identical). With the flag the
    name validates and resolves under the sessions root; reused is
    True when the profile already held state. Combining the flag
    with --profile-dir refuses (ambiguous selection).
    """
    from lakra.control.profiles import resolve_profile
    name = opts.get("session_profile")
    if name is None:
        return None, Path(profile_dir), False
    if opts.get("profile_dir") is not None:
        raise UsageError("--session-profile takes no --profile-dir")
    try:
        target = resolve_profile(sessions_root_for(opts), name)
    except Exception as exc:
        raise UsageError(f"invalid --session-profile ({exc})")
    from lakra.control.profiles import validate_profile_name
    reused = target.is_dir() and any(target.iterdir())
    return validate_profile_name(name), target, reused


def session_exclusive(opts: dict) -> bool:
    """True when the run selected a named profile (V2-06): named
    profiles launch under Lakra's cross-process lock, so two live
    holders never silently merge state. Ephemeral runs stay
    unlocked (V1 behavior identical)."""
    return opts.get("session_profile") is not None


def resolve_model_provider(opts: dict):
    """Build the V2-07 model provider from operator flags (or None
    for deterministic planning). Raises UsageError on unusable
    configuration — always before anything launches."""
    if (opts.get("planner") or "deterministic") == "deterministic":
        if opts.get("model") is not None \
                or opts.get("model_url") is not None \
                or opts.get("model_timeout_s") is not None:
            raise UsageError("--model/--model-url/--model-timeout-s"
                             " need --planner model")
        return None
    from lakra.control.model_provider import (
        ModelProviderConfigError,
        build_provider,
    )
    try:
        timeout = int(opts.get("model_timeout_s", 60))
    except (TypeError, ValueError):
        raise UsageError("--model-timeout-s must be an integer")
    try:
        return build_provider(
            opts.get("model") or "llama3.1:8b",
            base_url=opts.get("model_url")
            or "http://127.0.0.1:11434",
            timeout_s=timeout)
    except ModelProviderConfigError as exc:
        raise UsageError(f"unusable model configuration ({exc})")


def print_session_lines(name, reused) -> None:
    """Safe session metadata only (V2-06): profile name + fresh/reused.
    Never contents, cookies, tokens, or storage values. Ephemeral runs
    print nothing (V1 output byte-identical)."""
    if name is None:
        return
    print(f"session profile: {name}")
    print(f"session reused: {'yes' if reused else 'no'}")


def item_session_profile(legs):
    """Explicit session identity for one queue item's legs (V2-06).

    Returns the single distinct session_profile across legs, or None
    when legs carry none (existing ephemeral behavior). Refuses
    non-string values, invalid names, and mixed profiles (ambiguous
    selection must never silently pick one). Queue items carry the
    identity in-leg so no schema migration is needed and nothing is
    ever inherited silently.
    """
    from lakra.control.profiles import (
        ProfileRefused,
        validate_profile_name,
    )
    names = []
    for leg in (legs or []):
        if not isinstance(leg, dict):
            continue
        value = leg.get("session_profile")
        if value is None:
            continue
        if not isinstance(value, str):
            raise ProfileRefused(
                "session_profile must be a string")
        names.append(validate_profile_name(value))
    distinct = sorted(set(names))
    if len(distinct) > 1:
        raise ProfileRefused(
            f"ambiguous session profiles {distinct}: refuse, never mix")
    return distinct[0] if distinct else None


def chain_wants_downloads(legs) -> bool:
    """True when any leg runs the download road (V2-05): the browser
    context must then accept downloads, or the gated action fails
    closed. Pure scan, no browser."""
    try:
        return any(isinstance(leg, dict) and leg.get("road") == "download"
                   for leg in (legs or []))
    except TypeError:
        return False


def queue_wants_downloads(db) -> bool:
    """True when queued/claimed work may run a download leg (V2-05):
    lets the daemon enable the context switch only when some item
    could need it. Reads only; malformed rows are someone else's
    refusal, never a reason to enable."""
    from lakra.control import work_queue as _wq
    try:
        items = _wq.list_items(db)
    except Exception:
        return False
    for item in items:
        if item.get("state") not in ("QUEUED", "CLAIMED"):
            continue
        try:
            raw = json.loads(item.get("legs_json") or "{}") or {}
            legs = raw.get("legs") or []
        except (ValueError, AttributeError):
            continue
        if chain_wants_downloads(legs):
            return True
    return False


def build_stack(sessions, db, audit_path, shots_dir, monitor,
                transfer_root=None):
    """Assemble the real browser stack on live sessions + database.

    The exact construction the CLI always used; shared by the fresh
    and resume paths so both run the identical policy/guard/router
    machinery. transfer_root (V2-05) confines the download/upload
    executors and the file_nonempty predicate; None disables both
    transfer executors outright.
    """
    registry = TaskRegistry(store=db)
    audit = AuditLog(audit_path)
    sched = Scheduler(registry, store=db)
    approvals = Approvals(registry, store=db)
    tools = ToolRegistry()
    hands = BrowserActions(sessions, transfer_root=transfer_root)
    obs = BrowserObserver(sessions, shots_dir)
    for kind in ("browser.navigate", "browser.snapshot",
                 "browser.screenshot"):
        tools.register(kind, obs)
    for kind in ("browser.click", "browser.type", "browser.press",
                 "browser.scroll", "browser.wait", "browser.submit",
                 "browser.check", "browser.select",
                 "browser.download", "browser.upload"):
        tools.register(kind, hands)
    router = ToolRouter(registry, tools, approvals, audit, sched)
    ctrl = BrowserController(router, hands, audit, sched,
                             observer=obs,
                             transfer_root=transfer_root)
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


# -- chain visibility (slice-50, read-only) --------------------------------------
# Derives per-leg chain status from existing persisted state only: the
# task goal ("Chain of N legs", set by --chain-file), the task-filtered
# audit trail, and persisted plan hints. Pure reads (SELECTs + replay);
# no writes, no new events, no new state, no execution.
#
# Derivation rules (all deterministic, audit-ordered):
# - Not a chain task unless the persisted goal is exactly
#   "Chain of <N> legs". Total legs come from that goal.
# - Each terminal PLAN_OUTCOME for a non-superseded plan closes one
#   leg segment; the road comes from that plan's persisted hints
#   (key shape, same vocabulary as the dispatcher). Replanned
#   (superseded) plans are skipped: a leg is its final plan.
# - DONE advances to the next leg; anything else holds the index so
#   a --from-leg rerun overwrites the same slot (latest wins).
# - A plan_id "-" outcome is a pre-plan refusal: chain-level when it
#   carries an "invalid chain"/"invalid from_leg" detail (legs stay
#   empty), else the current leg's STOPPED with road unknown.
# - chain_id comes from the TASK_LIFECYCLE payload when the chain
#   finished DONE; otherwise it is genuinely unknown (None), never
#   invented.

def _chain_total_legs(goal) -> int | None:
    prefix, suffix = "Chain of ", " legs"
    if not isinstance(goal, str):
        return None
    if not (goal.startswith(prefix) and goal.endswith(suffix)):
        return None
    try:
        total = int(goal[len(prefix):-len(suffix)])
    except (TypeError, ValueError):
        return None
    if total < 1:
        return None
    return total


def _road_of_hints(hints: dict) -> str:
    if not isinstance(hints, dict):
        return "unknown"
    if "dest_path" in hints:
        return "download"
    if "src_path" in hints:
        return "upload"
    if "table_text" in hints:
        return "table"
    if "fields" in hints:
        return "form"
    if "link_text" in hints:
        return "follow"
    if "submit_selector" in hints and "text" in hints:
        return "search"
    if "selector" in hints:
        return "click"
    if "url" in hints:
        return "observe"
    return "unknown"


def chain_summary(db, task, events):
    """Per-leg chain picture, or None for non-chain tasks.

    Pure reads. Returns {"chain_id", "total_legs", "legs_done",
    "legs": [{"index", "road", "status"}]} with 1-based indices in
    order, or None when the task goal is not a chain goal.
    """
    from lakra.control import plan_store
    total = _chain_total_legs(task.goal if task is not None else "")
    if total is None:
        return None
    mine = [e for e in (events or []) if e.get("task_id") == task.task_id]
    superseded = set()
    for e in mine:
        if e.get("type") == "PLAN_SUPERSEDED":
            old = (e.get("payload") or {}).get("old_plan")
            if isinstance(old, str) and old:
                superseded.add(old)
    chain_id = None
    for e in mine:
        if e.get("type") == "TASK_LIFECYCLE":
            cid = (e.get("payload") or {}).get("chain_id")
            if isinstance(cid, str) and cid:
                chain_id = cid

    def road_of(pid):
        try:
            _, hints, _ = plan_store.load_plan(db, pid)
        except Exception:
            return "unknown"
        return _road_of_hints(hints)

    slots: dict = {}
    by_pid: dict = {}
    open_pid = None
    idx = 1
    for e in mine:
        etype = e.get("type")
        payload = e.get("payload") or {}
        if etype == "PLAN_CREATED":
            pid = payload.get("plan_id")
            if (isinstance(pid, str) and pid and pid != "-"
                    and pid not in superseded and open_pid is None):
                open_pid = pid
        elif etype == "PLAN_OUTCOME":
            pid = payload.get("plan_id")
            status = payload.get("status")
            if open_pid is not None and pid == open_pid:
                if idx <= total:
                    slots[idx] = {"index": idx, "road": road_of(pid),
                                  "status": status}
                    by_pid[pid] = idx
                open_pid = None
                if status == "DONE":
                    idx += 1
            elif isinstance(pid, str) and pid and pid != "-" \
                    and pid in by_pid:
                # A plan may record a transient outcome (ASK_PENDING
                # while parked) before its terminal one; latest wins.
                slots[by_pid[pid]]["status"] = status
            elif pid == "-":
                detail = payload.get("detail") or ""
                if detail.startswith("invalid chain (") or \
                        detail.startswith("invalid from_leg"):
                    continue  # chain-level refusal: no leg started
                if idx <= total:
                    slots[idx] = {"index": idx, "road": "unknown",
                                  "status": status}
    legs = [slots[n] for n in sorted(slots)]
    return {"chain_id": chain_id, "total_legs": total,
            "legs_done": sum(1 for leg in legs
                             if leg["status"] == "DONE"),
            "legs": legs}


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


def chain_to_json(chain) -> dict:
    """Closed chain keys (slice-50): only keys derived from existing
    persisted state (task goal, audit trail, plan hints)."""
    return {"chain_id": chain["chain_id"],
            "total_legs": chain["total_legs"],
            "legs_done": chain["legs_done"],
            "legs": [{"index": leg["index"], "road": leg["road"],
                      "status": leg["status"]} for leg in chain["legs"]]}


def render_status_json(task, info_or_reason, pending_ids, tail,
                       chain=None) -> str:
    out = {"task": task_to_json(task),
           "resume": resume_to_json(info_or_reason),
           "pending_approvals": list(pending_ids),
           "audit_tail": [event_to_json(e) for e in tail]}
    if chain is not None:
        out["chain"] = chain_to_json(chain)
    return json.dumps(out)


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


def render_status(task, info_or_reason, pending_ids, tail,
                    chain=None) -> str:
    """Full one-task picture. info_or_reason is the resume_info() dict
    or its refusal text; tail is already task-filtered audit events;
    chain is the chain_summary() dict or None for non-chain tasks."""
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
    if chain is not None:
        lines.append(f"chain: {chain['chain_id'] or 'unknown'}")
        lines.append(f"legs: {chain['legs_done']}/{chain['total_legs']}"
                     " done")
        for leg in chain["legs"]:
            lines.append(f"  {leg['index']}. {leg['road']}"
                         f" - {leg['status']}")
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
        chain = chain_summary(db, task, events)
        if format_name == "json":
            print(render_status_json(task, info, pending_ids, tail,
                                     chain))
        else:
            print(render_status(task, info, pending_ids, tail, chain))
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
                 "chain_file", "from_leg", "decompose", "table",
                 "download", "dest", "upload", "src",
                 "clear_session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "daemon", "once"):
        if opts.get(flag) is not None:
            print(f"usage error: --resume takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    if opts.get("allow_domains"):
        print("usage error: --resume takes no --allow-domain")
        return 1
    # V1-D1: a chain task must resume as a chain, never as a single
    # stranded unit (a rescued leg plan would COMPLETE the task with
    # legs unexecuted, silently bypassing chain semantics). Read-only
    # check before anything launches.
    try:
        _db = Database()
        try:
            _guard_task = task_store.load_task(_db, opts["resume"])
        finally:
            try:
                _db.close()
            except Exception:
                pass
    except Exception:
        _guard_task = None
    if _guard_task is not None and _chain_total_legs(
            _guard_task.goal) is not None:
        print(f"refused: task {opts['resume']} is a chain task;"
              " resume it with --resume TASK_ID --chain-file FILE"
              " --from-leg N")
        return 1
    try:
        _sess_name, _sess_dir, _sess_reused = select_session(
            opts, profile_dir)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    sessions = BrowserSessions(_sess_dir,
                               exclusive=session_exclusive(opts))
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
    print_session_lines(_sess_name, _sess_reused)
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
                 "max_iters", "slots_json", "submit", "goal", "query",
                 "decompose", "table", "download", "dest", "upload",
                 "src", "clear_session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "daemon", "once"):
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
    # V2-05: the download context switch is fixed at launch, so scan
    # the (already read) chain file for a download leg first. The
    # scan is pure JSON; validation still happens inside run_chain.
    legs_hint = chain.get("legs") if isinstance(chain, dict) else None
    try:
        _sess_name, _sess_dir, _sess_reused = select_session(
            opts, profile_dir)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    sessions = BrowserSessions(
        _sess_dir,
        accept_downloads=chain_wants_downloads(legs_hint),
        exclusive=session_exclusive(opts))
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor,
                         transfer_root=transfers_dir_for(opts))
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

        def read_clicks():
            return list(collect_clicks(hands.page))

        def read_tables():
            return list(collect_tables(hands.page))

        res = run_chain(taskloop, db, hands.open, read_pairs,
                        read_controls, task, chain, decider,
                        from_leg=from_leg, read_clicks=read_clicks,
                        read_tables=read_tables,
                        transfer_root=transfers_dir_for(opts))
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

    print_session_lines(_sess_name, _sess_reused)
    return finish(res, "chain", task.task_id, registry, audit_path)


def run_chain_resume(argv, opts, audit_path, profile_dir, shots_dir,
                     monitor, db_path=None) -> int:
    """Resume an EXISTING chain task from leg N (slice-51).

    UX: --resume TASK_ID --chain-file FILE [--from-leg N]. The task
    keeps its id; legs N..total re-execute fresh (re-observe,
    re-ground, fresh plans through the unchanged dispatcher) via the
    existing run_chain(). Stale live plans of this task are abandoned
    through the existing plan-status mechanism (SUPERSEDED +
    PLAN_SUPERSEDED audit) before any leg runs.

    All preconditions (chain file, from_leg range, known task,
    non-terminal, chain task, no pending approvals, no uncertain
    execution, ownership checkout) pass before the browser launches;
    any refusal is read-only (exit 1, nothing written, nothing
    executed). Exit codes mirror chain runs: 0 DONE, 2 denied,
    1 anything else.
    """
    from lakra.control.chain import ChainRefused, run_chain, validate_chain
    from lakra.control.recovery import find_uncertain
    from lakra.control.resume import PLAN_LIVE, pending_approval_ids
    from lakra.control.tasks import TERMINAL
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "query",
                 "table", "download", "dest", "upload", "src",
                 "clear_session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "daemon", "once"):
        if opts.get(flag) is not None:
            print(f"usage error: --resume --chain-file takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    if opts.get("allow_domains"):
        print("usage error: --resume --chain-file takes no --allow-domain")
        return 1
    task_id = opts.get("resume")
    try:
        with open(opts["chain_file"], encoding="utf-8") as fh:
            chain = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"usage error: cannot read chain file ({exc})")
        return 1
    try:
        legs = validate_chain(chain)
    except ChainRefused as exc:
        print(f"refused: invalid chain ({exc})")
        return 1
    try:
        from_leg = int(opts.get("from_leg", 1))
    except (TypeError, ValueError):
        print("usage error: --from-leg must be an integer")
        return 1
    if isinstance(from_leg, bool) or not (1 <= from_leg <= len(legs)):
        print(f"refused: invalid from_leg {opts.get('from_leg', 1)!r}"
              f" for a {len(legs)}-leg chain")
        return 1
    try:
        _sess_name, _sess_dir, _sess_reused = select_session(
            opts, profile_dir)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    sessions = BrowserSessions(
        _sess_dir, accept_downloads=chain_wants_downloads(legs),
        exclusive=session_exclusive(opts))
    db = None
    try:
        db = Database(db_path) if db_path is not None else Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor,
                         transfer_root=transfers_dir_for(opts))
        registry = ns["registry"]
        try:
            task = registry.get(task_id)
        except Exception:
            print(f"refused: unknown task {task_id}")
            return 1
        if task.status in TERMINAL:
            print(f"refused: task {task_id} is {task.status.value};"
                  " history immutable")
            return 1
        if _chain_total_legs(task.goal) is None:
            print(f"refused: task {task_id} is not a chain task")
            return 1
        try:
            events = ns["audit"].replay()
        except Exception:
            events = []
        pending = pending_approval_ids(db, task_id)
        if pending:
            ids = ", ".join(pending)
            print(f"refused: task {task_id} has undecided approval"
                  f" {ids}: decide it via approve.py or let it expire,"
                  " then resume")
            return 1
        uncertain = [kind for tid, kind in find_uncertain(events)
                     if tid == task_id]
        if uncertain:
            kinds = ", ".join(uncertain)
            print(f"refused: task {task_id} has uncertain execution"
                  f" ({kinds}): started but never completed; re-observe"
                  " and require a fresh approval, i.e. start a fresh run"
                  " instead")
            return 1
        try:
            registry.checkout(task_id, "cli")
        except Exception as exc:
            print(f"refused: cannot take ownership ({exc})")
            return 1
        try:
            task = registry.refresh(task_id)
        except Exception as exc:
            print(f"refused: cannot refresh task state ({exc})")
            return 1
        try:
            sessions.launch()
        except Exception as exc:
            print(f"error: cannot launch browser ({exc})")
            return 1
        try:
            sched = ns["sched"]
            try:
                sched.enqueue(task_id)
                steps, _ = task_store.get_usage(db, task_id)
                if steps:
                    sched._steps_used[task_id] = steps
            except Exception:
                pass
            if str(task.status.value) == "PAUSED":
                try:
                    sched.resume(task_id)
                    task = registry.get(task_id)
                except Exception as exc:
                    print(f"refused: cannot resume paused task ({exc})")
                    return 1
            from lakra.control import plan_store
            for pid in plan_store.plans_for_task(db, task_id):
                try:
                    plan, _, _ = plan_store.load_plan(db, pid)
                except Exception:
                    continue
                if plan.status not in PLAN_LIVE:
                    continue
                try:
                    plan_store.set_plan_status(db, pid, "SUPERSEDED")
                except Exception:
                    continue  # raced to terminal; already frozen
                ns["audit"].log("PLAN_SUPERSEDED", task_id,
                               {"old_plan": pid, "new_plan": "-",
                                "at_step": 0, "snapshot_chars": 0})
            try:
                decider = pick_decider(argv, ns["approvals"], task_id)
            except UsageError as exc:
                print(f"usage error: {exc}")
                return 1
            hands = ns["hands"]

            def read_pairs():
                return list(collect_links(hands.page))

            def read_controls():
                return list(collect_controls(hands.page))

            def read_clicks():
                return list(collect_clicks(hands.page))

            def read_tables():
                return list(collect_tables(hands.page))

            print(f"resume: task {task_id} ({task.status.value})"
                  f" -> chain legs {from_leg}..{len(legs)}")
            res = run_chain(ns["taskloop"], db, hands.open, read_pairs,
                            read_controls, task, chain, decider,
                            from_leg=from_leg, read_clicks=read_clicks,
                            read_tables=read_tables,
                            transfer_root=transfers_dir_for(opts))
        except Exception as exc:
            print(f"error: {exc}")
            return 1
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
    print_session_lines(_sess_name, _sess_reused)
    return finish(res, "chain", task_id, registry, audit_path)


def run_decompose(argv, opts, audit_path, profile_dir, shots_dir,
                  monitor) -> int:
    """Run a high-level goal decomposed into a chain (V2-01).

    UX: --decompose PROSE [--from-leg N]. The prose splits into 2-4
    independently shapable legs through the pure decompose_goal()
    contract; search legs ground their submit phrase against the
    observed submit inventory at the execution edge (same precedent
    as --goal); the finalized legs pass the real validate_chain()
    and execute through the unchanged run_chain() on a NEW task, so
    a decomposed run behaves exactly like its hand-written
    --chain-file equivalent. Pure-shaping refusals happen before
    the browser launches; nothing else in V1 moves.
    """
    from lakra.control.analyzer import ground_submit
    from lakra.control.chain import ChainRefused, run_chain, validate_chain
    from lakra.control.decompose import DecomposeRefused, decompose_goal
    from lakra.control.planner import UnknownGoalError
    from lakra.execution.browser.observer import collect_submits
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "query",
                 "chain_file", "resume", "table", "download", "dest",
                 "upload", "src", "clear_session_profile",
                 "daemon", "once"):
        if opts.get(flag) is not None:
            print(f"usage error: --decompose takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    prose = opts.get("decompose")
    if not prose or not prose.strip():
        print("usage error: --decompose needs non-empty prose")
        return 1
    planner_name = opts.get("planner") or "deterministic"
    if planner_name not in ("deterministic", "model"):
        print("usage error: --planner must be deterministic|model")
        return 1
    if planner_name == "deterministic":
        print("planner: deterministic")
        try:
            legs = decompose_goal(prose)
        except DecomposeRefused as exc:
            print(f"refused: {exc}")
            return 1
    else:
        # V2-07 model planning happens here, before the browser
        # launches: the provider is untrusted input, and the legs
        # below are validated V2-01 road dicts either way.
        try:
            provider = resolve_model_provider(opts)
        except UsageError as exc:
            print(f"usage error: {exc}")
            return 1
        from lakra.control.model_planner import plan_with_fallback
        from lakra.control.model_provider import provider_available
        if not provider_available(provider):
            # Fast path: runtime down means the calls would fail
            # anyway; fall back without burning planning budget.
            print("planner: model->fallback (provider unavailable)")
            try:
                legs = decompose_goal(prose)
            except DecomposeRefused as exc:
                print(f"refused: {exc}")
                return 1
        else:
            try:
                legs, attempt = plan_with_fallback(prose, provider)
            except DecomposeRefused as exc:
                print(f"refused: {exc}")
                return 1
            if attempt.fallback_used:
                print(f"planner: model->fallback"
                      f" ({attempt.rejection_reason})")
            else:
                print(f"planner: model ({provider.name},"
                      f" {attempt.latency_ms}ms)")
    try:
        from_leg = int(opts.get("from_leg", 1))
    except (TypeError, ValueError):
        print("usage error: --from-leg must be an integer")
        return 1
    if isinstance(from_leg, bool) or not (1 <= from_leg <= len(legs)):
        print(f"refused: invalid from_leg {opts.get('from_leg', 1)!r}"
              f" for a {len(legs)}-leg chain")
        return 1
    try:
        _sess_name, _sess_dir, _sess_reused = select_session(
            opts, profile_dir)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    sessions = BrowserSessions(_sess_dir,
                               exclusive=session_exclusive(opts))
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor,
                         transfer_root=transfers_dir_for(opts))
        registry, taskloop, hands = (ns["registry"], ns["taskloop"],
                                     ns["hands"])
        approvals = ns["approvals"]
        for n, leg in enumerate(legs, 1):
            if leg.get("road") != "search" or "submit_phrase" not in leg:
                continue
            try:
                hands.open(leg["search_url"])
                submits = list(collect_submits(hands.page))
                leg["submit_selector"] = ground_submit(
                    leg["submit_phrase"], submits)
            except UnknownGoalError as exc:
                print(f"refused: leg {n} ({exc})")
                return 1
            except Exception as exc:
                print(f"error: cannot ground submit ({exc})")
                return 1
            del leg["submit_phrase"]
        try:
            validate_chain({"legs": legs})
        except ChainRefused as exc:
            print(f"refused: invalid chain ({exc})")
            return 1
        print(f"decomposed: {len(legs)} legs"
              f" ({', '.join(leg['road'] for leg in legs)})")
        task = registry.add(Task.create(
            f"Chain of {len(legs)} legs", allowed_tools=["browser"],
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

        def read_clicks():
            return list(collect_clicks(hands.page))

        def read_tables():
            return list(collect_tables(hands.page))

        res = run_chain(taskloop, db, hands.open, read_pairs,
                        read_controls, task, {"legs": legs}, decider,
                        from_leg=from_leg, read_clicks=read_clicks,
                        read_tables=read_tables,
                        transfer_root=transfers_dir_for(opts))
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

    print_session_lines(_sess_name, _sess_reused)
    return finish(res, "chain", task.task_id, registry, audit_path)


def run_daemon(argv, opts, audit_path, profile_dir, shots_dir,
               monitor) -> int:
    """Supervised worker over the persisted queue (V2-03).

    UX: --daemon [--max-items N] | --once [--max-items N]. Each pass
    reclaims stale work before claiming fresh FIFO work through the
    existing work_queue CAS, materializes the item to validated legs,
    and executes through the unchanged run_chain() on either a fresh
    task or the item's bound task (chain-resume flow: ownership,
    usage restore, stale-plan supersede). Task outcomes map 1:1
    (COMPLETED/CANCELLED/FAILED settle; parked/paused/stalled legs
    stay CLAIMED under the lease for a later pass). L3 gates park
    through the existing approval flow (--poll waits, --yes/--no
    decides); killing the worker mid-claim leaves standard
    lease-governed recovery. Exit 0 on a clean stop (drained or
    budget reached), 1 on interrupt or worker error. Orchestration
    only: no policy, approval, or execution logic lives here.
    """
    import os
    from uuid import uuid4

    from lakra.control import plan_store, task_store, work_queue
    from lakra.control.chain import run_chain
    from lakra.control.daemon import serve
    from lakra.control.profiles import ProfileRefused, resolve_profile
    from lakra.control.resume import PLAN_LIVE
    from lakra.control.tasks import TERMINAL
    from lakra.control.work_queue import WorkRefused
    for flag in ("road", "url", "text", "expect", "table",
                 "download", "dest", "upload", "src",
                 "session_profile", "clear_session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "max_iters", "slots_json", "submit", "goal", "query",
                 "chain_file", "from_leg", "resume", "decompose"):
        if opts.get(flag) is not None:
            print(f"usage error: --daemon takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    once = bool(opts.get("once"))
    if opts.get("max_items") is not None:
        try:
            max_items = int(opts["max_items"])
        except (TypeError, ValueError):
            print("usage error: --max-items must be an integer")
            return 1
    else:
        max_items = None
    owner = f"daemon-{os.getpid()}-{uuid4().hex[:8]}"
    # V2-05: the download context switch is fixed at launch, so peek
    # at queued/claimed work for a download leg first. The peek is
    # reads-only; every download still parks at its own L3 gate.
    db = None
    try:
        db = Database()
        want_downloads = queue_wants_downloads(db)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    # V2-06: per-item session identity. The daemon never takes
    # --session-profile (that would silently attach one profile to
    # all queued work); each item's legs may carry one explicit
    # session_profile instead, and sessions rotate to it. None means
    # the existing ephemeral directory, unchanged.
    ephemeral_dir = Path(profile_dir)
    sessions_root = sessions_root_for(opts)
    transfer_root = transfers_dir_for(opts)
    session_key = None
    sessions = BrowserSessions(ephemeral_dir,
                               accept_downloads=want_downloads)
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        try:
            db.close()
        except Exception:
            pass
        return 1
    try:
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor,
                         transfer_root=transfer_root)
        registry, taskloop, hands = (ns["registry"], ns["taskloop"],
                                     ns["hands"])
        sched = ns["sched"]

        def ensure_session(want):
            """Rotate browser sessions to one item's profile (V2-06).

            Returns None on success (already there or rotated), else
            a detail string. Rotation closes the previous context and
            rebuilds the stack on the same database and audit trail;
            the queue claim is untouched. A contended profile (locked
            by another live process) fails here so the item defers
            under its lease instead of corrupting shared state.
            """
            nonlocal sessions, ns, registry, taskloop, hands, sched
            nonlocal session_key
            if want == session_key:
                return None
            try:
                target = ephemeral_dir if want is None \
                    else resolve_profile(sessions_root, want)
            except ProfileRefused as exc:
                return f"invalid session profile ({exc})"
            try:
                sessions.close()
            except Exception:
                pass
            fresh = BrowserSessions(
                target, accept_downloads=want_downloads,
                exclusive=(want is not None))
            try:
                fresh.launch()
            except Exception as exc:
                return f"cannot launch browser session ({exc})"
            sessions = fresh
            ns = build_stack(sessions, db, audit_path, shots_dir,
                             monitor, transfer_root=transfer_root)
            registry, taskloop, hands = (
                ns["registry"], ns["taskloop"], ns["hands"])
            sched = ns["sched"]
            session_key = want
            return None

        def read_pairs():
            return list(collect_links(hands.page))

        def read_controls():
            return list(collect_controls(hands.page))

        def read_clicks():
            return list(collect_clicks(hands.page))

        def read_tables():
            return list(collect_tables(hands.page))

        def execute(item, legs, from_leg):
            try:
                want = item_session_profile(legs)
            except ProfileRefused as exc:
                return {"outcome": "FAILED", "task_id": None,
                        "detail": f"session profile refused ({exc})"}
            problem = ensure_session(want)
            if problem is not None:
                if problem.startswith("invalid session profile"):
                    return {"outcome": "FAILED", "task_id": None,
                            "detail": problem}
                return {"outcome": "DEFERRED",
                        "task_id": item.get("task_id"),
                        "detail": problem}
            total = len(legs)
            task_id = item.get("task_id")
            task = None
            if task_id:
                try:
                    task = registry.get(task_id)
                except Exception:
                    task = None
            if task is None:
                task = registry.add(Task.create(
                    f"Chain of {total} legs",
                    allowed_tools=["browser"],
                    allowed_domains=opts["allow_domains"] or ["file:"],
                    allowed_paths=[]))
                task_id = task.task_id
            if not item.get("task_id"):
                # Bind at creation (before execution) so a crash
                # between creation and settle cannot orphan the task:
                # recovery reuses the bound task instead of creating
                # a duplicate (same contract as bind_task documents).
                try:
                    work_queue.bind_task(db, item["work_id"], owner,
                                         task_id)
                except WorkRefused as exc:
                    return {"outcome": "DEFERRED", "task_id": task_id,
                            "detail": f"cannot bind task ({exc})"}
            try:
                registry.checkout(task_id, "cli")
            except Exception as exc:
                return {"outcome": "DEFERRED", "task_id": task_id,
                        "detail": f"cannot take ownership ({exc})"}
            try:
                task = registry.refresh(task_id)
            except Exception as exc:
                return {"outcome": "DEFERRED", "task_id": task_id,
                        "detail": f"cannot refresh task state ({exc})"}
            if str(task.status.value) == "QUEUED":
                # Fresh task: enter RUNNING like every other chain
                # entry does. Without this run_chain()'s end-of-chain
                # COMPLETED transition (RUNNING-only) never fires and
                # the item would park as DEFERRED forever.
                try:
                    registry.set_status(task_id, Status.RUNNING)
                    task = registry.refresh(task_id)
                except Exception as exc:
                    return {"outcome": "DEFERRED", "task_id": task_id,
                            "detail": f"cannot start task ({exc})"}
            if task.status in TERMINAL:
                # Death between task settlement and queue settlement:
                # adopt the terminal outcome, never re-execute.
                mapping = {"COMPLETED": "COMPLETED",
                           "CANCELLED": "CANCELLED",
                           "FAILED": "FAILED"}
                return {"outcome": mapping[str(task.status.value)],
                        "task_id": task_id,
                        "detail": "task already terminal"}
            try:
                sched.enqueue(task_id)
                steps, _ = task_store.get_usage(db, task_id)
                if steps:
                    sched._steps_used[task_id] = steps
            except Exception:
                pass
            if str(task.status.value) == "PAUSED":
                try:
                    sched.resume(task_id)
                    task = registry.get(task_id)
                except Exception as exc:
                    return {"outcome": "DEFERRED", "task_id": task_id,
                            "detail": f"cannot resume paused ({exc})"}
            for pid in plan_store.plans_for_task(db, task_id):
                try:
                    plan, _, _ = plan_store.load_plan(db, pid)
                except Exception:
                    continue
                if plan.status not in PLAN_LIVE:
                    continue
                try:
                    plan_store.set_plan_status(db, pid, "SUPERSEDED")
                except Exception:
                    continue
                ns["audit"].log("PLAN_SUPERSEDED", task_id,
                               {"old_plan": pid, "new_plan": "-",
                                "at_step": 0, "snapshot_chars": 0})
            try:
                decider = pick_decider(argv, ns["approvals"], task_id)
            except UsageError as exc:
                return {"outcome": "DEFERRED", "task_id": task_id,
                        "detail": f"usage error: {exc}"}
            res = run_chain(taskloop, db, hands.open, read_pairs,
                            read_controls, task, {"legs": legs},
                            decider, from_leg=from_leg,
                            read_clicks=read_clicks,
                            read_tables=read_tables,
                            transfer_root=transfers_dir_for(opts))
            final = registry.get(task_id).status.value
            mapping = {"COMPLETED": "COMPLETED",
                       "CANCELLED": "CANCELLED", "FAILED": "FAILED"}
            return {"outcome": mapping.get(final, "DEFERRED"),
                    "task_id": task_id,
                    "detail": f"leg chain {res.status}: {res.detail}"}

        try:
            summary = serve(db, owner, execute, once=once,
                            max_items=max_items)
        except WorkRefused as exc:
            print(f"usage error: {exc}")
            return 1
        except Exception as exc:
            print(f"error: {exc}")
            return 1
        print(f"daemon: {owner} processed={summary['processed']}"
              f" settled={summary['settled']}"
              f" deferred={summary['deferred']}"
              + (" stopped=interrupted"
                 if summary.get("stopped") else " drained"))
        if summary.get("stopped") or summary["deferred"]:
            return 1
        return 0
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


def run_visibility(opts: dict) -> int:
    """Read-only task visibility (slice-39). No browser, no writes,
    no deciders: --list/--status never mix with execution flags."""
    if opts.get("list") and opts.get("status") is not None:
        print("usage error: --list and --status are mutually exclusive")
        return 1
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "resume",
                 "query", "chain_file", "from_leg", "decompose", "table",
                 "download", "dest", "upload", "src", "session_profile",
                 "sessions_dir", "clear_session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "daemon", "once"):
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


def run_clear_session(opts: dict) -> int:
    """Delete one named session profile and exit (V2-06 lifecycle).

    Pure filesystem operation: no browser, no database, no audit.
    Refuses unknown names and anything outside the sessions root.
    """
    from lakra.control.profiles import ProfileRefused, clear_profile
    for flag in ("road", "url", "text", "expect", "max_items",
                 "max_iters", "slots_json", "submit", "goal", "query",
                 "chain_file", "from_leg", "decompose", "resume",
                 "table", "download", "dest", "upload", "src",
                 "session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "daemon", "once", "list", "status", "state", "last",
                 "format", "allow_domains", "ram_floor_mb",
                 "cpu_ceiling", "audit", "profile_dir", "shots_dir",
                 "transfers_dir", "poll", "yes", "no"):
        if opts.get(flag):
            print(f"usage error: --clear-session-profile takes no"
                  f" --{flag.replace('_', '-')}")
            return 1
    name = opts.get("clear_session_profile")
    if not name:
        print("usage error: --clear-session-profile needs a name")
        return 1
    try:
        target = clear_profile(sessions_root_for(opts), name)
    except ProfileRefused as exc:
        print(f"refused: {exc}")
        return 1
    print(f"cleared session profile: {target.name}")
    return 0


def run_goal(argv, opts, audit_path, profile_dir, shots_dir,
             monitor) -> int:
    """Run a natural-language goal: shape prose to a road goal dict
    (slice-46/47 + NL click). The shaper is deterministic and
    refusal-first; observe, follow (with arrival phrase), search
    (with submit-control grounding), and click (with target phrase
    grounded by the click road) shape. The shaped dict flows through
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
                 "max_iters", "slots_json", "submit", "query", "table",
                 "download", "dest", "upload", "src",
                 "chain_file", "from_leg", "decompose",
                 "clear_session_profile",
                 "planner", "model", "model_url", "model_timeout_s",
                 "daemon", "once"):
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
    try:
        _sess_name, _sess_dir, _sess_reused = select_session(
            opts, profile_dir)
    except UsageError as exc:
        print(f"usage error: {exc}")
        return 1
    sessions = BrowserSessions(_sess_dir,
                               exclusive=session_exclusive(opts))
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor,
                         transfer_root=transfers_dir_for(opts))
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

        def read_clicks():
            return list(collect_clicks(hands.page))

        def read_tables():
            return list(collect_tables(hands.page))

        res = run_task(taskloop, db, hands.open, read_pairs,
                       read_controls, task, shaped, decider,
                       read_clicks=read_clicks, read_tables=read_tables,
                       transfer_root=transfers_dir_for(opts))
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

    print_session_lines(_sess_name, _sess_reused)
    return finish(res, "goal", task.task_id, registry, audit_path)


def main(argv: list[str]) -> int:
    try:
        opts = parse_args(argv)
    except UsageError as exc:
        print(f"usage error: {exc}\n\n{USAGE}")
        return 1

    if opts.get("list") or opts.get("status") is not None:
        return run_visibility(opts)

    if opts.get("clear_session_profile") is not None:
        return run_clear_session(opts)

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
    if opts.get("resume") and opts.get("chain_file") is not None:
        return run_chain_resume(argv, opts, audit_path, profile_dir,
                                shots_dir, monitor)
    if opts.get("resume"):
        return run_resume(argv, opts, audit_path, profile_dir, shots_dir,
                          monitor)
    if opts.get("chain_file") is not None or (
            opts.get("from_leg") is not None
            and opts.get("decompose") is None):
        if opts.get("chain_file") is None:
            print("usage error: --from-leg needs --chain-file"
                  " or --decompose")
            return 1
        return run_chain_file(argv, opts, audit_path, profile_dir,
                              shots_dir, monitor)
    if opts.get("chain_file") is not None or (
            opts.get("from_leg") is not None
            and opts.get("decompose") is None):
        if opts.get("chain_file") is None:
            print("usage error: --from-leg needs --chain-file"
                  " or --decompose")
            return 1
        return run_chain_file(argv, opts, audit_path, profile_dir,
                              shots_dir, monitor)
    if opts.get("decompose") is not None:
        return run_decompose(argv, opts, audit_path, profile_dir,
                             shots_dir, monitor)
    if opts.get("daemon") or opts.get("once"):
        return run_daemon(argv, opts, audit_path, profile_dir,
                          shots_dir, monitor)
    if opts.get("goal") is not None:
        return run_goal(argv, opts, audit_path, profile_dir,
                        shots_dir, monitor)

    for flag in ("planner", "model", "model_url", "model_timeout_s"):
        if opts.get(flag) is not None:
            print(f"usage error: --road takes no"
                  f" --{flag.replace('_', '-')} (planning flags need"
                  " --decompose)")
            return 1
    try:
        goal = build_goal(opts)
    except UsageError as exc:
        print(f"usage error: {exc}\n\n{USAGE}")
        return 1
    road = opts["road"]
    allow_domains = opts["allow_domains"] or ["file:"]

    # V2-05: only an explicit download execution gets a
    # download-accepting context; every other road keeps the default.
    # V2-06: an explicit --session-profile selects the profile
    # directory; otherwise the existing ephemeral behavior is exact.
    try:
        _sess_name, _sess_dir, _sess_reused = select_session(
            opts, profile_dir)
    except UsageError as exc:
        print(f"usage error: {exc}\n\n{USAGE}")
        return 1
    sessions = BrowserSessions(_sess_dir,
                               accept_downloads=(road == "download"),
                               exclusive=session_exclusive(opts))
    db = None
    try:
        sessions.launch()
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1
    try:
        db = Database()
        ns = build_stack(sessions, db, audit_path, shots_dir, monitor,
                         transfer_root=transfers_dir_for(opts))
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

        def read_clicks():
            return list(collect_clicks(hands.page))

        def read_tables():
            return list(collect_tables(hands.page))

        read_links = read_texts if road == "loop" else read_pairs
        res = run_task(taskloop, db, hands.open, read_links,
                       read_controls, task, goal, decider,
                       read_clicks=read_clicks, read_tables=read_tables,
                       transfer_root=transfers_dir_for(opts))
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

    if road == "table":
        # Machine-readable extraction output: the road's one
        # JSON-serializable dict, deterministic key order, printed
        # only on DONE (refusals carry no findings by contract).
        for found in (getattr(res, "findings", None) or []):
            print("table: " + json.dumps(found, sort_keys=True))
    if road in ("download", "upload"):
        # Machine-readable transfer summary, same DONE-only contract.
        for found in (getattr(res, "findings", None) or []):
            print(f"{road}: " + json.dumps(found, sort_keys=True))
    print_session_lines(_sess_name, _sess_reused)
    return finish(res, road, task.task_id, registry, audit_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
