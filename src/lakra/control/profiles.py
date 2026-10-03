"""V2-06: opt-in persistent browser profiles (names, not paths).

A session profile is a NAME resolving to one directory under a
Lakra-controlled sessions root (default var/sessions). Playwright's
persistent context already stores cookies/storage opaquely inside
that directory — this module adds only identity and confinement:

- validate_profile_name(): strict charset ([a-z0-9_-], leading
  alnum, <=64 chars, lowercased for determinism on
  case-insensitive filesystems); anything else — empty, traversal,
  absolute paths, separators, spaces, weird characters — refuses.
  Names are never filesystem paths, so the flag can never address
  an arbitrary user directory.
- resolve_profile(): root/name as a Path, refused when a
  pre-existing entry resolves outside the root (symlink escape).
  Creates nothing; BrowserSessions owns creation.
- clear_profile(): validated recursive delete of one named profile;
  unknown names refuse (never invented, never silent).

No credentials, tokens, or secrets ever pass through here: profile
directories hold only opaque browser-managed state. Lakra never
reads, prints, or transmits their contents.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

MAX_PROFILE_NAME = 64

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ProfileRefused(ValueError):
    """Invalid profile name or unsafe resolution. Fail-closed."""


def validate_profile_name(name) -> str:
    """Normalize a profile name or refuse it. Returns the lowercase
    canonical name (deterministic identity: "Alpha" and "alpha" are
    one profile, never two colliding directories)."""
    if not isinstance(name, str):
        raise ProfileRefused("profile name must be a string")
    lowered = name.lower()
    if not lowered or len(name) > MAX_PROFILE_NAME:
        raise ProfileRefused(
            f"profile name must be 1..{MAX_PROFILE_NAME} characters")
    if _NAME_RE.fullmatch(lowered) is None:
        raise ProfileRefused(
            f"invalid profile name {name!r}: use letters, digits,"
            " '-' or '_' only (no paths, no traversal, no spaces)")
    return lowered


def _root_real(root) -> str:
    try:
        return os.path.realpath(root)
    except Exception as exc:
        raise ProfileRefused(f"unusable sessions root ({exc})") from None


def resolve_profile(root, name) -> Path:
    """Validated name -> confined directory path (created by nobody
    here). Refuses symlink escape: a pre-existing entry resolving
    outside the root is never returned."""
    canonical = validate_profile_name(name)
    real = _root_real(root)
    literal = os.path.join(real, canonical)
    # Post-validation names hold no separators, so realpath differs
    # from the literal path only when a symlink redirects the
    # resolution — which refuses instead of escaping the root.
    if os.path.realpath(literal) != literal:
        raise ProfileRefused(
            f"profile {canonical!r} escapes the sessions root")
    return Path(literal)


def clear_profile(root, name) -> Path:
    """Delete one named profile directory. Refuses unknown names and
    anything not resolving to a real directory under the root."""
    target = resolve_profile(root, name)
    if not target.is_dir():
        raise ProfileRefused(f"unknown session profile {name!r}")
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise ProfileRefused(
            f"cannot clear profile {name!r} ({exc})") from None
    return target
