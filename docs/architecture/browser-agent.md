# Browser Agent Architecture (proposed)

Highest-priority execution subsystem. Structured interaction first, visual
fallback second, verification always.

## 1. Layout

```
execution/browser/
  controller.py    # owns the per-task loop, sequences observer→actions→verification
  observer.py      # snapshots: URL, title, DOM/a11y tree (scoped), selected network/page state
  sessions.py      # launch, profiles (isolated automation profile vs user Chrome channel),
                   # tabs, navigation history, downloads, popup handling
  actions.py       # navigate, search-form fill, click, type, scroll, keypress, tab ops, waits
  verification.py  # per-action predicates (URL changed? element gone? text present? download done?)
  recovery.py      # bounded retry → alternate selector/strategy → replan signal → ask/stop
```

Only `controller` talks to the TOOL ROUTER; nothing outside `browser/` imports
the rest. This mirrors Paperclip's adapter boundary: invocation, observation,
and cancellation through one seam.

## 2. The Loop (per action, no exceptions)

```
OBSERVE → UNDERSTAND → PLAN NEXT ACTION → POLICY CHECK → ACT
  → OBSERVE AGAIN → VERIFY → CONTINUE / RETRY / REPLAN / ASK / STOP
```

- OBSERVE captures state *before* (baseline for comparison).
- POLICY CHECK re-evaluates the concrete action (domain + effect), not just
  the plan's intent — a plan approved for "read docs" does not smuggle a
  "submit form" (L3) inside it.
- VERIFY compares after-state against the action's declared predicate with a
  timeout; on failure, RECOVERY tries ≤N alternates (re-query selector, scroll
  into view, wait+retry), then escalates to REPLAN (new plan from fresh
  observation) or ASK (needs user) or STOP (budget/attempts exhausted).
- Every step appends audit events (`BROWSER_NAVIGATE/CLICK/TYPE`, `SCREENSHOT`,
  `VERIFICATION`, `RETRY`, `REPLAN`).

## 3. Capability Ladder

1. Launch/session/navigate/tabs (slice-04, read-only observer first).
2. Read: snapshots, a11y, screenshots, downloads listing.
3. Act: click/type/scroll/keys/waits/popups (slice-05) — L1 by default.
4. Verify + recover (slice-05) — predicates per action class.
5. Multi-step workflows with submission policy: safe portions automatic, L3
   items (submit/assignment/payment/publish) pause for approval, resume with
   the approval token — never pre-approved silently.
6. Visual fallback: screenshot-grounded click/type only when structured refs
   fail twice; flagged in audit as degraded mode.

## 4. Safety & Resources

- Allowed-domains per task; navigation outside → ASK (L2) or BLOCK.
- No credential harvesting: login flows stop at the credential field and ASK
  (user types secrets, or approves a stored ref — L3).
- CAPTCHA/security interstitial → STOP + report (L4: never defeat).
- One browser-task foreground at a time if physical input needed; background
  sessions preferred (isolated profile, no user-profile mutation).
- Screenshots redacted for secret fields before logging; full images stay local.

## 5. Foreground / Background (with computer control)

- **Background (default):** Playwright-driven isolated session; no
  mouse/keyboard capture; user unaffected.
- **Foreground (exception):** needs physical input → acquire
  computer-control lock (indicator shown, audit `LOCK_ACQUIRED`), execute
  minimal steps, release + audit `LOCK_RELEASED`. Lock watchdog force-releases
  on user input. Only one holder; scheduler queues others.
