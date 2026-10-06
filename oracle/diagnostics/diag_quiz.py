"""Detailed diagnosis: why doesn't Submit Answer advance to next question?"""
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
        signed = ("My Classes" in text or "Sign Out" in text) and "Username or email" not in text
        print(f"SIGNED IN: {signed}")
        if not signed:
            print("Waiting for sign-in (15 min)...")
            deadline = time.time() + 900
            while time.time() < deadline:
                time.sleep(3)
                try:
                    text = page.locator("body").inner_text(timeout=4000) or ""
                except Exception:
                    continue
                if ("My Classes" in text or "Sign Out" in text) and "Username or email" not in text:
                    print("SIGNED IN!")
                    time.sleep(4)
                    break
            else:
                print("TIMEOUT waiting for sign-in")
                return 1

        # Navigate to course outline
        print("\n--- Navigating to course ---")
        page.evaluate("""() => {
            const a = Array.from(document.querySelectorAll('a')).find(a =>
                (a.innerText || '').toLowerCase().includes('my classes'));
            if (a) a.click();
        }""")
        time.sleep(5)
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
        print(f"URL: {page.url[:80]}")

        # Click first incomplete section
        print("\n--- Clicking section ---")
        page.evaluate("""() => {
            const containers = Array.from(document.querySelectorAll('tr, .a-CardView-item, .t-Card, li, .t-TreeNav-item, .t-Region'));
            for (const c of containers) {
                const text = (c.innerText || '').toLowerCase();
                if (/section\\s*\\d+/i.test(text) || /quiz\\s*:\\s*dp/i.test(text)) {
                    const isDone = text.includes('100%') || text.includes('passed');
                    if (!isDone) {
                        const link = c.querySelector('a') || (c.tagName === 'A' ? c : null);
                        if (link) { link.click(); return; }
                    }
                }
            }
        }""")
        time.sleep(5)
        print(f"URL: {page.url[:80]}")

        # On Page 15 — inspect Take an Assessment button in detail
        print("\n--- Page 15: Take an Assessment button details ---")
        btn_details = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a, .t-Button, [role="button"]'));
            const matches = btns.filter(b => {
                const t = (b.innerText || b.value || '').trim().toLowerCase();
                return t.includes('take an assessment') || t.includes('start') || t.includes('finish assessment');
            });
            return matches.map(b => {
                const rect = b.getBoundingClientRect();
                return {
                    tag: b.tagName,
                    text: (b.innerText || b.value || '').trim(),
                    href: b.href || '',
                    onclick: b.getAttribute('onclick') || '',
                    disabled: b.disabled,
                    classes: b.className,
                    id: b.id,
                    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                    visible: rect.width > 0 && rect.height > 0,
                    parentTag: b.parentElement ? b.parentElement.tagName : '',
                    parentHref: b.parentElement ? (b.parentElement.href || '') : '',
                };
            });
        }""")
        for b in btn_details:
            print(f"  BTN: {b}")

        # Count tabs before
        context = page.context
        tabs_before = len(context.pages)
        print(f"\n  Tabs before click: {tabs_before}")

        # Click Take an Assessment
        print("\n--- Clicking Take an Assessment ---")
        page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a, .t-Button, [role="button"]'));
            const b = btns.find(x => {
                const t = (x.innerText || x.value || '').trim().toLowerCase();
                return t === 'take an assessment' || t === 'start';
            });
            if (b) b.click();
        }""")
        time.sleep(5)

        tabs_after = len(context.pages)
        print(f"  Tabs after click: {tabs_after}")
        print(f"  URL: {page.url[:80]}")
        print(f"  Title: {page.title()}")

        if tabs_after > tabs_before:
            page = context.pages[-1]
            page.bring_to_front()
            print(f"  Switched to new tab: {page.url[:80]}")
            time.sleep(3)

        # Check if we're on the quiz page
        body = page.locator("body").inner_text(timeout=5000) or ""
        print(f"\n  Body length: {len(body)}")
        print(f"  Has 'Question': {'Question' in body}")
        print(f"  Has 'Take the Assessment': {'Take the Assessment' in body}")
        print(f"  Body[:500]: {body[:500]}")

        # Check for iframes
        frames = page.frames
        print(f"\n  Frames: {len(frames)}")
        for f in frames:
            print(f"    frame url: {f.url[:80]}")
            try:
                ft = f.locator("body").inner_text(timeout=3000) or ""
                print(f"    frame text len: {len(ft)}, has Question: {'Question' in ft}")
            except Exception as e:
                print(f"    frame text error: {e}")

        # If there's a "Take the Assessment" or "Start" button on this page, click it
        if "Take the Assessment" in body or "Start" in body:
            print("\n--- Found Start/Take the Assessment on quiz page ---")
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a, .t-Button, [role="button"]'));
                const b = btns.find(x => {
                    const t = (x.innerText || x.value || '').trim().toLowerCase();
                    return t.includes('take the assessment') || t === 'start' || t.includes('begin');
                });
                if (b) b.click();
            }""")
            time.sleep(5)
            print(f"  URL: {page.url[:80]}")
            body = page.locator("body").inner_text(timeout=5000) or ""
            print(f"  Body[:500]: {body[:500]}")

        # Now try to find the quiz frame and interact
        print("\n--- Looking for quiz frame ---")
        target = page
        for f in page.frames:
            try:
                if "p=63000:190" in f.url or f.locator("input[type='radio'], input[type='checkbox']").count() > 0:
                    target = f
                    print(f"  Found quiz frame: {f.url[:80]}")
                    break
            except Exception:
                pass

        # Extract question
        q_text = target.evaluate("""() => {
            const body = document.body.innerText;
            const m = body.match(/Question\\s+\\d+\\s+of\\s+\\d+([\\s\\S]*?)(?=Choices|True or False\\?)/i);
            if (m && m[1]) return m[1].replace(/Instructions/gi, '').trim();
            return '';
        }""")
        print(f"  Question: {q_text[:100]}")

        # Extract choices
        choices = target.evaluate("""() => {
            const labels = Array.from(document.querySelectorAll("label, .apex-item-option, [role='checkbox'], [role='radio']"));
            const items = [];
            labels.forEach(l => {
                const txt = (l.innerText || l.textContent || '').trim();
                if (txt && txt.length > 0 && txt.length < 350 && !items.includes(txt)) {
                    items.push(txt);
                }
            });
            return items;
        }""")
        print(f"  Choices: {choices}")

        # Check input details
        inputs = target.evaluate("""() => {
            const inps = Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox']"));
            return inps.map(i => ({
                type: i.type,
                id: i.id,
                name: i.name,
                value: i.value,
                checked: i.checked,
                disabled: i.disabled,
                labelFor: (document.querySelector(`label[for="${i.id}"]`) || {}).innerText || '',
            }));
        }""")
        print(f"\n  Inputs ({len(inputs)}):")
        for inp in inputs:
            print(f"    {inp}")

        # Select first option
        print("\n--- Selecting option 1 ---")
        target.evaluate("""() => {
            const inps = Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox']"));
            if (inps.length > 0) {
                inps[0].click();
                inps[0].checked = true;
                inps[0].dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""")
        time.sleep(1)

        # Verify selection
        checked = target.evaluate("""() => {
            return Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox']"))
                .filter(i => i.checked).length;
        }""")
        print(f"  Checked after select: {checked}")

        # Find Submit Answer button
        print("\n--- Looking for Submit Answer button ---")
        submit_btns = target.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a.t-Button, input[type="button"], input[type="submit"]'));
            return btns.filter(b => {
                const t = (b.innerText || b.value || '').trim().toLowerCase();
                return t.includes('submit') || t.includes('next') || t.includes('save');
            }).map(b => ({
                tag: b.tagName,
                text: (b.innerText || b.value || '').trim(),
                disabled: b.disabled,
                classes: b.className,
                id: b.id,
                rect: (() => { const r = b.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; })(),
            }));
        }""")
        for b in submit_btns:
            print(f"  BTN: {b}")

        # Click Submit Answer
        print("\n--- Clicking Submit Answer ---")
        target.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a.t-Button, input[type="button"], input[type="submit"]'));
            const b = btns.find(x => {
                const t = (x.innerText || x.value || '').trim().toLowerCase();
                return t === 'submit answer' || t === 'submit' || t === 'next question';
            });
            if (b) b.click();
        }""")
        time.sleep(4)

        # Check if question advanced
        q_text2 = target.evaluate("""() => {
            const body = document.body.innerText;
            const m = body.match(/Question\\s+\\d+\\s+of\\s+\\d+/i);
            return m ? m[0] : '';
        }""")
        print(f"  Question after submit: {q_text2}")
        print(f"  URL: {page.url[:80]}")

        # Check for dialogs/modals
        dialog = page.evaluate("""() => {
            const d = document.querySelector('.ui-dialog, [role="dialog"], .t-DialogRegion, .ui-widget-overlay');
            if (d && d.offsetHeight > 0) {
                return { visible: true, text: (d.innerText || '').substring(0, 200) };
            }
            return { visible: false };
        }""")
        print(f"  Dialog: {dialog}")

        # Screenshot
        shot = ROOT / "var" / "tmp" / "quiz-diag.png"
        page.screenshot(path=str(shot))
        print(f"\n  Screenshot: {shot}")

        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
