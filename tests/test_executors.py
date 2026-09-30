"""Executor tests: sandbox containment, allowlist, timeouts."""

import sys

import pytest

from lakra.control.policy import Action
from lakra.control.tasks import Task
from lakra.execution.filesystem import FileSystemExecutor
from lakra.execution.terminal import ShellExecutor


def fs_action(kind, target):
    return Action(kind=kind, target=target, effect="bounded-mutation",
                  task_id="t")


@pytest.fixture
def fs(tmp_path):
    return FileSystemExecutor(tmp_path / "ws")


@pytest.fixture
def task():
    return Task.create("g")


def test_fs_write_read_roundtrip(fs, task):
    r = fs.execute(fs_action("fs.write", "sub/n.txt\n---\nhello"), task)
    assert r.ok and "read-back matched" in r.output
    r2 = fs.execute(fs_action("fs.read", "sub/n.txt"), task)
    assert r2.ok and r2.output == "hello"


def test_fs_dotdot_escape_refused_without_disk_touch(fs, task, tmp_path):
    r = fs.execute(fs_action("fs.write", "sub/../../evil.txt\n---\nx"), task)
    assert not r.ok and "escapes sandbox" in r.error
    assert not (tmp_path / "evil.txt").exists()


def test_fs_absolute_path_refused(fs, task):
    import os
    target = os.path.join("C:\\", "Windows", "x.txt")
    r = fs.execute(fs_action("fs.read", target), task)
    assert not r.ok


def test_fs_list_and_delete(fs, task):
    assert fs.execute(fs_action("fs.write", "a.txt\n---\n1"), task).ok
    assert fs.execute(fs_action("fs.list", "."), task).output == "a.txt"
    assert fs.execute(fs_action("fs.delete", "a.txt"), task).ok
    assert not fs.execute(fs_action("fs.read", "a.txt"), task).ok


def test_fs_unknown_kind(fs, task):
    assert not fs.execute(fs_action("fs.launch", "x"), task).ok


def test_shell_allows_python_version(tmp_path, task):
    sh = ShellExecutor(tmp_path / "ws")
    r = sh.execute(Action(kind="terminal.run", target=f"{sys.executable} --version",
                          effect="bounded-mutation", task_id="t"), task)
    assert r.ok and "Python" in r.output


def test_shell_refuses_pip_without_executing(tmp_path, task):
    sh = ShellExecutor(tmp_path / "ws")
    r = sh.execute(Action(kind="terminal.run", target="pip install requests",
                          effect="bounded-mutation", task_id="t"), task)
    assert not r.ok and "allowlist" in r.error


def test_shell_refuses_shell_builtins(tmp_path, task):
    sh = ShellExecutor(tmp_path / "ws")
    r = sh.execute(Action(kind="terminal.run", target="dir",
                          effect="read", task_id="t"), task)
    assert not r.ok  # no shell=True, so builtins don't exist here


def test_shell_timeout(tmp_path, task):
    sh = ShellExecutor(tmp_path / "ws", timeout_s=1)
    r = sh.execute(Action(
        kind="terminal.run",
        target=f"{sys.executable} -c \"import time; time.sleep(30)\"",
        effect="bounded-mutation", task_id="t"), task)
    assert not r.ok and "timed out" in r.error


def test_shell_nonzero_exit_is_failure(tmp_path, task):
    sh = ShellExecutor(tmp_path / "ws")
    r = sh.execute(Action(
        kind="terminal.run",
        target=f"{sys.executable} -c \"raise SystemExit(3)\"",
        effect="bounded-mutation", task_id="t"), task)
    assert not r.ok and "exit 3" in r.error
