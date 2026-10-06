import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "var" / "sessions" / "oracle"

HOST_RESOLVER_RULES = (
    "MAP academy.oracle.com 23.217.111.104,"
    "MAP signon.oracle.com 23.217.111.57,"
    "MAP login-ext.identity.oraclecloud.com 131.186.9.131,"
    "MAP www.oracle.com 23.217.111.104"
)

MEMBER_HUB_URL = "https://academy.oracle.com/pls/f?p=63000:1"

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            f"--host-resolver-rules={HOST_RESOLVER_RULES}",
            "--enable-features=DnsOverHttps",
            "--dns-over-https-mode=automatic"
        ]
    )
    page = context.pages[0] if context.pages else context.new_page()

    try:
        print("Navigating to Member Hub...")
        page.goto(MEMBER_HUB_URL, timeout=45000, wait_until="domcontentloaded")
        time.sleep(5)
        print("Current URL:", page.url)
        print("Title:", page.title())

        # If on page 100, click Database Programming
        if "p=63000:100" in page.url or "my classes" in page.title().lower():
            print("On Page 100. Clicking course...")
            page.locator("a:has-text('Database Programming with SQL'), a:has-text('Database Programming')").first.click()
            time.sleep(5)

        # Dump current HTML structure of choices or buttons if any
        html_dump = page.evaluate("""() => {
            return {
                inputs: Array.from(document.querySelectorAll('input')).map(i => ({
                    type: i.type,
                    name: i.name,
                    id: i.id,
                    value: i.value,
                    checked: i.checked,
                    outerHTML: i.outerHTML.substring(0, 150)
                })),
                labels: Array.from(document.querySelectorAll('label')).map(l => ({
                    for: l.htmlFor,
                    text: l.innerText,
                    outerHTML: l.outerHTML.substring(0, 150)
                })),
                buttons: Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"]')).map(b => ({
                    text: b.innerText || b.value,
                    id: b.id,
                    className: b.className,
                    outerHTML: b.outerHTML.substring(0, 150)
                }))
            };
        }""")
        print("\n--- Inputs Found ---")
        for inp in html_dump['inputs']:
            print(" ", inp)
        print("\n--- Labels Found ---")
        for lbl in html_dump['labels'][:10]:
            print(" ", lbl)
        print("\n--- Buttons Found ---")
        for btn in html_dump['buttons']:
            print(" ", btn)

        time.sleep(10)
    finally:
        context.close()
