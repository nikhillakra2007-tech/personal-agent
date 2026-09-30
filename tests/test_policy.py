"""T2 tests: policy truth table — every L0-L4 example maps to its verdict."""

import pytest

from lakra.control.policy import ALLOW, ASK, BLOCK, Action, evaluate
from lakra.control.tasks import Task


def make_task(**kw):
    kw.setdefault("allowed_tools", ["browser", "fs", "terminal"])
    kw.setdefault("allowed_domains", ["docs.example.com"])
    kw.setdefault("allowed_paths", ["C:/LakraWorkspace"])
    return Task.create("test goal", **kw)


def act(kind, target, effect):
    return Action(kind=kind, target=target, effect=effect, task_id="t")


# (kind, target, effect, expected) — one row per contract example.
TRUTH_TABLE = [
    # L0 observe -> ALLOW
    ("browser.snapshot", "https://docs.example.com/a", "read", ALLOW),
    ("browser.screenshot", "https://docs.example.com/a", "read", ALLOW),
    ("fs.read", "C:/LakraWorkspace/notes.md", "read", ALLOW),
    # L1 safe/reversible -> ALLOW
    ("browser.navigate", "https://docs.example.com/a", "reversible", ALLOW),
    ("browser.click", "https://docs.example.com/a", "reversible", ALLOW),
    ("browser.type", "https://docs.example.com/search", "reversible", ALLOW),
    ("browser.scroll", "https://docs.example.com/a", "reversible", ALLOW),
    ("browser.tabs.switch", "https://docs.example.com/a", "reversible", ALLOW),
    # L2 in-bounds -> ALLOW
    ("fs.write", "C:/LakraWorkspace/out.md", "bounded-mutation", ALLOW),
    ("terminal.run", "pytest tests", "bounded-mutation", ALLOW),
    # L2 out-of-bounds -> ASK (never silent ALLOW)
    ("fs.write", "C:/Windows/evil.txt", "bounded-mutation", ASK),
    ("browser.navigate", "https://evil.example/x", "reversible", ASK),
    ("mcp.call", "calendar.list", "read", ASK),  # tool not allowlisted
    # L3 list -> ASK even when in-bounds and permission allows
    ("browser.submit", "https://docs.example.com/form", "consequential", ASK),
    ("mail.send", "tutor@example.com", "consequential", ASK),
    ("publish", "blog-post", "consequential", ASK),
    # consequential effect with level < 3 -> ASK
    ("custom.deploy", "staging", "consequential", ASK),
    # L4 -> BLOCK
    ("auth.bypass", "login wall", "blocked", BLOCK),
    ("captcha.defeat", "https://docs.example.com/c", "read", BLOCK),
    ("security.bypass", "paywall", "reversible", BLOCK),
    ("browser.navigate", "https://docs.example.com/a", "blocked", BLOCK),
]


@pytest.mark.parametrize("kind,target,effect,expected", TRUTH_TABLE)
def test_truth_table(kind, target, effect, expected):
    assert evaluate(act(kind, target, effect), make_task()) == expected


def test_first_match_l4_beats_l1_labelling():
    # A caller labelling a CAPTCHA defeat as "read" is still BLOCKED.
    a = act("captcha.defeat", "https://docs.example.com/c", "read")
    assert evaluate(a, make_task()) == BLOCK


def test_deny_targets_block():
    a = act("browser.navigate", "https://docs.example.com/a", "reversible")
    assert evaluate(a, make_task(),
                    deny_targets=frozenset({"docs.example.com"})) == BLOCK


def test_deterministic_same_input_same_verdict():
    t, a = make_task(), act("browser.click",
                            "https://docs.example.com/a", "reversible")
    assert evaluate(a, t) == evaluate(a, t) == ALLOW
