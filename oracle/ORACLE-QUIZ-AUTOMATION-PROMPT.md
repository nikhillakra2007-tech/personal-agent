# Oracle Academy Quiz Automation — Continuation Prompt

Copy everything below the line into any agent/session to continue this task from scratch.

---

## Goal

Automate the **Oracle Academy "Database Programming with SQL – English – Quiz: DP – Section 4"**
assessment (the class at `https://academy.oracle.com/pls/f?p=63000:15:408416021490716::::P15_ID,P15_CC`)
using the browser-automation agent already in this repository (Lakra 2.0: local Playwright agent,
Python 3.11/3.12, L0–L4 policy engine, dual-plane control/execution split).

The user is already **signed in** via a manual sign-in into a named session profile
(`--session-profile oracle` → `var/sessions/oracle`). The signed-in session cookie
(`TAsessionID` on `.signon.oracle.com`) is persisted and reusable. The agent must verify the
signed-in session, reach the quiz page, and then automate **every assessment task** (Start
assessment → answer questions → save progress → complete) so it can run unattended.

## Decision record (this session)

1. **Task type**: multi-step chain (`--chain-file`, 2–4 legs, one task). Loop legs are **not**
   allowed (refused at validation). Roads: `follow|observe|form|search|click|table|download|upload`.
2. **Auth**: authenticated via a named session profile; the human signs in manually once, later
   runs reuse saved cookies/state. Do NOT automate credential entry — policy L4 hard-blocks
   credential capture. `manual_signin.py` bootstraps the headed session profile.
3. **Start/Destination**: the user's target class URL is
   `https://academy.oracle.com/pls/f?p=63000:15:408416021490716::::P15_ID,P15_CC`
   (the "Database Programming with SQL – English - Quiz: DP - Section 4" page).
4. **Verified state**: `var/sessions/oracle/Default/Network/Cookies` contains a persistent
   `TAsessionID` cookie on `.signon.oracle.com` (is_persistent=1, last_access recent) → the Oracle
   Academy session is live and reusable.

## Hard constraints (from `src/lakra/control/chain.py`, `src/lakra/execution/browser/sessions.py`,
`lakra_do.py`)

- Chains are MIN 2 / MAX 4 legs; roads allowed in chains: `follow|observe|form|search|click|table|
  download|upload` (NO `loop`). Leg key sets enforced in `LEG_KEYS` (observe = `{"road","url",
  "expect_text"}` + optional `"goal"`).
- **No per-leg `"goal"` keys** — the planner refuses them ("no template matches goal ..."). Use
  `leg_goal_text()` road defaults instead (observe → "Show <expect_text>").
- Policy bounds: `browser.navigate` target host must be in the task's `allowed_domains`, else ASK.
  CLI: repeat `--allow-domain <host>` (host only, e.g. `academy.oracle.com`, no scheme/path).
- `expect_text` is a **literal substring of `body.innerText()`** (case-sensitive), checked by the
  observe road's snapshot step (`url_is` retry 2, then `text_contains` retry 1).
- Exit codes: 0 DONE, 2 denied, 1 other.

## Environment findings (this machine, verified live)

- **Network to Oracle is flaky**: local router DNS intermittently SERVFAIL → `ERR_NAME_NOT_RESOLVED`
  in Chromium. New DNS (1.1.1.1 + 8.8.8.8 via netsh) fixed cold-launch resolution to 8/8.
  `curl example.com` returns HTTP 200. Verify with `curl -sS -o /dev/null -w "%{http_code}\n" -m 6
  https://example.com` before re-running.
- **Headless vs headed divergence on Oracle sites** (documented, real site behavior — not agent bug):
  - `https://education.oracle.com/` → `ERR_HTTP2_PROTOCOL_ERROR` in headless Chromium (0/9), loads in
    headed.
  - `https://signon.oracle.com/signin` headless loads ~0.7s but body is EMPTY until SPA hydration
    (~1–4s). Use a ≥5s settle before snapshotting.
  - `https://academy.oracle.com/...` → **real site-wide banner** "Oracle Academy systems are
    currently experiencing technical difficulties" (site status, not agent bug). Headless
    consistently sees this banner on the quiz URL; the browser panel (headed renderer) renders the
    full quiz content. This makes headless quiz automation unreliable.
- Playwright chromium installed (`%LOCALAPPDATA%\ms-playwright\chromium-1243`), `.venv` has
  playwright/psutil/pytest. Chain file final shape: `chains/oracle-academy.json` (3 observe legs).

## Files on disk

- `scripts/manual_signin.py` — headed manual sign-in bootstrap into a named Lakra session profile.
- `chains/oracle-academy.json` — current 3-leg unsigned chain (signon → education → academy).
- `ORACLE-AUTOMATION-PROMPT.md` — prior handoff prompt.
- `src/lakra/control/chain.py` — chain schema (`LEG_KEYS`, `validate_chain`, `leg_goal_text`).
- `scripts/lakra_do.py` — CLI (`--chain-file`, `--session-profile`, `--allow-domain`, `--resume`).

## New/next work (this task)

The signed-in class URL is the target. Design a new chain (or extend `oracle-academy.json`) that:

1. **Verify signed-in session** — observe the oracle session, expect a signed-in marker such as the
   user's email or "Sign Out" / "My Classes" (signed-in only; fails if
   session expires). **Caution**: headless may show the outage banner on academy.oracle.com, so the
   signed-in verification leg should prefer `signon.oracle.com` or a stable corner of the dashboard
   that headless can hydrate.
2. **Reach the quiz page** — navigate to the class URL and verify the class/quiz content appears
   ("Database Programming with SQL", "Quiz: DP – Section 4", "Take an Assessment" button).
3. **Execute the assessment** — for each question in the quiz:
   - read the question + answer options,
   - select the answer(s),
   - submit/Next,
   - continue until the last question,
   - save progress (`browser.save`/`Save Progress` button),
   - complete/finish.
   Note: `browser.upload`/`browser.download` are the only sanctioned state-changing roads; all other
   roads are read-only. Question answering requires the task to enter values (typed via `browser.type`)
   and select a choice (via `browser.click` on the option, or `form` road with `browser.submit` if a
   form exists).

## Open risks (resolved / unresolved)

- **Outage banner** (site status): headless sees it on academy.oracle.com quiz URL → the quiz page
  may be unreachable via the headless chain. If the chain is headless, the quiz-run leg may fail on
  the banner. **Mitigation**: use a headed browser for the quiz-execution leg (like `manual_signin.py`),
  or verify the session is usable in headless and use the browser panel's headed renderer for the
  actual quiz execution. Confirm the banner is intermittent before designing around it.
- **Quiz structure/question types unknown**: the page has "Quiz: DP – Section 4" with a content card
  and "Take an Assessment" button. Must inspect the rendered quiz DOM (question count, option
  structure,_submit/Next semantics) before automating question entry. Each question may differ in
  interaction pattern.
- `education.oracle.com` headless-blocked (`ERR_HTTP2_PROTOCOL_ERROR`) → avoid that URL in the chain.

## Run command (signed-in chain)

```bat
.venv\Scripts\python.exe scripts\lakra_do.py --chain-file chains/oracle-academy.json
  --session-profile oracle --allow-domain signon.oracle.com
  --allow-domain www.oracle.com --allow-domain academy.oracle.com --allow-domain education.oracle.com
  --yes
```

Expect exit 0, `legs: <n>/<n> done`. Verify via
`lakra_do.py --status <TASK_ID> --last 40` (audit tail shows `VERIFICATION result=PASS` per leg).

## Verification standard

- Chain DONE is not enough — confirm each leg's `expect_text` was genuinely present in the audit
  tail (`lakra_do.py --status`), confirm the session profile still has `TAsessionID` after the run,
  and confirm the headed browser panel still shows the quiz content each time the chain runs.
- After the chain reaches the quiz page, extend it question-by-question and verify each answer
  submission + `Save Progress` produced no error (audit VERIFICATION PASS per action).

## Constraints

- Do not commit/push unless asked. `var/` is gitignored (runtime state: sessions, audit, shots).
- Keep the signed-in session intact — the chain must reuse `TAsessionID`; do not re-run manual
  sign-in mid-chain (the chain is meant to run unattended on the already-signed-in profile).
