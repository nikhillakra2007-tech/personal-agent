import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
profile_dir = ROOT / "var" / "sessions" / "oracle"

quiz_url = "https://academy.oracle.com/pls/f?p=63000:15:408416021490716::::P15_ID,P15_CC"

print(f"Checking session in {profile_dir} with DNS mapping...")
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

    def on_framenavigated(frame):
        if frame == page.main_frame:
            print(f"[NAVIGATED] -> {frame.url}")

    page.on("framenavigated", on_framenavigated)

    try:
        print(f"Navigating to {quiz_url}...")
        page.goto(quiz_url, timeout=45000)
        # Wait up to 20 seconds observing where it settles
        for i in range(20):
            time.sleep(1)
            if "academy.oracle.com" in page.url and "signon" not in page.url and page.title() and page.title() != "login-ext.identity.oraclecloud.com":
                print(f"Settled on {page.url} with title: '{page.title()}'")
                break
        
        print("\n--- FINAL STATE ---")
        print("Final Page URL:", page.url)
        print("Page Title:", page.title())
        body_text = page.locator("body").inner_text()
        print("\n--- BODY TEXT (first 1000 chars) ---")
        print(body_text[:1000])
        print("\n--- END SNIPPET ---")
        
        # Save screenshot
        shot_path = ROOT / "var" / "tmp" / "oracle_dns_fixed.png"
        page.screenshot(path=str(shot_path))
        print("Saved screenshot to", shot_path)

        buttons = page.locator("button, a, input").all()
        print(f"Total interactive elements: {len(buttons)}")
        for b in buttons[:20]:
            try:
                t = b.inner_text().strip()
                if t:
                    print("  Element:", t[:50])
            except:
                pass

    except Exception as e:
        print("Error during test:", e)
    finally:
        context.close()
