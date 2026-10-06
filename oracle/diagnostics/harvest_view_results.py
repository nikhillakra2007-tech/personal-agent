import os
import re
import json
import time
from playwright.sync_api import sync_playwright

USER_DATA_DIR = os.path.abspath("var/sessions/oracle")
ANSWERS_CACHE_FILE = os.path.abspath("var/oracle_answers_cache.json")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            viewport={"width": 1280, "height": 850},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--host-resolver-rules="
                "MAP academy.oracle.com 23.217.111.104,"
                "MAP signon.oracle.com 23.217.111.57,"
                "MAP login-ext.identity.oraclecloud.com 131.186.9.131"
            ]
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        print("[HARVESTER] Navigating to Member Hub...")
        page.goto("https://academy.oracle.com/pls/f?p=63000:1", timeout=60000)
        time.sleep(3)

        # Enter class
        print("[HARVESTER] Entering My Classes / Course Outline...")
        page.locator("a:has-text('My Classes'), a[href*='63000:14:']").first.click(force=True)
        time.sleep(4)

        p14 = page.locator("a[href*='63000:14:']").first
        if p14.count() > 0:
            p14.click(force=True)
            time.sleep(4)

        # Section 4
        s4 = page.locator("a:has-text('Section 4')").first
        if s4.count() > 0:
            s4.click(force=True)
            time.sleep(4)

        # Look for Quiz link on right sidebar
        quiz_link = page.locator("a, li").filter(has_text=re.compile(r"Quiz\s*:\s*DP\s*-\s*Section\s*4", re.I)).first
        if quiz_link.count() > 0:
            quiz_link.click(force=True)
            time.sleep(4)

        # Look for 'View Results' button on Page 15 (table of attempts) or Page 192
        print("[HARVESTER] Looking for 'View Results' button...")
        view_res = page.locator("button:has-text('View Results'), a:has-text('View Results'), .t-Button:has-text('View Results')").first
        if view_res.count() > 0:
            print("[HARVESTER] Found 'View Results' button! Clicking...")
            view_res.click(force=True)
            time.sleep(5)
            print("Current URL on results page:", page.url)

            # Dump review page content
            review_data = page.evaluate("""() => {
                const results = [];
                // Look for all questions in review
                const all = Array.from(document.querySelectorAll('*'));
                // Find all question headers (Question 1, Question 2, etc.)
                const qHeaders = all.filter(el => el.children.length === 0 && /^Question\s+\d+/i.test((el.innerText || '').trim()));
                
                return {
                    title: document.title,
                    bodyText: document.body.innerText,
                    html: document.body.innerHTML.substring(0, 5000)
                };
            }""")

            with open("var/review_dump.txt", "w", encoding="utf-8") as f:
                f.write(review_data["bodyText"])
            print("Successfully dumped review text to var/review_dump.txt!")
        else:
            print("[HARVESTER] 'View Results' button not found on this screen.")

        time.sleep(3)
        browser.close()

if __name__ == "__main__":
    main()
