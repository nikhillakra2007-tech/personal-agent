# Handoff prompt — Automate the Oracle Academy site with the Lakra agent

Copy everything below the line into any agent/session to continue this task
from scratch.

---

## Goal

Automate the user's Oracle Academy workflow using the browser-automation agent
already in this repository (Lakra 2.0: local Playwright agent, Python 3.11/3.12,
L0–L4 policy engine, dual-plane control/execution split). The user wants a
**multi-step chain flow** that runs against Oracle's sign-in + Academy pages.

User decisions (already confirmed):
1. **Task type**: multi-step chain (`--chain-file`, 2–4 legs, one task).
2. **Auth**: authenticated — use a **named session profile**; the human signs
   in manually once, later runs reuse the saved cookies/state.
3. **Start URL**: `https://signon.oracle.com/signin`
4. **Post-login destination**: `https://education.oracle.com/` (verify the
   signed-in dashboard content).
5. **Chain intent**: verify signed-in dashboard (read-only observe legs).

## Hard constraints (from docs/capability-matrix.md + code)

- **No login road exists.** Credential entry is blocked at policy L4
  (credential capture / auth bypass are hard-blocked). Never try to automate
  typing credentials. The only sanctioned pattern: headed manual sign-in into
  the same persistent profile dir that `lakra_do.py --session-profile NAME`
  reuses (profile root: `var/sessions/<name>`).
- Chains are MIN 2 / MAX 4 legs; roads allowed in chains:
  `follow|observe|form|search|click|table|download|upload` (NO `loop`).
  Leg key sets are enforced in `src/lakra/control/chain.py::LEG_KEYS`
  (observe = `{"road","url","expect_text"}` + optional `"goal"`).
- Policy bounds check: `browser.navigate` target host must be in the task's
  `allowed_domains`, else the step is ASK (parks). CLI: repeat
  `--allow-domain HOST` (host only, e.g. `signon.oracle.com`, no scheme/path).
- Everything L0/L2 runs without approvals; `--yes` scripts the decider so no
  TTY prompt blocks unattended runs. Exit codes: 0 DONE, 2 denied, 1 other.
- `expect_text` is a **literal substring of `body.innerText()`** (case-
  sensitive), checked by the observe road's snapshot step
  (`planner._observe_steps`: navigate `url_is` retry 2, then snapshot
  `text_contains` retry 1).

## Environment findings (this Windows box, verified live)

- Network to Oracle is **flaky**: local router DNS (192.168.1.1) intermittently
  SERVFAILs → `ERR_NAME_NOT_RESOLVED` in Chromium even when `nslookup` works.
  Retrying usually recovers. `education.oracle.com` returns
  `ERR_HTTP2_PROTOCOL_ERROR` **consistently in headless Chromium (0/9)** but
  loads fine in **headed** Chromium (2/2). DoH to 1.1.1.1/dns.google works.
- Headless-reachable (verified with Lakra's exact `wait_until="load",
  timeout=15000`):
  - `https://signon.oracle.com/signin` — loads ~0.7s but **body text is EMPTY
    right at load** (Angular SPA renders ~1–4s later; full text seen after
    4–8s wait: "Sign in to Oracle Username or email Next Forgot username?
    Don't have an Oracle Account? Create Account … Terms of Use Privacy
    Policy"). This is the main open risk for leg 1 (see TODO).
  - `https://www.oracle.com/in/education/` — this is where
    `education.oracle.com/` geo-redirects anyway (Oracle University page);
    loads 0.8s with text immediately ("Oracle University … Training
    Certification …"). Use this as the dashboard leg instead of the
    education root (headless-blocked).
  - `https://www.oracle.com/academy/` → redirects to
    `https://academy.oracle.com/` — loads 1.1s, text includes
    "Oracle Academy" (page currently also shows a real site-wide banner
    "Oracle Academy systems are currently experiencing technical
    difficulties…").
- Playwright chromium installed (`%LOCALAPPDATA%\ms-playwright\chromium-1243`),
  `.venv` has playwright/psutil/pytest.

## Work already done (files)

- `scripts/manual_signin.py` (NEW) — headed manual sign-in bootstrap:
  `python scripts/manual_signin.py --profile oracle --url
  https://signon.oracle.com/signin`. Validates the profile NAME through
  `lakra.control.profiles` (same confinement as V2-06), launches
  `launch_persistent_context(var/sessions/<name>, headless=False)`, human
  signs in in the window, Enter closes cleanly. `--help` + traversal refusal
  verified; not yet exercised end-to-end with a real login.
- `chains/oracle-academy.json` (NEW) — current 2-leg chain (needs updating to
  the 3-leg version below): signon observe expect "Create Account"; education
  observe expect "Oracle University".
- `var/tmp/probe_*.py` — throwaway diagnosis scripts (safe to delete).
- NOT yet run: the chain end-to-end through `lakra_do.py`.

## Mid-session update (2026-10-05, after first chain runs)

- First chain runs failed; diagnosis completed:
  1. Custom per-leg `"goal"` strings in the chain file are REFUSED by the
     planner ("no template matches goal ...") — leg `goal` must be omitted
     so `leg_goal_text()` supplies the road default ("Show <expect>").
  2. `education.oracle.com` headless-blocked (ERR_HTTP2_PROTOCOL_ERROR);
     chain leg 2 was rewritten to `https://www.oracle.com/in/education/`
     (the geo-redirect target, same Oracle University content) and leg 3
     added: `https://www.oracle.com/academy/` (→ academy.oracle.com).
  3. Chain final shape (chains/oracle-academy.json): 3 observe legs —
     signon (expect "Create Account"), /in/education/ (expect "Oracle
     University"), /academy/ (expect "Oracle Academy").
  4. **Root failure cause: local DNS.** Fresh Chromium launches failed
     ~35% with ERR_NAME_NOT_RESOLVED while Python/nslookup succeeded
     (Windows resolver retries mask it). Evidence: plain chromium 6/10,
     6/8 ok; warm single browser 6/6; chromium with secure-DoH flags
     (1.1.1.1) 8/8. Lakra's 3 nav retries land in <0.4s, all inside one
     poisoned window → leg STOPPED.
- **System DNS was changed (user-approved, elevated netsh/PowerShell):**
  Wi-Fi adapter now 1.1.1.1 + 8.8.8.8 (was DHCP/192.168.1.1). Revert:
  `netsh interface ip set dns name=Wi-Fi source=dhcp` (elevated).
- **Right after the change the entire WAN went down** (TCP to any host
  fails, UDP53 times out even to 8.8.8.8, gateway pings 2ms) — an
  upstream/line outage, NOT caused by the DNS change (DNS settings
  cannot break TCP443). Verify recovery before drawing conclusions:
  `curl -sS -o /dev/null -w "%{http_code}\n" --max-time 6
  https://example.com` → need 200. Then re-measure plain chromium
  cold-launch success rate ×8 before running the chain.
- Chain status via `lakra_do.py --list --state all` /
  `--status TASK_ID --last 30`; EXECUTION_FAILED payloads in
  `var/audit-do.jsonl` carry the exact navigation error.

## Next steps (in order)

1. **Wait for WAN recovery, then re-measure** plain chromium cold-launch
   resolution (×8) with the new 1.1.1.1 DNS. If still failing, revisit the
   DoH-flags option for BrowserSessions (proven 8/8) or the retry wrapper —
   but confirm the line is actually up first.
2. **Signon SPA timing (open risk)**: observe's snapshot fires right after
   `load`, but signon's body is empty until hydration (~1-4s). The recovery
   step waits only 500ms before final re-verify. If leg 1 fails on
   `text_contains "Create Account"` with a healthy network, investigate
   `loops.run_task`/`planner` for a sanctioned wait — do NOT weaken the
   expect; any src change needs the full suite green.
3. **Chain is finalized** as 3 legs in `chains/oracle-academy.json` (no
   per-leg `"goal"` keys — planner refuses them):
   observe signon ("Create Account"), observe www.oracle.com/in/education/
   ("Oracle University"), observe www.oracle.com/academy/ ("Oracle Academy").
4. **Run it end-to-end** (rerun on transient failures):
   ```
   .venv/Scripts/python.exe scripts/lakra_do.py --chain-file chains/oracle-academy.json \
     --session-profile oracle --allow-domain signon.oracle.com \
     --allow-domain www.oracle.com --allow-domain academy.oracle.com --yes
   ```
   Expect `legs: 3/3 done`, exit 0. Also verify `--status TASK_ID` shows legs.
4. **Have the user sign in** via `scripts/manual_signin.py`, re-run the chain,
   then **tighten leg 1/2 expects to signed-in-only markers** (e.g. their name
   or "Sign Out") so the chain actually fails when the session expires —
   capture the real signed-in body text with a quick headless dump first.
5. Report the "Oracle Academy systems currently experiencing technical
   difficulties" banner to the user; it's a real site status, not an agent bug.
6. Keep `var/` gitignored (runtime only); committed artifacts live in
   `scripts/` and `chains/`. Do not commit/push unless asked.

## Verification standard

Chain DONE is not enough — confirm each leg's `expect_text` was genuinely
present (check audit tail via `lakra_do.py --status`), confirm the session
profile actually reuses state (second run must not show the sign-in form
after login), and run `pytest tests -q` after any src/ change.
