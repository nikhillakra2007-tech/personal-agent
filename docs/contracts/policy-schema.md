# Policy Schema (draft v0.1 — slice-01 target)

```python
@dataclass
class Action:
    kind: str        # e.g. "browser.navigate" | "browser.click" | "browser.type"
                     #        "browser.submit" | "fs.write" | "terminal.run" ...
    target: str      # URL, domain, path, or command summary
    effect: str      # "read" | "reversible" | "bounded-mutation" | "consequential" | "blocked"
    task_id: str

Verdict = "ALLOW" | "ASK" | "BLOCK"
```

**Evaluation order (deterministic, first match wins):**

1. L4 blocklist (`effect == "blocked"` or kind/target on denylist) → BLOCK.
2. L3 list (submit/send/publish/pay/important-delete/private-upload, or
   `permission_level < 3` with consequential effect) → ASK.
3. Bounds check (domain/path/tool not in task allowlists) → ASK (L2-out-of-
   bounds) — never silently ALLOW.
4. L2 within bounds → ALLOW (logged as controlled).
5. L0/L1 → ALLOW (logged).

**Approval record:** ASK creates `{ approval_id, action, requested_at,
decided: None|approved|denied, decided_at }`; task moves to
`WAITING_APPROVAL`; on approval the *same concrete action* re-evaluates (no
substitution); denial → STOP that branch (replan or end).
