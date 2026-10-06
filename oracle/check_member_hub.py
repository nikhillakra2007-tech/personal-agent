import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
profile_dir = ROOT / "var" / "sessions" / "oracle"

print("Navigating to Member Hub entry...")
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
        # Clean Member Hub login URL
        page.goto("https://academy.oracle.com/pls/f?p=63000:1", timeout=45000)
        time.sleep(8)
        print("URL:", page.url)
        print("Title:", page.title())
        shot_path = ROOT / "var" / "tmp" / "member_hub.png"
        page.screenshot(path=str(shot_path))
        print("Screenshot saved to", shot_path)
    finally:
        context.close()
