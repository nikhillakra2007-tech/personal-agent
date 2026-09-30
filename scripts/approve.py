"""Slice-07 demo harness (in-process), slice-08 real cross-process CLI.

Opens the SHARED database (LAKRA_DB env or %LOCALAPPDATA%\\Lakra\\lakra.db),
sweeps expired approvals (fail-closed), lists pending items with task goals,
and prompts y/n per item (or --yes / --no for everything). Approving decides
through compare-and-set: if another process decided first, the decision is
refused instead of double-applied. Minting/consuming tokens stays Process
A's (Lakra's) job — this tool only decides.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\approve.py [--yes | --no]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.approvals import Approvals  # noqa: E402
from lakra.control.audit import AuditLog  # noqa: E402
from lakra.control.registry import TaskRegistry  # noqa: E402
from lakra.control.store import Database  # noqa: E402

YES = "--yes" in sys.argv
NO = "--no" in sys.argv

db = Database()
registry = TaskRegistry(store=db)
registry.load_all()
approvals = Approvals(registry, store=db)
approvals.load()
audit = AuditLog(ROOT / "var" / "audit-approve.jsonl")

expired = approvals.sweep()
for aid in expired:
    audit.log("APPROVAL_EXPIRED", "-", {"approval_id": aid})
if expired:
    print(f"swept {len(expired)} expired approval(s) (fail-closed)\n")

items = approvals.pending()
if not items:
    print("no pending approvals")
    raise SystemExit(0)

for item in items:
    print(f"task   : {item['goal']}\n"
          f"action : {item['kind']} {item['target']} [{item['effect']}]\n"
          f"expiry : {item['expires_at']}")
    if YES:
        answer = "y"
    elif NO:
        answer = "n"
    else:
        try:
            answer = input("approve once? [y/n] ").strip().lower()
        except EOFError:
            answer = "n"
    from lakra.control.approvals import ApprovalError  # noqa: E402
    try:
        approvals.decide(item["approval_id"], answer == "y")
    except ApprovalError as exc:
        print(f"refused (already settled elsewhere): {exc}")
        continue
    if answer == "y":
        audit.log("APPROVAL_GRANTED", item["task_id"],
                  {"approval_id": item["approval_id"], "by": "cli"})
        print("approved; Lakra mints/consumes the one-shot token on execute")
    else:
        audit.log("APPROVAL_DENIED", item["task_id"],
                  {"approval_id": item["approval_id"], "by": "cli"})
        print("denied; task cancelled, no retry")

print(f"\npending now: {len(approvals.pending())}")
