# Testing Strategy (proposed)

No test framework in the repo today — adopted from slice-01: **pytest**.

| Level | What | When |
|---|---|---|
| Syntax/smoke | `compileall`, import checks | every slice |
| Unit | policy truth table (every L0–L4 example → expected verdict), task transitions (incl. double-checkout fails, terminal immutability), audit replay equality | slice-01+ |
| Concurrency | lock atomicity under threads (second checkout/lock-holder loses) | slice-02 |
| Contract | router rejects unregistered tool; out-of-scope action → ASK not ALLOW | slice-03 |
| Browser replay | scripted observer snapshots on fixed local pages; action→verify predicates; recovery on missing selector | slice-04/05 |
| Resource/budget | simulated pressure → pause/throttle/unload; overspend → hard-stop | slice-06 |
| Manual | per-slice checklist in `tasks.md` (run script, read logs, confirm behavior) | every slice |

Rule: no slice merges while any check is red; failing checks block the next
slice (constitution §8).
