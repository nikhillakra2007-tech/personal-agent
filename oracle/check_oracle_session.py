import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
profile_dir = ROOT / "var" / "sessions" / "oracle"

quiz_url = "https://academy.oracle.com/pls/f?p=63000:15:408416021490716::::P15_ID,P15_CC"

print(f"Checking session in {profile_dir}...")
with sync_playwright() as pw:
    headless_mode = "--headed" not in sys.argv
    print(f"Launching chromium (headless={headless_mode})...")
    context = pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless_mode
    )
    page = context.pages[0] if context.pages else context.new_page()
    try:
        print(f"Navigating to {quiz_url}...")
        resp = page.goto(quiz_url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        print("Final Page URL:", page.url)
        print("Page Title:", page.title())
        body_text = page.locator("body").inner_text()
        print("\n--- BODY TEXT (first 1500 chars) ---")
        print(body_text[:1500])
        print("\n--- END SNIPPET ---")
        
        # Save screenshot
        shot_path = ROOT / "var" / "tmp" / "oracle_page.png"
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shot_path))
        print("Screenshot saved to", shot_path)
    except Exception as e:
        print("Error during navigation:", e)
    finally:
        context.close()
