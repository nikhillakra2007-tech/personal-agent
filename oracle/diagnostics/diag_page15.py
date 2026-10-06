"""Diagnose Page 15 'Take an Assessment' button behavior."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.execution.browser.sessions import BrowserSessions

HUB = "https://academy.oracle.com/pls/f?p=63000:1"


def main() -> int:
    s = BrowserSessions(ROOT / "var" / "sessions" / "oracle", headless=False)
    s.launch()
    try:
        page = s._require_context().new_page()
        page.goto(HUB, wait_until="load", timeout=30000)
        time.sleep(5)

        text = page.locator("body").inner_text(timeout=5000) or ""
        print(f"HUB URL: {page.url}")
        print(f"HUB TITLE: {page.title()}")
        print(f"SIGNED IN: {'My Classes' in text or 'Sign Out' in text}")
        print(f"BODY[:300]: {text[:300]}")

        # Navigate to course outline via My Classes
        print("\n--- Navigating to My Classes ---")
        page.evaluate("""() => {
            const a = Array.from(document.querySelectorAll('a')).find(a =>
                (a.innerText || '').toLowerCase().includes('my classes'));
            if (a) a.click();
        }""")
        time.sleep(5)
        print(f"URL: {page.url}")
        print(f"TITLE: {page.title()}")

        # Click Take Class / course entry
        print("\n--- Clicking course entry ---")
        page.evaluate("""() => {
            const a = Array.from(document.querySelectorAll('a')).find(a =>
                (a.href || '').includes('63000:14:'));
            if (a) { a.click(); return; }
            const candidates = Array.from(document.querySelectorAll('.a-CardView-item, .t-Card, tr, li, div'));
            const match = candidates.find(c => (c.innerText || '').toLowerCase().includes('database programming'));
            if (match) {
                const btn = match.querySelector('a, button, [role="button"]') || match;
                btn.click();
            }
        }""")
        time.sleep(5)
        print(f"URL: {page.url}")
        print(f"TITLE: {page.title()}")

        # Click Section 6
        print("\n--- Clicking Section 6 ---")
        page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a')).filter(a => {
                const t = (a.innerText || '').trim();
                return /section\s*6/i.test(t);
            });
            for (const l of links) {
                const parent = l.closest('tr') || l.closest('li') || l.closest('.t-Card') || l.parentElement;
                const fullText = (parent ? parent.innerText : l.innerText).toLowerCase();
                if (!fullText.includes('100%') && !fullText.includes('passed')) {
                    l.click();
                    return;
                }
            }
        }""")
        time.sleep(5)
        print(f"URL: {page.url}")
        print(f"TITLE: {page.title()}")

        # Now on Page 15 - inspect the Take an Assessment button
        print("\n--- Page 15: Inspecting 'Take an Assessment' button ---")
        btn_info = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a, .t-Button, input[type="button"]'));
            const matches = btns.filter(b => {
                const t = (b.innerText || b.value || '').trim().toLowerCase();
                return t.includes('take an assessment') || t.includes('start') || t.includes('finish assessment');
            });
            return matches.map(b => ({
                tag: b.tagName,
                text: (b.innerText || b.value || '').trim(),
                href: b.href || '',
                onclick: b.getAttribute('onclick') || '',
                disabled: b.disabled,
                classes: b.className,
                id: b.id,
                rect: b.getBoundingClientRect(),
            }));
        }""")
        for b in btn_info:
            print(f"  BTN: {b}")

        # Check for iframes
        frames = page.frames
        print(f"\n  FRAMES: {len(frames)}")
        for f in frames:
            print(f"    frame: name={f.name!r} url={f.url[:80]}")

        # Try clicking and see what happens
        print("\n--- Clicking 'Take an Assessment' ---")
        page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a, .t-Button, input[type="button"]'));
            const b = btns.find(x => {
                const t = (x.innerText || x.value || '').trim().toLowerCase();
                return t === 'take an assessment';
            });
            if (b) b.click();
        }""")
        time.sleep(6)
        print(f"URL after click: {page.url}")
        print(f"TITLE after click: {page.title()}")
        text2 = page.locator("body").inner_text(timeout=5000) or ""
        print(f"BODY[:500]: {text2[:500]}")

        # Check if a new tab opened
        pages = s._require_context().pages
        print(f"\n  PAGES: {len(pages)}")
        for p in pages:
            print(f"    page: {p.url[:80]}")

        shot = ROOT / "var" / "tmp" / "page15-diag.png"
        page.screenshot(path=str(shot))
        print(f"\nSHOT: {shot}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
