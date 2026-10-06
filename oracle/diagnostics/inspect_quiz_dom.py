import os
import sys
import time
from playwright.sync_api import sync_playwright

USER_DATA_DIR = os.path.abspath("var/sessions/oracle")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--host-resolver-rules="
                "MAP academy.oracle.com 23.217.111.104,"
                "MAP signon.oracle.com 23.217.111.57,"
                "MAP login-ext.identity.oraclecloud.com 131.186.9.131"
            ]
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        print(f"Current URL: {page.url}")
        
        # If on page 190 or 15, inspect DOM
        time.sleep(3)
        print(f"URL after 3s: {page.url}")
        
        # Dump question container and inputs
        info = page.evaluate("""() => {
            const result = {
                title: document.title,
                url: window.location.href,
                h1: Array.from(document.querySelectorAll('h1, h2, h3')).map(h => h.innerText),
                inputs: Array.from(document.querySelectorAll('input')).map(i => ({
                    id: i.id,
                    type: i.type,
                    name: i.name,
                    value: i.value,
                    checked: i.checked,
                    className: i.className,
                    visible: i.offsetParent !== null
                })),
                labels: Array.from(document.querySelectorAll('label')).map(l => ({
                    for: l.getAttribute('for'),
                    text: (l.innerText || '').trim(),
                    className: l.className
                })),
                buttons: Array.from(document.querySelectorAll('button, a.t-Button, input[type="button"], input[type="submit"]')).map(b => ({
                    tag: b.tagName,
                    text: (b.innerText || b.value || '').trim(),
                    className: b.className,
                    id: b.id,
                    visible: b.offsetParent !== null
                })),
                questionBoxes: Array.from(document.querySelectorAll('.t-Region, .question-text, .apex-item-display-only, [id*="QUESTION"], [id*="P190"]')).map(el => ({
                    id: el.id,
                    className: el.className,
                    text: (el.innerText || '').trim().substring(0, 100)
                }))
            };
            return result;
        }""")
        
        print("\n--- DOM INFO ---")
        import json
        print(json.dumps(info, indent=2))
        
        time.sleep(2)
        browser.close()

if __name__ == "__main__":
    main()
