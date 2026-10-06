import os
import time
from playwright.sync_api import sync_playwright

USER_DATA_DIR = os.path.abspath("var/sessions/oracle")

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
        
        # Navigate directly to Member Hub
        page.goto("https://academy.oracle.com/pls/f?p=63000:1", timeout=45000)
        time.sleep(3)
        print("URL on Hub:", page.url)
        
        # Find My Classes or Class link
        page.locator("a:has-text('My Classes'), a[href*='63000:14:']").first.click(force=True)
        time.sleep(4)
        print("URL after My Classes:", page.url)
        
        # Click into Course Outline if on Page 100
        p14 = page.locator("a[href*='63000:14:']").first
        if p14.count() > 0:
            p14.click(force=True)
            time.sleep(4)
            print("URL after P14:", page.url)
            
        # Section 4
        s4 = page.locator("a:has-text('Section 4')").first
        if s4.count() > 0:
            s4.click(force=True)
            time.sleep(4)
            print("URL after Section 4:", page.url)
            
        # Quiz: DP - Section 4
        quiz_link = page.locator("a:has-text('Quiz: DP - Section 4')").first
        if quiz_link.count() > 0:
            quiz_link.click(force=True)
            time.sleep(4)
            print("URL after Quiz link:", page.url)
            
        # Take an Assessment
        take_btn = page.locator("button:has-text('Take an Assessment'), a:has-text('Take an Assessment')").first
        if take_btn.count() > 0:
            take_btn.click(force=True)
            time.sleep(3)
            print("Clicked Take an Assessment")
            
        # Modal Start
        start_btn = page.locator("button:has-text('Start'), .t-Button:has-text('Start'), input[value='Start']").first
        if start_btn.count() > 0:
            start_btn.click(force=True)
            time.sleep(4)
            print("Clicked Start button. Current URL:", page.url)
            
        # Now we are on Question 1! Dump the entire HTML!
        html_dump = page.evaluate("""() => {
            const body = document.body;
            // Find question title / number
            const qTitle = document.querySelector('h1, h2, h3, .question-text, .t-Region-title')?.innerText || '';
            // Get all elements inside the assessment region
            const region = document.querySelector('.t-Region') || document.querySelector('.t-Body-content') || document.body;
            return {
                title: document.title,
                url: window.location.href,
                qTitle,
                html: region.innerHTML
            };
        }""")
        
        with open("var/question_dom_dump.html", "w", encoding="utf-8") as f:
            f.write(html_dump["html"])
            
        print("DOM dumped successfully to var/question_dom_dump.html!")
        time.sleep(2)
        browser.close()

if __name__ == "__main__":
    main()
