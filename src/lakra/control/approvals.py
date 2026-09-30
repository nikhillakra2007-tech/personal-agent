"""Slice-02 mechanics, slice-07 tokens, slice-08 persistence.

request() parks a task in WAITING_APPROVAL with a bound Approval record.
decide() is the single choke point with compare-and-set semantics (exactly
one decider wins across processes). Approved moves to the approved store;
execution additionally needs a minted token consumed through the router.
One approval binds one task + one action; no wildcard, no approve-all.
See docs/contracts/approval-token.md for the canonical token binding.

With store=None everything is in-memory (slices 01-07 behavior, unchanged).
With a Database, every mutation is write-through and every read that gates
a decision (pending/decide/redeem/burn) goes to the database, so two
processes share one truth.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from uuid import uuid4

from .policy import Action, Approval, evaluate
from .registry import TaskRegistry
from .tasks import Status


class ApprovalError(RuntimeError):
    """Unknown approval id, or decision on a settled approval."""


@dataclass
class ApprovalToken:
    token_id: str
    approval_id: str
    task_id: str
    kind: str
    target: str
    effect: str
    expires_at: str
    used: bool = False


def _action_to_json(action: Action) -> str:
    return json.dumps(dataclasses.asdict(action))


def _action_from_json(raw: str) -> Action:
    return Action(**json.loads(raw))


def _approval_to_row(apv: Approval) -> tuple:
    return (apv.approval_id, apv.action.task_id, _action_to_json(apv.action),
            apv.requested_at, apv.decided, apv.decided_at, apv.expires_at)


def _row_to_approval(row: tuple) -> Approval:
    (aid, _, action_json, requested_at, decided, decided_at,
     expires_at) = row
    return Approval(approval_id=aid, action=_action_from_json(action_json),
                    requested_at=requested_at, decided=decided,
                    decided_at=decided_at, expires_at=expires_at)


class Approvals:
    def __init__(self, registry: TaskRegistry, store=None) -> None:
        self.registry = registry
        self.store = store  # Database or None
        self._pending: dict[str, Approval] = {}
        self._approved: dict[str, Approval] = {}
        self._tokens: dict[str, ApprovalToken] = {}

    # -- persistence helpers -------------------------------------------------
    def _db_insert_approval(self, apv: Approval) -> None:
        self.store.execute(
            "INSERT INTO approvals(approval_id, task_id, action_json,"
            " requested_at, decided, decided_at, expires_at)"
            " VALUES (?,?,?,?,?,?,?)", _approval_to_row(apv))
        self.store.commit()

    def _db_load_approval(self, approval_id: str) -> Approval | None:
        cur = self.store.execute(
            "SELECT approval_id, task_id, action_json, requested_at, decided,"
            " decided_at, expires_at FROM approvals WHERE approval_id=?",
            (approval_id,))
        row = cur.fetchone()
        return _row_to_approval(row) if row else None

    def _db_cas_decide(self, approval_id: str, decided: str,
                       decided_at: str) -> bool:
        cur = self.store.execute(
            "UPDATE approvals SET decided=?, decided_at=? WHERE"
            " approval_id=? AND decided IS NULL",
            (decided, decided_at, approval_id))
        self.store.commit()
        return cur.rowcount == 1

    # -- lifecycle -------------------------------------------------------------
    def request(self, task_id: str, action: Action,
                ttl_s: int = 900) -> Approval:
        task = self.registry.get(task_id)
        if action.task_id != task_id:
            raise ApprovalError("action.task_id does not match task")
        approval = Approval.request(action, ttl_s=ttl_s)
        self._pending[approval.approval_id] = approval
        if self.store is not None:
            self._db_insert_approval(approval)
        self.registry.set_status(task_id, Status.WAITING_APPROVAL)
        task.owner  # ownership retained while parked (no silent handoff)
        return approval

    def pending(self) -> list[dict]:
        if self.store is not None:
            cur = self.store.execute(
                "SELECT approval_id, task_id, action_json, requested_at, decided,"
                " decided_at, expires_at FROM approvals"
                " WHERE decided IS NULL")
            approvals = [_row_to_approval(r) for r in cur.fetchall()]
        else:
            approvals = list(self._pending.values())
        out = []
        for apv in approvals:
            try:
                goal = self.registry.get(apv.action.task_id).goal
            except Exception:
                goal = "?"
            out.append({
                "approval_id": apv.approval_id,
                "task_id": apv.action.task_id,
                "goal": goal,
                "kind": apv.action.kind,
                "target": apv.action.target,
                "effect": apv.action.effect,
                "requested_at": apv.requested_at,
                "expires_at": apv.expires_at,
            })
        return out

    def decide(self, approval_id: str, approved: bool) -> Approval:
        from datetime import datetime, timezone
        decided = "approved" if approved else "denied"
        decided_at = datetime.now(timezone.utc).isoformat()
        if self.store is not None:
            if not self._db_cas_decide(approval_id, decided, decided_at):
                raise ApprovalError(
                    f"approval {approval_id} unknown or already settled")
            approval = self._db_load_approval(approval_id)
            assert approval is not None
        else:
            approval = self._pending.get(approval_id)
            if approval is None:
                raise ApprovalError(f"unknown approval {approval_id}")
            if approval.decided is not None:
                raise ApprovalError(
                    f"approval {approval_id} already settled")
            approval.decided = decided
            approval.decided_at = decided_at
            del self._pending[approval_id]
        if approved:
            # Resume; execution additionally needs a minted token consumed
            # through the router (docs/contracts/approval-token.md).
            self.registry.set_status(approval.action.task_id, Status.RUNNING)
            self._approved[approval_id] = approval
            self._pending.pop(approval_id, None)
        else:
            self.registry.set_status(approval.action.task_id,
                                     Status.CANCELLED)
            try:
                owner = self.registry.get(approval.action.task_id).owner or ""
                self.registry.release(approval.action.task_id, owner)
            except Exception:
                pass
            self._pending.pop(approval_id, None)
        return approval

    def adopt(self, approval_id: str) -> Approval:
        """Adopt an already-settled approval without re-deciding it.

        Slice-37 cross-process boundary: when another process
        (approve.py) settles a parked approval while this process was
        deciding, shared truth wins — adopt it instead of colliding
        with the CAS in decide(). Read-only: no row is written, no
        status moved, no token minted. Raises ApprovalError while
        still pending or when unknown (caller falls back to the normal
        decide() path). The returned Approval carries decided
        ("approved"/"denied"); approved ones mint through the unchanged
        token path, denied ones map to the identical clean deny
        outcome. Bound-task status is NOT synced here — pair with
        TaskRegistry.refresh() so redeem()'s RUNNING re-check reads
        shared truth, not a stale pre-park cache.
        """
        if self.store is not None:
            approval = self._db_load_approval(approval_id)
            if approval is None:
                raise ApprovalError(f"unknown approval {approval_id}")
            if approval.decided is None:
                raise ApprovalError(
                    f"approval {approval_id} not settled")
            if approval.decided == "approved":
                self._approved[approval_id] = approval
            self._pending.pop(approval_id, None)
            return approval
        approval = self._approved.get(approval_id)
        if approval is not None:
            return approval
        if approval_id in self._pending:
            raise ApprovalError(f"approval {approval_id} not settled")
        raise ApprovalError(f"unknown approval {approval_id}")

    def sweep(self) -> list[str]:
        expired = [a["approval_id"] for a in self.pending()
                   if self._is_expired(a["expires_at"])]
        out = []
        for aid in expired:
            try:
                self.decide(aid, False)
                out.append(aid)
            except ApprovalError:
                pass
        return out

    @staticmethod
    def _is_expired(expires_at: str | None) -> bool:
        if not expires_at:
            return False
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat() >= expires_at

    def load(self) -> None:
        """Boot recovery: load approvals + tokens into memory."""
        if self.store is None:
            return
        cur = self.store.execute(
            "SELECT approval_id, task_id, action_json, requested_at, decided,"
            " decided_at, expires_at FROM approvals")
        for row in cur.fetchall():
            apv = _row_to_approval(row)
            if apv.decided == "approved":
                self._approved[apv.approval_id] = apv
            elif apv.decided is None:
                self._pending[apv.approval_id] = apv
        cur = self.store.execute(
            "SELECT token_id, approval_id, task_id, kind, target, effect,"
            " expires_at, used FROM tokens")
        for (tid, aid, task_id, kind, target, effect, exp, used) in \
                cur.fetchall():
            self._tokens[tid] = ApprovalToken(
                token_id=tid, approval_id=aid, task_id=task_id, kind=kind,
                target=target, effect=effect, expires_at=exp,
                used=bool(used))

    def recheck(self, approval: Approval, **kw) -> str:
        """Re-evaluate the identical approved action. Execution may proceed
        only if the verdict is unchanged (still the same gated ASK): a flip
        to BLOCK means the world changed adversely (e.g. fresh denylist);
        anything else means this is no longer the approved action."""
        if approval.decided != "approved":
            raise ApprovalError("cannot recheck an unapproved approval")
        task = self.registry.get(approval.action.task_id)
        return evaluate(approval.action, task, **kw)

    @staticmethod
    def must_be_gated(verdict: str) -> None:
        if verdict != "ASK":
            raise ApprovalError(
                f"post-approval recheck returned {verdict}, not ASK; "
                "the approved action is no longer current — "
                "execution forbidden")

    def mint_token(self, approval_id: str, ttl_s: int = 300) -> ApprovalToken:
        """Mint a one-shot token for a decided-approved approval. Idempotent:
        re-mint returns the existing unburned token (UNIQUE approval_id)."""
        from datetime import datetime, timedelta, timezone
        approval = self._approved.get(approval_id)
        if self.store is not None:
            approval = self._db_load_approval(approval_id)
        if approval is None or approval.decided != "approved":
            raise ApprovalError(
                f"no approved approval {approval_id} to mint from")
        if self.store is not None:
            cur = self.store.execute(
                "SELECT token_id, approval_id, task_id, kind, target,"
                " effect, expires_at, used FROM tokens WHERE approval_id=?",
                (approval_id,))
            row = cur.fetchone()
            if row is not None:
                return ApprovalToken(
                    token_id=row[0], approval_id=row[1], task_id=row[2],
                    kind=row[3], target=row[4], effect=row[5],
                    expires_at=row[6], used=bool(row[7]))
        else:
            for tok in self._tokens.values():
                if tok.approval_id == approval_id and not tok.used:
                    return tok
        token = ApprovalToken(
            token_id=uuid4().hex,
            approval_id=approval_id,
            task_id=approval.action.task_id,
            kind=approval.action.kind,
            target=approval.action.target,
            effect=approval.action.effect,
            expires_at=(datetime.now(timezone.utc)
                        + timedelta(seconds=ttl_s)).isoformat(),
        )
        if self.store is not None:
            self.store.execute(
                "INSERT INTO tokens(token_id, approval_id, task_id, kind,"
                " target, effect, expires_at, used)"
                " VALUES (?,?,?,?,?,?,?,0)",
                (token.token_id, token.approval_id, token.task_id,
                 token.kind, token.target, token.effect, token.expires_at))
            self.store.commit()
        self._tokens[token.token_id] = token
        return token

    def redeem(self, token_id: str, action: Action, **kw) -> ApprovalToken:
        """Validate presentation. No state change except failed-open nothing:
        every check must hold or ApprovalError (token unburned). Extra
        keywords pass through to the policy recheck (e.g. denylists)."""
        from datetime import datetime, timezone
        token = self._tokens.get(token_id)
        if self.store is not None:
            cur = self.store.execute(
                "SELECT token_id, approval_id, task_id, kind, target,"
                " effect, expires_at, used FROM tokens WHERE token_id=?",
                (token_id,))
            row = cur.fetchone()
            token = (ApprovalToken(
                token_id=row[0], approval_id=row[1], task_id=row[2],
                kind=row[3], target=row[4], effect=row[5],
                expires_at=row[6], used=bool(row[7])) if row else None)
        if token is None:
            raise ApprovalError("unknown token")
        if token.used:
            raise ApprovalError("token already consumed")
        now = datetime.now(timezone.utc).isoformat()
        if now >= token.expires_at:
            raise ApprovalError("token expired")
        approval = self._approved.get(token.approval_id)
        if self.store is not None:
            approval = self._db_load_approval(token.approval_id)
        if approval is None or approval.decided != "approved":
            raise ApprovalError("source approval no longer approved")
        if self.registry.get(token.task_id).status != Status.RUNNING:
            raise ApprovalError("task left the approved resume state")
        bound = (token.task_id, token.kind, token.target, token.effect)
        presented = (action.task_id, action.kind, action.target,
                     action.effect)
        if presented != bound:
            raise ApprovalError("presented action differs from bound action")
        self.must_be_gated(self.recheck(approval, **kw))
        return token

    def burn(self, token_id: str) -> None:
        if self.store is not None:
            cur = self.store.execute(
                "UPDATE tokens SET used=1 WHERE token_id=? AND used=0",
                (token_id,))
            self.store.commit()
            if cur.rowcount != 1:
                raise ApprovalError("cannot burn unknown/consumed token")
            tok = self._tokens.get(token_id)
            if tok is not None:
                tok.used = True
            return
        token = self._tokens.get(token_id)
        if token is None or token.used:
            raise ApprovalError("cannot burn unknown/consumed token")
        token.used = True
