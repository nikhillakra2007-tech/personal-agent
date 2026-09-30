"""Computer-lock tests: exclusivity, safe release, race safety."""

import threading

import pytest

from lakra.control.locks import ComputerLock, LockBusyError, LockError


def test_first_owner_succeeds():
    lock = ComputerLock()
    assert lock.acquire("task-a") == "task-a"
    assert lock.holder == "task-a"


def test_second_owner_fails():
    lock = ComputerLock()
    lock.acquire("task-a")
    with pytest.raises(LockBusyError):
        lock.acquire("task-b")
    assert lock.holder == "task-a"  # not stolen


def test_owner_can_release_and_reacquire():
    lock = ComputerLock()
    lock.acquire("task-a")
    lock.release("task-a")
    assert lock.holder is None
    lock.acquire("task-b")
    assert lock.holder == "task-b"


def test_non_owner_cannot_release():
    lock = ComputerLock()
    lock.acquire("task-a")
    with pytest.raises(LockError):
        lock.release("task-b")
    assert lock.holder == "task-a"


def test_release_unheld_fails():
    with pytest.raises(LockError):
        ComputerLock().release("nobody")


def test_concurrent_acquire_single_winner():
    lock = ComputerLock()
    winners, denied = [], []

    def grab(owner):
        try:
            lock.acquire(owner)
            winners.append(owner)
        except LockBusyError:
            denied.append(owner)

    threads = [threading.Thread(target=grab, args=(f"t-{i}",))
               for i in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(winners) == 1 and len(denied) == 15
