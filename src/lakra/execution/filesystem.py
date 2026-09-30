"""Slice-03: sandboxed filesystem executor.

All targets are confined under one root directory. Absolute paths and `..`
escapes are refused WITHOUT touching disk (defense in depth behind the
policy bounds check). Writes are verified by read-back; the read-back is
recorded as an observation — task-level verification stays UNVERIFIED until
the slice-05 verifier exists.
"""

from __future__ import annotations

import os
from pathlib import Path

from .registry import ExecuteResult
from ..control.policy import Action
from ..control.tasks import Task

KINDS = frozenset({"fs.read", "fs.write", "fs.list", "fs.delete"})


class FileSystemExecutor:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._root_real = os.path.realpath(self.root)

    def _confine(self, target: str) -> Path | None:
        """Return the in-root path, or None if the target escapes.

        Absolute targets are accepted iff they resolve inside the root
        (lets callers use policy-compatible absolute paths); anything
        resolving outside is refused without touching disk.
        """
        if os.path.isabs(target):
            full = os.path.realpath(target)
        else:
            full = os.path.realpath(os.path.join(self._root_real, target))
        if full != self._root_real and not full.startswith(
                self._root_real + os.sep):
            return None
        return Path(full)

    def execute(self, action: Action, task: Task) -> ExecuteResult:
        _ = task  # containment depends only on root, not task identity
        if action.kind not in KINDS:
            return ExecuteResult(ok=False,
                                 error=f"unsupported kind {action.kind}")
        path = self._confine(action.target)
        if path is None:
            return ExecuteResult(
                ok=False,
                error=f"refused: {action.target!r} escapes sandbox root")
        try:
            if action.kind == "fs.read":
                return ExecuteResult(ok=True, output=path.read_text(
                    encoding="utf-8"))
            if action.kind == "fs.list":
                names = sorted(p.name for p in path.iterdir())
                return ExecuteResult(ok=True, output="\n".join(names))
            if action.kind == "fs.write":
                # Content travels embedded: "<relpath>\n---\n<content>".
                # Tradeoff (documented): small bodies appear in audit targets
                # in this slice; a separate content channel is deferred until
                # a slice actually needs large/secret bodies.
                if "\n---\n" not in action.target:
                    return ExecuteResult(
                        ok=False,
                        error="fs.write target must be "
                              "'<relpath>\\n---\\n<content>'")
                relpath, content = action.target.split("\n---\n", 1)
                wpath = self._confine(relpath)
                if wpath is None:
                    return ExecuteResult(
                        ok=False,
                        error=f"refused: {relpath!r} escapes sandbox root")
                try:
                    wpath.parent.mkdir(parents=True, exist_ok=True)
                    wpath.write_text(content, encoding="utf-8")
                    if wpath.read_text(encoding="utf-8") != content:
                        return ExecuteResult(
                            ok=False, error="read-back mismatch after write")
                    return ExecuteResult(
                        ok=True,
                        output=f"wrote {len(content)} chars to {relpath} "
                               f"(read-back matched)")
                except OSError as exc:
                    return ExecuteResult(ok=False, error=f"os error: {exc}")
            if action.kind == "fs.delete":
                try:
                    path.unlink()
                    return ExecuteResult(
                        ok=True, output=f"deleted {action.target}")
                except FileNotFoundError:
                    return ExecuteResult(
                        ok=False, error=f"not found: {action.target!r}")
                except OSError as exc:
                    return ExecuteResult(ok=False, error=f"os error: {exc}")
        except OSError as exc:
            return ExecuteResult(ok=False, error=f"os error: {exc}")
        return ExecuteResult(ok=False, error="unreachable")
