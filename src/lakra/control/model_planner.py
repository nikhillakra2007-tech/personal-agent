"""V2-07: untrusted model planner behind the V2-01 decomposition interface.

The model proposes CHAIN LEGS (road dicts, the dispatcher's own
vocabulary) — never actions, executors, verdicts, or policies. Every
proposal passes, in order:

  model output -> JSON shape -> strict schema -> planning limits ->
  V2-01 road contract -> validate_chain() (by the caller)

and only then enters the existing dispatcher/router/policy/approval/
execution/verification/audit machinery. NOTHING the model emits can
reach an executor except through those gates: unknown roads, refused
roads, invented selectors, shell/code/policy/approval fields, and
oversized or nested structures all reject with ModelPlanRejected,
and the caller falls back to the deterministic decompose_goal().

Trust boundary (hard rules, all enforced below):
- road vocabulary is closed: observe/follow/click/search/table/
  download only. form (caller-stated fill values), upload (file
  selection), and loop (not chainable) are refused outright.
- the model never supplies CSS selectors: triggers arrive as
  natural-language phrases grounded at the execution edge by the
  existing ground_* functions (search legs keep submit_phrase for
  the unchanged edge-grounding loop in run_decompose).
- every *_url must repeat a URL present in the caller's own prose
  (exact match): invented destinations are smuggling, not planning.
- credential-shaped prose or values refuse before anything runs.
- steps are flat mappings (depth 1): any nested object except
  query:{"text": ...} rejects (no depth, no fan-out, no trees).
- metadata, when present, is constrained scalars and is IGNORED
  downstream — it can neither authorize nor execute anything.
- authority words (ALLOW/ASK/BLOCK/APPROVED/VERIFIED/COMPLETED,
  EXECUTE SHELL, IGNORE POLICY, ...) have no field to live in:
  inside text values they are inert page-match strings.

Planning limits (conservative, documented here):
  PLAN_VERSION=1, MIN/MAX_MODEL_LEGS=2/4 (the chain contract),
  MAX_OUTPUT_CHARS=4000, MAX_PLANNING_ATTEMPTS=2,
  GOAL_CAP_CHARS=1000, COMPLETION_BUDGET_TOKENS=512, model replans: 0
  (escalations use the existing deterministic replan path, never the
  model). Bounded context: goal text only, never page snapshots.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..models.base import BudgetExhausted, ModelError
from .decompose import DecomposeRefused, decompose_goal
from .planner import HINT_VALUE_CAP, SECRET_WORDS

PLAN_VERSION = 1
MIN_MODEL_LEGS = 2
MAX_MODEL_LEGS = 4
MAX_OUTPUT_CHARS = 4000
MAX_PLANNING_ATTEMPTS = 2
GOAL_CAP_CHARS = 1000
COMPLETION_BUDGET_TOKENS = 512
MAX_METADATA_KEYS = 8
MAX_METADATA_CHARS = 1000

URL_RE = re.compile(r"(https?://\S+|file://\S+)")

# Roads the model may propose (subset of chain legs). form carries
# caller-stated fill values, upload selects sandbox files, loop is
# not chainable: none of them is model-proposable, by design.
MODEL_ROADS = frozenset({
    "observe", "follow", "click", "search", "table", "download",
})

LEG_FIELDS = {
    "observe": ("url", "expect_text"),
    "follow": ("list_url", "goal_text", "body_expect"),
    "click": ("click_url", "click_text", "expect_text"),
    "search": ("search_url", "query", "submit_phrase", "expect_text"),
    "table": ("table_url", "table_text", "expect_text"),
    "download": ("download_url", "download_text", "dest_path",
                 "expect_text"),
}

URL_FIELDS = frozenset({
    "url", "list_url", "click_url", "search_url", "table_url",
    "download_url",
})

PROMPT_TEMPLATE = (
    "Propose a browser-task plan as strict JSON (no other text) with"
    " this exact shape:"
    ' {{"version": 1, "steps": [{{"road": ..., ...}}],'
    ' "metadata": {{}}}}.'
    f" Rules: 2 to {MAX_MODEL_LEGS} flat steps, no nesting except"
    ' "query": {"text": ...}; each step has exactly "road" plus that'
    " road's fields. Allowed roads/fields:"
    " observe(url, expect_text); follow(list_url, goal_text,"
    " body_expect); click(click_url, click_text, expect_text);"
    " search(search_url, query.text, submit_phrase, expect_text);"
    " table(table_url, table_text, expect_text);"
    " download(download_url, download_text, dest_path, expect_text)."
    " Repeat ONLY these page addresses (never invent URLs): {urls}."
    " Never include credentials, passwords, secrets, tokens, shell"
    " commands, code, selectors, or policy/approval decisions."
    " Task goal: {goal}")


class ModelPlanRejected(ValueError):
    """Model output unusable: malformed, over-limit, unsafe, or
    unsupported. Callers fall back to deterministic V2-01 planning
    (MODEL_PLAN_REJECTED) and never launch a browser for planning."""


@dataclass
class PlannerContext:
    """Bounded planning input: caller prose only (capped), attempt
    and token budgets. No snapshots, no credentials, no history."""
    goal: str
    max_attempts: int = MAX_PLANNING_ATTEMPTS
    budget_tokens: int = COMPLETION_BUDGET_TOKENS


@dataclass
class ModelPlanAttempt:
    planner: str = "model"  # model | deterministic | model->fallback
    provider: str = ""
    calls_made: int = 0
    tokens_used: int = 0
    latency_ms: int = 0
    parsed: bool = False
    validation: str = ""
    rejection_reason: str | None = None
    fallback_used: bool = True


def _goal_urls(prose: str) -> list[str]:
    """URLs stated by the caller (exact, scheme-checked). Model *_url
    fields must repeat one of these verbatim."""
    out = []
    for raw in URL_RE.findall(prose):
        clean = raw.rstrip(").,;\"'")
        try:
            parts = urlparse(clean)
        except Exception:
            continue
        if parts.scheme in ("http", "https", "file") and (
                parts.netloc or parts.path):
            out.append(clean)
    return out


def _check_prose(prose: str) -> str:
    if not isinstance(prose, str) or not prose.strip():
        raise ModelPlanRejected("empty goal: nothing to plan")
    lowered = prose.lower()
    words = set(lowered.replace("=", " ").replace(":", " ").split())
    if words & SECRET_WORDS:
        raise ModelPlanRejected("credential-shaped goal refused")
    return prose.strip()


def _check_text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelPlanRejected(f"{what} must be a non-empty string")
    if len(value) > HINT_VALUE_CAP:
        raise ModelPlanRejected(f"{what} over cap")
    words = set(value.lower().replace("=", " ").replace(":", " ")
                .replace("/", " ").split())
    if words & SECRET_WORDS:
        raise ModelPlanRejected(f"{what} looks secret-shaped: rejected")
    return value.strip()


def _check_scalar(value, what: str):
    """Flat-structure gate: only JSON scalars allowed (depth limit).
    Mappings and sequences are branching/depth smuggling, not legs."""
    if isinstance(value, (dict, list)):
        raise ModelPlanRejected(
            f"{what} must be a scalar (no nested structures)")
    if isinstance(value, str):
        return _check_text(value, what)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ModelPlanRejected(f"{what} has an unsupported type")


def _validate_metadata(meta) -> None:
    if meta is None:
        return
    if not isinstance(meta, dict):
        raise ModelPlanRejected("metadata must be an object")
    if len(meta) > MAX_METADATA_KEYS:
        raise ModelPlanRejected("metadata over key cap")
    try:
        raw = json.dumps(meta)
    except (TypeError, ValueError) as exc:
        raise ModelPlanRejected(
            f"metadata not serializable ({exc})") from None
    if len(raw) > MAX_METADATA_CHARS:
        raise ModelPlanRejected("metadata over size cap")
    for key, value in meta.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ModelPlanRejected("metadata keys must be short strings")
        if isinstance(value, (dict, list)):
            raise ModelPlanRejected(
                "metadata values must be scalars")
        if isinstance(value, str) and len(value) > HINT_VALUE_CAP:
            raise ModelPlanRejected("metadata value over cap")


def _validate_leg(step, pos: int, allowed_urls: list[str]) -> dict:
    """One proposed step -> dispatcher-ready leg (with "road" key).
    Anything outside the closed road/field vocabulary rejects."""
    if not isinstance(step, dict):
        raise ModelPlanRejected(f"step {pos} must be an object")
    road = step.get("road")
    if road not in MODEL_ROADS:
        if isinstance(road, str):
            raise ModelPlanRejected(
                f"step {pos} road {road!r} unsupported"
                " (no such execution road)")
        raise ModelPlanRejected(f"step {pos} needs a road name")
    fields = LEG_FIELDS[road]
    keys = set(step)
    if keys != {"road"} | set(fields):
        raise ModelPlanRejected(
            f"step {pos} ({road}) needs exactly {sorted(fields)},"
            f" got {sorted(keys)}")
    leg: dict = {"road": road}
    for name in fields:
        value = step[name]
        if name == "query":
            if not isinstance(value, dict) or set(value) != {"text"}:
                raise ModelPlanRejected(
                    f"step {pos} query must be exactly {{'text': ...}}")
            leg[name] = {"text": _check_text(value["text"],
                                             f"step {pos} query.text")}
        elif name in URL_FIELDS:
            text = _check_text(value, f"step {pos} {name}")
            if text not in allowed_urls:
                raise ModelPlanRejected(
                    f"step {pos} {name} repeats no goal URL"
                    " (invented destinations refused)")
            leg[name] = text
        else:
            leg[name] = _check_scalar(value, f"step {pos} {name}")
    return leg


def parse_proposed_plan(obj, prose: str) -> list:
    """Untrusted object -> validated leg list (V2-01 road dicts).
    Raises ModelPlanRejected for anything else. Pure function: no
    browser, no policy, no execution."""
    _check_prose(prose)
    if not isinstance(obj, dict):
        raise ModelPlanRejected("plan must be an object")
    if obj.get("version") != PLAN_VERSION:
        raise ModelPlanRejected(
            f"plan version must be {PLAN_VERSION}")
    steps = obj.get("steps")
    if not isinstance(steps, list):
        raise ModelPlanRejected("plan steps must be a list")
    if not (MIN_MODEL_LEGS <= len(steps) <= MAX_MODEL_LEGS):
        raise ModelPlanRejected(
            f"plan needs {MIN_MODEL_LEGS}..{MAX_MODEL_LEGS} steps,"
            f" got {len(steps)}")
    allowed = set(_goal_urls(prose))
    _validate_metadata(obj.get("metadata"))
    extra = set(obj) - {"version", "steps", "metadata"}
    if extra:
        raise ModelPlanRejected(
            f"plan carries unexpected fields {sorted(extra)}")
    return [_validate_leg(step, n, sorted(allowed))
            for n, step in enumerate(steps, 1)]


def _extract_json(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        raise ModelPlanRejected(
            f"model output over {MAX_OUTPUT_CHARS} chars")
    s = text.strip()
    if "{" in s:
        s = s[s.find("{"):s.rfind("}") + 1]
    return s


def build_prompt(prose: str) -> str:
    """Frozen planning prompt: goal (capped) + repeated-URL allowlist.
    No snapshots, no credentials, no history ride along."""
    goal = prose.strip()[:GOAL_CAP_CHARS]
    urls = _goal_urls(goal)[:8]
    return PROMPT_TEMPLATE.format(
        urls=", ".join(urls) if urls else "(none stated: omit URLs)",
        goal=goal)


def plan_with_model(prose: str, provider,
                    context: PlannerContext | None = None
                    ) -> tuple[list, ModelPlanAttempt]:
    """Ask the provider for legs. Returns (legs, attempt). Raises
    ModelPlanRejected on provider failure, timeout, over-output,
    malformed JSON, schema/limit/road/URL failure. Never falls back
    internally and never touches the browser: the caller decides."""
    context = context or PlannerContext(goal=prose)
    attempt = ModelPlanAttempt(provider=getattr(provider, "name", "?"))
    _check_prose(prose)
    attempts = max(1, min(int(context.max_attempts or 1),
                          MAX_PLANNING_ATTEMPTS))
    prompt = build_prompt(prose)
    started = time.monotonic()
    try:
        for _ in range(attempts):
            try:
                resp = provider.complete(
                    prompt, budget_tokens=context.budget_tokens)
            except (ModelError, BudgetExhausted) as exc:
                attempt.calls_made += 1
                attempt.rejection_reason = f"provider: {exc}"
                break
            attempt.calls_made += 1
            attempt.tokens_used += resp.total_tokens
            try:
                obj = json.loads(_extract_json(resp.text))
            except (json.JSONDecodeError, ValueError,
                    ModelPlanRejected) as exc:
                attempt.rejection_reason = f"output: {exc}"
                continue
            if not isinstance(obj, dict):
                attempt.rejection_reason = "schema: not an object"
                continue
            attempt.parsed = True
            try:
                legs = parse_proposed_plan(obj, prose)
            except ModelPlanRejected as exc:
                attempt.rejection_reason = f"schema: {exc}"
                continue
            attempt.validation = f"accepted {len(legs)} legs"
            attempt.fallback_used = False
            return legs, attempt
    finally:
        attempt.latency_ms = int(
            (time.monotonic() - started) * 1000)
    if attempt.rejection_reason is None:
        attempt.rejection_reason = "provider: no attempt made"
    err = ModelPlanRejected(attempt.rejection_reason)
    err.attempt = attempt  # fallback callers keep the measurements
    raise err


def plan_with_fallback(prose: str, provider,
                       context: PlannerContext | None = None
                       ) -> tuple[list, ModelPlanAttempt]:
    """Model legs, or deterministic V2-01 legs on any rejection.

    The deterministic fallback is decompose_goal() unchanged; when it
    too refuses, DecomposeRefused propagates (existing CLI prints
    "refused", no browser launches either way). attempt.planner
    reports model | deterministic | model->fallback for the
    observability line.
    """
    try:
        legs, attempt = plan_with_model(prose, provider, context)
        attempt.planner = "model"
        return legs, attempt
    except ModelPlanRejected as exc:
        attempt = getattr(exc, "attempt", None) or ModelPlanAttempt(
            provider=getattr(provider, "name", "?"))
        attempt.rejection_reason = f"MODEL_PLAN_REJECTED ({exc})"
        attempt.fallback_used = True
    try:
        legs = decompose_goal(prose)
    except DecomposeRefused:
        attempt.planner = "model->fallback"
        attempt.validation = "fallback refused"
        raise
    attempt.planner = "model->fallback"
    attempt.validation = f"fallback accepted {len(legs)} legs"
    return legs, attempt
