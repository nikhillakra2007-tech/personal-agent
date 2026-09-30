# Approval-Token Binding (canonical, slice-07)

## The bound tuple

A token authorizes exactly one execution of exactly one action. Binding is
the ordered 4-tuple, compared by field equality (no hashing, no
normalization — byte-identical or rejected):

```
(task_id, action.kind, action.target, action.effect)
```

## Token record

```python
ApprovalToken:
    token_id: str       # opaque uuid4; the only thing the caller holds
    approval_id: str    # source approval (must be decided == "approved")
    task_id, kind, target, effect  # the bound 4-tuple (copied at mint)
    expires_at: str     # ISO-8601 UTC; mint TTL default 300 s
    used: bool          # False until burned
```

Mint (`Approvals.mint_token`) requires: approval exists in the approved
store, `decided == "approved"`, not expired. Denied/expired/unknown approvals
cannot mint — there is no token for "no".

## Redemption (`Approvals.redeem`) — all must hold, else reject, no state change

1. Token exists, `used == False`, `expires_at` in the future.
2. Source approval still `decided == "approved"`.
3. Task status is `RUNNING` (the post-decide resume state; anything else
   means the world moved on).
4. Presented action's 4-tuple equals the bound tuple exactly.
5. `recheck()` on the presented action returns the same gated `ASK`
   (policy re-evaluated live — a denylist flip to BLOCK rejects).

## Burn rule

`burn()` happens **iff** the authorized execution returned `ok=True`,
inside the router, immediately after. Executor failure leaves the token live
(the authorization was valid; the attempt failed) — but every attempt goes
through checks 1–5 again, so drift still stops it. Deny/park/mismatch paths
never burn and never execute.

## What the token is NOT

- Not a bypass: the router still evaluates policy first; the token only
  authorizes the ASK→execute transition for its bound action.
- Not a scope: one task, one action, one success. No wildcards, no TTL
  extension, no approve-all (no such API exists and tests assert its absence).
- Not ambient authority: possession of a token_id for task A grants nothing
  toward task B (check 4 rejects cross-task presentation).
