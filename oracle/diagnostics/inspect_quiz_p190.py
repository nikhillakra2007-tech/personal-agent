import os
import re
import sys
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
        
        print("Navigating to academy.oracle.com...")
        page.goto("https://academy.oracle.com/pls/f?p=63000:1", timeout=60000)
        time.sleep(3)
        
        print(f"Current URL: {page.url}")
        
        # Navigate to My Classes -> Course Outline -> Section 4 Quiz
        # Check if already on page 1, 100, 14, 15, or 190
        for step in range(10):
            url = page.url
            print(f"[STEP {step}] Current URL: {url}")
            
            if "63000:190" in url:
                print("Already on Quiz Page 190!")
                break
                
            if "63000:1" in url or "63000:100" in url:
                # Find classes link
                classes_link = page.locator("a[href*='63000:100:'], a:has-text('My Classes'), a[href*='63000:14:']").first
                if classes_link.count() > 0:
                    classes_link.click()
                    time.sleep(4)
                    continue
                    
            if "63000:14" in url:
                # Find Section 4 quiz
                s4_quiz = page.locator("a, button, li, tr").filter(has_text=re.compile(r"Quiz\s*:\s*DP\s*-\s*Section\s*4", re.I)).first
                if s4_quiz.count() > 0:
                    s4_quiz.click()
                    time.sleep(4)
                    continue
                    
            if "63000:15" in url:
                # Check modal start or take assessment
                start_btn = page.locator("button:has-text('Start'), .t-Button:has-text('Start'), input[value='Start']").first
                if start_btn.count() > 0 and start_btn.is_visible():
                    start_btn.click()
                    time.sleep(4)
                    continue
                take_btn = page.locator("button:has-text('Take an Assessment'), a:has-text('Take an Assessment')").first
                if take_btn.count() > 0:
                    take_btn.click()
                    time.sleep(3)
                    continue
                    
            time.sleep(2)
            
        print("\n--- Inspecting Page 190 DOM ---")
        time.sleep(2)
        html_snippet = page.evaluate("""() => {
            const container = document.querySelector('.t-Body-content') || document.querySelector('.t-Region') || document.body;
            // Get question elements
            const qHeader = Array.from(document.querySelectorAll('h1, h2, h3, h4, .t-Region-title, .question-text')).map(e => e.innerText);
            
            // Get all inputs
            const inputs = Array.from(document.querySelectorAll("input")).map(i => ({
                id: i.id,
                name: i.name,
                type: i.type,
                value: i.value,
                checked: i.checked,
                outerHTML: i.outerHTML
            }));
            
            // Get all choices / labels
            const labels = Array.from(document.querySelectorAll("label, .apex-item-option, .choice")).map(l => ({
                for: l.getAttribute('for'),
                text: l.innerText,
                outerHTML: l.outerHTML.substring(0, 200)
            }));
            
            // Get error message if present
            const errors = Array.from(document.querySelectorAll('.a-Notification--error, .t-Alert--error, [role="alert"]')).map(e => e.innerText);
            
            return {
                url: window.location.href,
                qHeader,
                inputs,
                labels,
                errors
            };
        }""")
        
        import json
        print(json.dumps(html_snippet, indent=2))
        time.sleep(5)
        browser.close()

if __name__ == "__main__":
    main()
