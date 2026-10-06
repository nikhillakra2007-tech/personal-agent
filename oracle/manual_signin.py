"""Manual sign-in bootstrap for a Lakra session profile.

Lakra runs headless and refuses credential entry by design (policy
L4: no credential capture, no login road). Authenticated automation
therefore starts HERE: this script opens a HEADED, human-controlled
browser on the exact same persistent profile directory that
``scripts/lakra_do.py --session-profile NAME`` later reuses, so you
sign in yourself once and every subsequent Lakra run inherits the
session cookies/state.

Contract (mirrors V2-06 profile rules):
  - The profile is a NAME, never a path: it validates through
    lakra.control.profiles.validate_profile_name and resolves under
    the Lakra sessions root (default var/sessions, override with
    --sessions-dir, same flag semantics as lakra_do.py).
  - No credential ever passes through this script or any Lakra
    code: you type into the real Oracle page in a real browser.
  - The context closes only when you press Enter in this terminal,
    giving Chrome a clean shutdown so session state is flushed to
    the profile directory.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\manual_signin.py --url https://signon.oracle.com/signin
    .\\.venv\\Scripts\\python.exe scripts\\manual_signin.py --profile oracle --url https://education.oracle.com/

Exit codes:
    0 : browser opened and closed cleanly (profile saved)
    1 : usage error / invalid profile / launch failure
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.profiles import (  # noqa: E402
    ProfileRefused,
    resolve_profile,
    validate_profile_name,
)

DEFAULT_URL = "https://signon.oracle.com/signin"
DEFAULT_PROFILE = "oracle"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="manual_signin.py",
        description="Open a headed browser on a Lakra session profile "
                    "so you can sign in manually; Lakra reuses the "
                    "saved session afterwards.")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"page to open (default: {DEFAULT_URL})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="session profile NAME (default: "
                             f"{DEFAULT_PROFILE})")
    parser.add_argument("--sessions-dir", default=None,
                        help="sessions root (default: var/sessions)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        name = validate_profile_name(args.profile)
    except ProfileRefused as exc:
        print(f"refused: {exc}")
        return 1
    root = Path(args.sessions_dir) if args.sessions_dir \
        else (ROOT / "var" / "sessions")
    try:
        target = resolve_profile(root, name)
    except ProfileRefused as exc:
        print(f"refused: {exc}")
        return 1
    if not args.url.startswith(("http://", "https://")):
        print("refused: --url must be http(s)")
        return 1

    from playwright.sync_api import sync_playwright

    print(f"profile: {name}")
    print(f"session dir: {target}")
    print(f"opening: {args.url}")
    print("Sign in IN THE BROWSER WINDOW (this script never sees your "
          "credentials).")
    print("Press Enter here when done so the session can be saved...")

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(target), headless=False)
            page = context.pages[0] if context.pages \
                else context.new_page()
            try:
                page.goto(args.url, timeout=45000,
                          wait_until="domcontentloaded")
            except Exception as exc:
                print(f"warning: initial navigation failed ({exc}); "
                      "the window stays open - retry inside it if needed")
            try:
                input()
            except EOFError:
                pass
            final_url = page.url
            try:
                body = (page.locator("body").inner_text(
                    timeout=5000) or "")
            except Exception:
                body = ""
            if ("Username or email" in body
                    or "Create Account" in body):
                print("VERDICT: STILL LOGGED OUT - the page shows the"
                      " sign-in form. Sign in inside THIS Chromium window"
                      " (not your usual browser) and run again.")
            elif ("Sign Out" in body or "My Classes" in body
                    or "@" in body):
                print("VERDICT: SIGNED IN - session markers found,"
                      " profile saved.")
            else:
                print("VERDICT: UNKNOWN - body text inconclusive"
                      f" ({len(body)} chars). If you see your classes in"
                      " the window, the session saved; else run again.")
            context.close()  # clean shutdown -> state flushed to disk
    except KeyboardInterrupt:
        print("\ninterrupted; nothing saved beyond what Chrome already "
              "wrote")
        return 1
    except Exception as exc:
        print(f"error: cannot launch browser ({exc})")
        return 1

    print(f"saved session profile: {name}")
    print(f"last URL: {final_url}")
    print("Next: run the chain, e.g.")
    print("  python scripts/lakra_do.py --chain-file"
          " chains/oracle-academy.json --session-profile"
          f" {name} --allow-domain signon.oracle.com"
          " --allow-domain www.oracle.com --allow-domain"
          " academy.oracle.com --headed --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
