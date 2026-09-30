"""Slice-03: conservative shell executor.

NEVER uses shell=True. argv[0] (basename, extension stripped) must be in the
command allowlist; everything else is refused without executing. Subcommand
policing (e.g. `git push`) is the POLICY layer's job via caller-supplied
kind/effect — executors validate shape, policy judges meaning (see contract
review C8). cwd is pinned to the workspace root; wall-time bounded.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .registry import ExecuteResult
from ..control.policy import Action
from ..control.tasks import Task

# Conservative default: interpreters and read-only VCS/diagnostics.
# `pip` is EXCLUDED (network + arbitrary code install is not L2-quiet).
ALLOW_COMMANDS = frozenset({"python", "pytest", "git"})


class ShellExecutor:
    def __init__(self, cwd: str | Path,
                 allow_commands: frozenset[str] = ALLOW_COMMANDS,
                 timeout_s: int = 60) -> None:
        self.cwd = Path(cwd)
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.allow_commands = allow_commands
        self.timeout_s = timeout_s

    @staticmethod
    def _bin_of(argv0: str) -> str:
        return os.path.splitext(os.path.basename(argv0))[0].lower()

    def execute(self, action: Action, task: Task) -> ExecuteResult:
        _ = task
        if action.kind != "terminal.run":
            return ExecuteResult(ok=False,
                                 error=f"unsupported kind {action.kind}")
        try:
            # posix=False: POSIX-mode shlex eats Windows backslashes
            # (C:\... -> C:...). Strip one layer of quotes manually instead.
            raw = shlex.split(action.target, posix=False)
            argv = [t[1:-1] if len(t) >= 2 and t[0] == t[-1]
                    and t[0] in "\"'" else t for t in raw]
        except ValueError as exc:
            return ExecuteResult(ok=False, error=f"unparseable: {exc}")
        if not argv:
            return ExecuteResult(ok=False, error="empty command")
        if self._bin_of(argv[0]) not in self.allow_commands:
            return ExecuteResult(
                ok=False,
                error=f"refused: {argv[0]!r} not in command allowlist")
        try:
            proc = subprocess.run(
                argv, cwd=self.cwd, capture_output=True, text=True,
                timeout=self.timeout_s, shell=False)
        except FileNotFoundError:
            return ExecuteResult(ok=False,
                                 error=f"not found: {argv[0]!r}")
        except subprocess.TimeoutExpired:
            return ExecuteResult(
                ok=False,
                error=f"timed out after {self.timeout_s}s: {argv[0]!r}")
        except OSError as exc:
            return ExecuteResult(ok=False, error=f"os error: {exc}")
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            return ExecuteResult(
                ok=False, output=out,
                error=f"exit {proc.returncode}: {argv[0]!r}")
        return ExecuteResult(ok=True, output=out or "(no output)")
