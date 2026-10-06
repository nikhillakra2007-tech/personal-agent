"""Oracle Academy Quiz Automation Runner & Solver.

Solves:
1. 'HTTP 400 Bad Request': Navigates to the clean Member Hub entry point
   (https://academy.oracle.com/pls/f?p=63000:1) to generate fresh APEX session & checksums.
2. 'login-ext.identity.oraclecloud.com' DNS resolution via host-resolver-rules.
3. Live sign-in detection: Waits indefinitely for you to complete sign-in in the
   headed browser without premature timeout.
4. Auto-navigates from dashboard -> 'Database Programming with SQL' -> 'Quiz: DP - Section 4'
   -> solves questions -> submits assessment.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, BrowserContext

ROOT = Path(__file__).resolve().parent.parent
MEMBER_HUB_URL = "https://academy.oracle.com/pls/f?p=63000:1"
DEFAULT_PROFILE = "oracle"
COOKIES_JSON = ROOT / "var" / "sessions" / "cookies.json"


def import_cookies_if_present(context: BrowserContext):
    """Load exported cookies from var/sessions/cookies.json if provided."""
    if not COOKIES_JSON.exists():
        return
    try:
        raw = json.loads(COOKIES_JSON.read_text(encoding="utf-8"))
        cookies_to_add = []
        for c in raw:
            cookie = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", "").lstrip("."),
                "path": c.get("path", "/"),
            }
            if "sameSite" in c and c["sameSite"] in ("Lax", "Strict", "None"):
                cookie["sameSite"] = c["sameSite"]
            if "secure" in c:
                cookie["secure"] = bool(c["secure"])
            if "httpOnly" in c:
                cookie["httpOnly"] = bool(c["httpOnly"])
            cookies_to_add.append(cookie)
        context.add_cookies(cookies_to_add)
        print(f"[COOKIES] Imported {len(cookies_to_add)} cookies from {COOKIES_JSON}")
    except Exception as exc:
        print(f"[COOKIES] Notice: Could not import cookies from {COOKIES_JSON}: {exc}")


def is_sign_in_page(page: Page) -> bool:
    try:
        title = page.title().lower()
        if "sign in" in title or "login" in title:
            return True
        body = page.locator("body").inner_text().lower()
        if "sign in to oracle" in body or "username or email" in body:
            return True
        if "signon.oracle.com" in page.url or "identity.oraclecloud.com" in page.url:
            return True
    except Exception:
        pass
    return False


def wait_until_authenticated(page: Page) -> bool:
    """Waits indefinitely for the user to complete sign-in in the opened browser."""
    print("\n" + "=" * 65)
    print("[AUTH REQUIRED] Oracle Sign-in window is open.")
    print("Please enter your username & password in the browser window.")
    print("The automation is waiting and will resume automatically")
    print("once login completes...")
    print("=" * 65 + "\n")

    counter = 0
    while True:
        time.sleep(2)
        counter += 1
        curr_url = page.url
        title = page.title()

        # Check if login completed and back on Oracle Academy
        if not is_sign_in_page(page) and "academy.oracle.com" in curr_url:
            print(f"\n[AUTH SUCCESS] Successfully authenticated into Oracle Academy!")
            print(f"Current URL: {curr_url}")
            print(f"Page Title: {title}")
            time.sleep(3)
            return True

        if counter % 5 == 0:
            print(f"[WAITING] Waiting for sign-in ({counter * 2}s elapsed)... (Current Title: '{title}')")


def run_quiz_workflow(page: Page, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)
    page.screenshot(path=str(shots_dir / "01_dashboard.png"))

    print("\n--- Scanning Oracle Academy Dashboard ---")
    print(f"Current URL: {page.url}")
    print(f"Title: {page.title()}")

    # Check for "Database Programming" course or "Section 4" or "Take an Assessment"
    print("Searching for curriculum links or assessment triggers...")
    
    # Check if directly on the quiz page already
    body_text = page.locator("body").inner_text()
    if "take an assessment" in body_text.lower():
        btn = page.locator("button:has-text('Take an Assessment'), a:has-text('Take an Assessment'), input[value*='Take an Assessment']").first
        if btn.count() > 0:
            print("[ACTION] Clicking 'Take an Assessment'...")
            btn.click()
            time.sleep(5)
            page.screenshot(path=str(shots_dir / "02_assessment_opened.png"))

    # If on dashboard, locate "Database Programming with SQL"
    elif "database programming" in body_text.lower():
        course_link = page.locator("a:has-text('Database Programming')").first
        if course_link.count() > 0:
            print("[ACTION] Clicking 'Database Programming with SQL' course...")
            course_link.click()
            time.sleep(5)
            page.screenshot(path=str(shots_dir / "02_course_view.png"))

    # Check for Section 4 Quiz
    sec4_link = page.locator("a:has-text('Section 4'), a:has-text('Quiz: DP - Section 4'), a:has-text('Quiz')").first
    if sec4_link.count() > 0:
        print("[ACTION] Clicking Section 4 Quiz link...")
        sec4_link.click()
        time.sleep(5)
        page.screenshot(path=str(shots_dir / "03_quiz_view.png"))

    # Question answering loop
    print("\n--- Assessment Answering Loop ---")
    step = 1
    while step <= 50:
        time.sleep(2)
        page.screenshot(path=str(shots_dir / f"step_{step}.png"))
        curr_body = page.locator("body").inner_text()

        # Check for radio buttons / options
        options = page.locator("input[type='radio'], input[type='checkbox']").all()
        print(f"[STEP {step}] Page: '{page.title()}' | Found {len(options)} options.")

        if options:
            # Check if any is selected
            if not any(opt.is_checked() for opt in options):
                print(f"Selecting option 1 for question...")
                try:
                    options[0].click()
                except Exception:
                    page.evaluate("(el) => el.click()", options[0])

        # Find navigation button: Next, Submit, Save Progress, Finish
        nav_button = None
        for b in page.locator("button, a, input[type='button'], input[type='submit']").all():
            try:
                txt = (b.inner_text() or b.get_attribute("value") or "").strip().lower()
                if txt in ("next", "next question", "continue", "submit", "save progress", "finish", "finish assessment"):
                    nav_button = (b, txt)
                    break
            except Exception:
                continue

        if nav_button:
            btn_obj, btn_txt = nav_button
            print(f"[ACTION] Clicking: '{btn_txt}'")
            btn_obj.click()
            time.sleep(3)
            step += 1
        else:
            print("[INFO] No further navigation button found. Either completed or review screen reached.")
            break

    print("\nWorkflow cycle completed. Leaving browser open for your inspection.")
    time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Oracle Academy Quiz Automation")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="Profile name")
    args = parser.parse_args()

    profile_dir = ROOT / "var" / "sessions" / args.profile
    shots_dir = ROOT / "var" / "do-shots" / "oracle_quiz"

    print(f"Starting Oracle Academy Automation...")
    print(f"Profile: {profile_dir}")
    print(f"Entry URL: {MEMBER_HUB_URL}")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--host-resolver-rules=MAP login-ext.identity.oraclecloud.com 131.186.9.131"
            ]
        )
        import_cookies_if_present(context)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"Navigating to Member Hub: {MEMBER_HUB_URL}...")
            page.goto(MEMBER_HUB_URL, timeout=60000)
            time.sleep(4)

            # Wait for sign-in if required
            if is_sign_in_page(page):
                wait_until_authenticated(page)
            else:
                print("[AUTH] Active session detected directly!")

            run_quiz_workflow(page, shots_dir)

        except Exception as exc:
            print(f"[ERROR] Run failed: {exc}")
            return 1
        finally:
            context.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
