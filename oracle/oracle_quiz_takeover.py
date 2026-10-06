"""Single-window Oracle quiz takeover: open the quiz page headed on the
oracle profile, WAIT for the human to sign in (polls for hard proof,
no Enter press, no premature close), then dump the signed-in quiz DOM
and exit cleanly so follow-up automation inherits a seconds-fresh
session. Never touches credentials (L4): it only reads rendered text.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.execution.browser.sessions import BrowserSessions  # noqa: E402

QUIZ_URL = ("https://academy.oracle.com/pls/f?p=63000:100:"
            "415024783861316:::100::&cs=3QLOnQ1w9MXwJrQ001l6Gmhz7ay23"
            "XidtuYaWHO48A59D7CNa9yv9wf3JfIit3D_N61aibI029nu1bnfqumf-bA")
import os
EMAIL = os.getenv("ORACLE_STUDENT_EMAIL", "")
WAIT_S = 600
POLL_S = 5


def signed_in(text: str) -> bool:
    return (EMAIL in text or "My Classes" in text) and \
        "Username or email" not in text


def main() -> int:
    s = BrowserSessions(ROOT / "var" / "sessions" / "oracle",
                        headless=False)
    s.launch()
    try:
        page = s._require_context().new_page()
        try:
            page.goto(QUIZ_URL, wait_until="load", timeout=25000)
        except Exception as exc:
            print(f"initial goto issue (retrying in-loop): {exc}")
        deadline = time.time() + WAIT_S
        while time.time() < deadline:
            time.sleep(POLL_S)
            try:
                text = page.locator("body").inner_text(timeout=4000) or ""
            except Exception as exc:
                print(f"waiting for browser... ({exc})")
                continue
            if signed_in(text):
                print("SIGNED IN - proof: email/classes marker live.")
                print(f"URL: {page.url}")
                try:
                    links = page.locator("a").all()
                    print(f"LINKS: {len(links)}")
                    shown = 0
                    for a in links:
                        try:
                            txt = a.inner_text().strip()
                        except Exception:
                            continue
                        if txt and any(k in txt for k in
                                       ("Section", "Quiz", "Assessment",
                                        "Take", "Class", "Sign Out")):
                            href = a.get_attribute("href") or ""
                            print(f"  LINK: {txt[:70]} -> {href[:110]}")
                            shown += 1
                            if shown >= 30:
                                break
                except Exception as exc:
                    print(f"link dump issue: {exc}")
                shot = ROOT / "var" / "tmp" / "quiz-signedin.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(shot))
                    print(f"screenshot: {shot}")
                except Exception as exc:
                    print(f"screenshot issue: {exc}")
                dom = ROOT / "var" / "tmp" / "quiz-signedin.html"
                try:
                    dom.write_text(page.content(), encoding="utf-8")
                    print(f"dom: {dom}")
                except Exception as exc:
                    print(f"dom dump issue: {exc}")
                return 0
            title = ""
            try:
                title = page.title()
            except Exception:
                pass
            print(f"... waiting for sign-in (title={title[:40]!r},"
                  f" text={len(text)} chars)")
        print("TIMEOUT - no signed-in markers in 10 min.")
        return 2
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
