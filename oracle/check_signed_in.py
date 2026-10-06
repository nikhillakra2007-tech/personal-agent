import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
profile_dir = ROOT / "var" / "sessions" / "oracle"
quiz_url = "https://academy.oracle.com/pls/f?p=63000:15:408416021490716::::P15_ID,P15_CC"

print("Checking page with DNS fix...")
with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--host-resolver-rules=MAP login-ext.identity.oraclecloud.com 131.186.9.131"
        ]
    )
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(quiz_url, timeout=45000)
        time.sleep(10)
        print("URL after 10s:", page.url)
        print("Title:", page.title())
        text = page.locator("body").inner_text()
        print("First 500 chars of body:")
        print(text[:500])
        shot_path = ROOT / "var" / "tmp" / "signed_in_check.png"
        page.screenshot(path=str(shot_path))
        print("Screenshot saved to", shot_path)
    finally:
        context.close()
