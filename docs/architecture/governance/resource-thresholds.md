# Resource thresholds — measurement note (slice-22 C-D)

Behavior-neutral: no threshold was changed. This note records measured
host telemetry so the `under_pressure()` defaults in
`src/lakra/resources/monitor.py` rest on evidence, per Constitution §2.6.

## Host (2026-09-28, normal dev load: editor, browsers, OneDrive active)

| State | RAM avail | CPU % | Lakra RSS | Browser RSS | VRAM free | GPU temp | `under_pressure()` |
|---|---|---|---|---|---|---|---|
| Idle dev load (3 samples) | ~1690 MB / 16068 | 21–38 | ~19 MB | 0.0 | 5620 MB | 56 °C | True — `low RAM: 1695 MB available` |
| + headless browser, fixture page | 1414 MB | 19 | ~32 MB | 0.0 | 5586 MB | 56 °C | True — `low RAM: 1414 MB available` |

Defaults under test: `ram_floor_mb=2048`, `vram_floor_mb=512`,
`cpu_ceiling=90.0`.

## Reading

- **RAM gate is the binding constraint on a shared dev box.** With everyday
  load, available RAM sits below the 2048 MB floor, so a run wired to the
  REAL monitor pauses immediately — the guard working as designed
  (fail-safe: pause, never proceed blind). Lakra itself is light (~20–
  30 MB); the pressure comes from the user's own workload, which is
  exactly what the gate exists to yield to.
- **VRAM/CPU gates have wide headroom** (5.6 GB free of 6 GB, CPU far
  below 90%), so model/browser starts are not gated on this host today.
- **Verdict: keep the defaults.** They are conservative in the safe
  direction (more pausing, never less). Lowering the RAM floor to fit a
  loaded dev box would trade user-machine responsiveness for fewer
  pauses — rejected without a dedicated profiling slice.
- **Caveat (observed, not fixed here):** `browser_rss_mb` reads 0.0 with
  the current headless shell — the process-name match
  (`headless_shell.exe`/`chrome.exe`) undercounts on this setup. Browser
  cost is therefore invisible to per-process attribution; the RAM-
  available gate still covers it in aggregate. Fixing attribution is
  deferred post-V1 with the foreground-control work (C-A/C-B).

## Operational note

Scripted/stub monitors remain the tool for deterministic tests and
replays. The real monitor is for live runs, where a pause on this host
under dev load is EXPECTED, not a failure — relieve load (or raise the
floor deliberately) and resume.
