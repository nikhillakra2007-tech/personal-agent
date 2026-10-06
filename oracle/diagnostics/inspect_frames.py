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
    page.goto("https://academy.oracle.com/pls/f?p=63000:1", timeout=45000)
    time.sleep(4)
    print("Page URL:", page.url)
    print("Frames count:", len(page.frames))
    for idx, f in enumerate(page.frames):
        print(f"Frame {idx}: name='{f.name}', url='{f.url}'")
    context.close()
