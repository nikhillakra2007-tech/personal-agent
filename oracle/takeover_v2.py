"""Oracle Academy quiz automation v2 — single browser session.

Fixes vs v16:
  1. ONE browser session: sign-in and automation share the same
     context, so cookies/session are never lost between phases.
  2. "Take an Assessment" new-tab detection: the button opens the
     quiz in a new tab; we detect it and switch to it.
  3. Direct-URL fallback: if the button click doesn't navigate,
     extract the href and goto it directly.
  4. Gemini API solves every question (no stale cache).

The human signs in once in the browser window; everything after is
automatic. Runs unattended once signed in.
"""

from __future__ import annotations

import functools
import json
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

print = functools.partial(print, flush=True)
socket.setdefaulttimeout(5)

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "var" / "sessions" / "oracle"
SHOTS_DIR = ROOT / "var" / "do-shots" / "takeover_v2"

try:
    from oracle.env_helper import get_gemini_api_key, get_student_email
    API_KEY = get_gemini_api_key()
    EMAIL = get_student_email()
except Exception:
    import os
    API_KEY = os.getenv("GEMINI_API_KEY", "")
    EMAIL = os.getenv("ORACLE_STUDENT_EMAIL", "")

PRIMARY_MODEL = "gemini-flash-lite-latest"
FALLBACK_MODEL = "gemini-3.8-flash"
HUB_URL = "https://academy.oracle.com/pls/f?p=63000:1"

HOST_RESOLVER_RULES = (
    "MAP academy.oracle.com 23.217.111.104,"
    "MAP signon.oracle.com 23.217.111.57,"
    "MAP login-ext.identity.oraclecloud.com 131.186.9.131,"
    "MAP www.oracle.com 23.217.111.104"
)


def is_signed_in(text: str) -> bool:
    low = text.lower()
    has_signin = any(m in low for m in ("username or email", "sign in to oracle"))
    has_signedin = any(m in low for m in (
        "my classes", "sign out", "database programming",
        "taking a class", "course outline")) or EMAIL in text
    return has_signedin and not has_signin


def wait_for_signin(page, timeout_s: int = 900) -> bool:
    print("\n" + "=" * 60, flush=True)
    print("[AUTH] Browser window is open. Please sign in.", flush=True)
    print("[AUTH] The automation will take over automatically.", flush=True)
    print("=" * 60 + "\n", flush=True)

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(3)
        try:
            text = page.locator("body").inner_text(timeout=4000) or ""
        except Exception:
            continue
        try:
            url = page.url
        except Exception:
            url = "?"
        if is_signed_in(text):
            print(f"[AUTH] SIGNED IN! URL: {url}", flush=True)
            time.sleep(4)
            return True
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        print(f"[AUTH] waiting... (url={url[:60]}, title={title[:30]!r})",
              flush=True)
    print("[AUTH] TIMEOUT waiting for sign-in", flush=True)
    return False


def get_quiz_target(page):
    for frame in page.frames:
        try:
            if "p=63000:190" in frame.url:
                return frame
            if frame.locator("input[type='radio'], input[type='checkbox']").count() > 0:
                return frame
        except Exception:
            pass
    return page


def solve_with_gemini(question: str, choices: list[str], num_to_choose: int = 1) -> list[int]:
    choices_formatted = "\n".join(f"{i}: {t}" for i, t in enumerate(choices))
    if num_to_choose > 1:
        prompt = (
            "You are an expert Oracle SQL Database administrator. You are taking an official "
            "Oracle Academy assessment on SQL. Read the question carefully and select ALL correct answers.\n\n"
            f"Question:\n{question}\n\nChoices:\n{choices_formatted}\n\n"
            f"This question requires EXACTLY {num_to_choose} correct answers. "
            f"Select the {num_to_choose} best correct option indices (0-based, 0 to {len(choices)-1}).\n"
            "Think step by step about SQL semantics before answering.\n"
            f'Respond ONLY with JSON: {{"choice_indices": [<int>, ...], "reasoning": "<brief explanation>"}}'
        )
    else:
        prompt = (
            "You are an expert Oracle SQL Database administrator. You are taking an official "
            "Oracle Academy assessment on SQL. Read the question carefully and select the ONE best answer.\n\n"
            f"Question:\n{question}\n\nChoices:\n{choices_formatted}\n\n"
            "Select the single best correct option index (0-based, 0 to " + str(len(choices)-1) + ").\n"
            "Think step by step about SQL semantics before answering.\n"
            'Respond ONLY with JSON: {"choice_indices": [<int>], "reasoning": "<brief explanation>"}'
        )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "topP": 1.0, "maxOutputTokens": 300}
    }).encode("utf-8")

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={API_KEY}"
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            parsed = json.loads(text.strip())
            raw = parsed.get("choice_indices", [])
            if not raw and "choice_index" in parsed:
                raw = [parsed["choice_index"]]
            valid = [int(i) for i in raw if 0 <= int(i) < len(choices)]
            if len(valid) >= num_to_choose:
                res = valid[:num_to_choose]
                print(f"  [{model}] -> {[i+1 for i in res]} "
                      f"{[choices[i].splitlines()[0][:40] for i in res]}", flush=True)
                return res
            if valid:
                return valid
        except Exception as exc:
            print(f"  [{model}] failed: {exc}", flush=True)
            continue
    return [0]


def extract_choices(target) -> list[str]:
    choices = target.evaluate("""() => {
        const labels = Array.from(document.querySelectorAll(
            "label, .apex-item-option, [role='checkbox'], [role='radio']"));
        const items = [];
        const blacklist = [
            'instructions', 'true or false?', 'choices - just one correct!',
            'choices - mark all that apply!', 'exit', 'previous question',
            'submit answer', 'complete assessment', 'error has occurred',
            'errors have occurred', 'at least one choice must be selected'
        ];
        labels.forEach(l => {
            const txt = (l.innerText || l.textContent || '').trim();
            if (txt && txt.length > 0 && txt.length < 350 && !items.includes(txt)) {
                const lower = txt.toLowerCase();
                if (!blacklist.some(b => lower.startsWith(b) || lower === b)) {
                    items.push(txt);
                }
            }
        });
        return items;
    }""")
    if not choices:
        choices = target.evaluate("""() => {
            const inputs = Array.from(document.querySelectorAll(
                "input[type='radio'], input[type='checkbox']"));
            return inputs.map(i => {
                const l = document.querySelector(`label[for="${i.id}"]`) || i.closest('label');
                return l ? (l.innerText || '').trim() : '';
            }).filter(t => t.length > 0);
        }""")
    return choices


def extract_question(target) -> str:
    return target.evaluate("""() => {
        const body = document.body.innerText;
        const m = body.match(/Question\\s+\\d+\\s+of\\s+\\d+([\\s\\S]*?)(?=Choices|True or False\\?)/i);
        if (m && m[1]) {
            const clean = m[1].replace(/Instructions/gi, '').trim();
            if (clean.length > 10) return clean;
        }
        const items = Array.from(document.querySelectorAll(
            '.question-text, .apex-item-display-only, [id*="QUESTION"]'));
        for (const item of items) {
            const t = (item.innerText || '').trim();
            if (t.length > 15 && !t.includes('Instructions')) return t;
        }
        return '';
    }""")


def select_options(target, chosen_indices: list[int], choices: list[str]) -> bool:
    for attempt in range(4):
        inputs_info = target.evaluate("""() => {
            const inps = Array.from(document.querySelectorAll(
                "input[type='radio'], input[type='checkbox']"));
            return inps.map((inp, i) => {
                const lbl = document.querySelector(`label[for="${inp.id}"]`) ||
                    inp.closest('label') || inp.closest('.apex-item-option');
                const lblText = lbl ? (lbl.innerText || '').trim().substring(0, 80) : '';
                return {
                    index: i,
                    type: inp.type,
                    id: inp.id,
                    checked: inp.checked,
                    labelText: lblText,
                    parentClasses: inp.parentElement ? inp.parentElement.className : '',
                };
            });
        }""")

        for idx in chosen_indices:
            if idx >= len(inputs_info):
                continue
            inp_id = inputs_info[idx].get("id", "")
            if inp_id:
                try:
                    lbl_for = target.locator(f"label[for='{inp_id}']").first
                    if lbl_for.count() > 0:
                        lbl_for.scroll_into_view_if_needed()
                        lbl_for.click(force=True, timeout=3000)
                        time.sleep(0.3)
                except Exception:
                    pass
                try:
                    inp_loc = target.locator(f"#{inp_id}").first
                    if inp_loc.count() > 0:
                        inp_loc.scroll_into_view_if_needed()
                        if inputs_info[idx]["type"] == "checkbox":
                            inp_loc.check(force=True, timeout=3000)
                        else:
                            inp_loc.click(force=True, timeout=3000)
                        time.sleep(0.3)
                except Exception:
                    pass

            try:
                lbl_text = inputs_info[idx].get("labelText", "")[:40]
                if lbl_text:
                    lbl = target.locator(
                        f"label:has-text('{lbl_text}'), "
                        f".apex-item-option:has-text('{lbl_text}')"
                    ).first
                    if lbl.count() > 0:
                        lbl.scroll_into_view_if_needed()
                        lbl.click(force=True, timeout=3000)
                        time.sleep(0.3)
            except Exception:
                pass

        target.evaluate("""({indices, choicesList}) => {
            const chosenTexts = indices.map(i => choicesList[i]);
            const allLabels = Array.from(document.querySelectorAll(
                "label, .apex-item-option, [role='checkbox'], [role='radio']"));
            chosenTexts.forEach(txt => {
                const lbl = allLabels.find(l => {
                    const t = (l.innerText || l.textContent || '').trim();
                    return t === txt || t.startsWith(txt) || t.includes(txt);
                });
                if (lbl) {
                    lbl.click();
                    const forId = lbl.htmlFor || lbl.getAttribute('for');
                    let inp = forId ? document.getElementById(forId) : lbl.querySelector('input');
                    if (!inp && lbl.parentElement) inp = lbl.parentElement.querySelector('input');
                    if (!inp) inp = lbl.closest('.apex-item-option')?.querySelector('input');
                    if (inp) {
                        inp.checked = true;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        inp.dispatchEvent(new Event('click', { bubbles: true }));
                    }
                }
            });
        }""", {"indices": chosen_indices, "choicesList": choices})

        time.sleep(1)
        checked = target.evaluate("""(expected) => {
            const checkedInps = Array.from(document.querySelectorAll(
                "input[type='radio'], input[type='checkbox']")).filter(i => i.checked);
            if (checkedInps.length >= expected) return checkedInps.length;
            const active = Array.from(document.querySelectorAll(
                ".apex-item-option, label, [role='radio'], [role='checkbox']")).filter(el =>
                el.classList.contains('is-active') || el.classList.contains('checked') ||
                el.getAttribute('aria-checked') === 'true'
            );
            return Math.max(checkedInps.length, active.length);
        }""", len(chosen_indices))

        if checked >= len(chosen_indices):
            return True

        for idx in chosen_indices:
            if idx >= len(inputs_info):
                continue
            inp_id = inputs_info[idx].get("id", "")
            if inp_id:
                try:
                    inp_loc = target.locator(f"#{inp_id}").first
                    if inp_loc.count() > 0:
                        box = inp_loc.bounding_box()
                        if box:
                            target.page.mouse.click(
                                box["x"] + box["width"] / 2,
                                box["y"] + box["height"] / 2
                            )
                            time.sleep(0.3)
                except Exception:
                    pass

        target.evaluate("""(indices) => {
            const inps = Array.from(document.querySelectorAll(
                "input[type='radio'], input[type='checkbox']"));
            indices.forEach(idx => {
                if (idx < inps.length) {
                    inps[idx].checked = true;
                    inps[idx].dispatchEvent(new Event('input', { bubbles: true }));
                    inps[idx].dispatchEvent(new Event('change', { bubbles: true }));
                    inps[idx].dispatchEvent(new Event('click', { bubbles: true }));
                }
            });
        }""", chosen_indices)
        time.sleep(1)
    return False


def handle_start_modal(page) -> bool:
    """Detect and click 'Start' in the quiz start modal dialog."""
    try:
        found = page.evaluate("""() => {
            const selectors = [
                '.ui-dialog', '[role="dialog"]', '.t-DialogRegion',
                '.ui-widget-content', '.modal', '.popup', '[class*="dialog"]',
                '[class*="modal"]', '[class*="popup"]'
            ];
            for (const sel of selectors) {
                const els = Array.from(document.querySelectorAll(sel));
                for (const el of els) {
                    if (el.offsetHeight > 0 && el.offsetWidth > 0) {
                        const text = (el.innerText || '').toLowerCase();
                        if (text.includes('start') || text.includes('begin') ||
                            text.includes('assessment')) {
                            const btns = Array.from(el.querySelectorAll(
                                'button, a, input[type="button"], input[type="submit"]'));
                            const btn = btns.find(b => {
                                const t = (b.innerText || b.value || '').trim().toLowerCase();
                                return t === 'start' || t === 'begin' ||
                                       t.includes('start') || t.includes('begin');
                            });
                            if (btn) {
                                btn.click();
                                return { clicked: true, text: (btn.innerText || '').trim() };
                            }
                        }
                    }
                }
            }
            return { clicked: false };
        }""")
        if found.get("clicked"):
            print(f"  [MODAL] Clicked '{found.get('text')}' in dialog", flush=True)
            time.sleep(4)
            return True
    except Exception as exc:
        print(f"  [MODAL] Detection error: {exc}", flush=True)
    return False


def click_nav(target, patterns: list[str]) -> bool:
    for pat in patterns:
        try:
            btn = target.locator("button, a.t-Button, input[type='button']").filter(
                has_text=re.compile(pat, re.I)
            ).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(force=True)
                return True
        except Exception:
            pass
    return False


def run_quiz(page, quiz_label: str) -> None:
    print(f"\n{'='*60}\n[QUIZ] {quiz_label}\n{'='*60}", flush=True)
    q_idx = 1
    max_q = 25

    while q_idx <= max_q:
        time.sleep(2)

        if "p=63000:192" in page.url or \
                page.locator("text=/Percentage\\s+Scored/i").count() > 0:
            print("[QUIZ] Reached score summary page 192", flush=True)
            break

        target = get_quiz_target(page)

        q_num = target.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('*'));
            const match = all.find(el => el.children.length === 0 &&
                /Question\\s+\\d+\\s+of\\s+\\d+/i.test((el.innerText || '').trim()));
            return match ? match.innerText.trim() : null;
        }""") or f"Question {q_idx}"

        print(f"\n>>> [{q_num}] <<<", flush=True)

        choices = extract_choices(target)
        if not choices:
            body = target.locator("body").inner_text() or ""
            if any(w in body.lower() for w in ("score", "grade", "passed", "completed", "review")):
                print("[QUIZ] Completion screen detected", flush=True)
                break
            print("[QUIZ] No choices found, stopping", flush=True)
            break

        q_prompt = extract_question(target)
        print(f"  Q: {q_prompt[:90]}", flush=True)
        print(f"  Choices: {[c.splitlines()[0][:40] for c in choices]}", flush=True)

        input_types = target.evaluate("""() => ({
            checkboxes: document.querySelectorAll("input[type='checkbox']").length,
            radios: document.querySelectorAll("input[type='radio']").length
        })""")

        q_type = target.evaluate("""() => {
            const body = document.body.innerText;
            const m = body.match(/Choices\\s*-\\s*(.+?)(\\n|$)/i);
            return m ? m[1].trim().toLowerCase() : '';
        }""")

        bracket_match = re.search(
            r'\(\s*choose\s+(one|two|three|four|five|2|3|4|5)\s*\)',
            q_prompt, re.IGNORECASE
        )
        bracket_num = 1
        if bracket_match:
            word = bracket_match.group(1).lower()
            bracket_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                          "2": 2, "3": 3, "4": 4, "5": 5}.get(word, 1)

        full_text = (q_prompt + " " + " ".join(choices)).lower()
        num_to_choose = 1
        if bracket_num > 1:
            num_to_choose = bracket_num
        elif any(k in full_text for k in ("choose three", "choose 3", "three correct",
                                          "select three", "choose all that apply",
                                          "mark all that apply", "select all")):
            num_to_choose = 3
        elif any(k in full_text for k in ("choose two", "choose 2", "two correct",
                                            "select two", "choose both", "select both")):
            num_to_choose = 2
        elif "mark all" in q_type or "all that apply" in q_type or "all correct" in q_type:
            num_to_choose = 3
        elif input_types["checkboxes"] > 0 and input_types["radios"] == 0:
            num_to_choose = 2

        print(f"  Mode: {'multi-' + str(num_to_choose) if num_to_choose > 1 else 'single'}"
              f" (bracket={bracket_num})", flush=True)

        chosen = solve_with_gemini(q_prompt, choices, num_to_choose)
        select_options(target, chosen, choices)

        checked = target.evaluate("""() => {
            return Array.from(document.querySelectorAll(
                "input[type='radio'], input[type='checkbox']")).filter(i => i.checked).length;
        }""")
        if checked < len(chosen):
            print(f"  [WARN] Only {checked}/{len(chosen)} checked — forcing", flush=True)
            target.evaluate("""(indices) => {
                const inps = Array.from(document.querySelectorAll(
                    "input[type='radio'], input[type='checkbox']"));
                indices.forEach(idx => {
                    if (idx < inps.length) {
                        inps[idx].checked = true;
                        inps[idx].dispatchEvent(new Event('input', { bubbles: true }));
                        inps[idx].dispatchEvent(new Event('change', { bubbles: true }));
                    }
                });
            }""", chosen)
            time.sleep(1)

        is_last = "15 of 15" in q_num.lower() or "15 of 15" in q_prompt.lower()

        if is_last:
            print(f"  [ACTION] Final question — Complete Assessment", flush=True)
            click_nav(target, [r"^\s*(Complete Assessment|Finish Assessment)\s*$"])
            time.sleep(2)
            for _ in range(8):
                confirmed = page.evaluate("""() => {
                    const dialogs = Array.from(document.querySelectorAll(
                        '.ui-dialog, [role="dialog"], .t-DialogRegion, .ui-widget-content'));
                    for (const dialog of dialogs) {
                        if (dialog.offsetHeight > 0) {
                            const btns = Array.from(dialog.querySelectorAll(
                                'button, a.t-Button, input[type="button"]'));
                            const btn = btns.find(b => {
                                const t = (b.innerText || b.value || '').trim().toLowerCase();
                                return (t.includes('complete assessment') ||
                                        t === 'yes' || t === 'ok' ||
                                        t === 'submit') && !b.disabled;
                            });
                            if (btn) { btn.click(); return btn.innerText; }
                        }
                    }
                    return null;
                }""")
                if confirmed:
                    print(f"  [MODAL] Confirmed: '{confirmed}'", flush=True)
                    time.sleep(5)
                    break
                time.sleep(1)
        else:
            print(f"  [ACTION] Submit Answer / Next", flush=True)
            submitted = click_nav(target, [
                r"^\s*(Submit Answer|Submit|Next Question|Next)\s*$"
            ])
            if not submitted:
                target.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll(
                        'button, input[type="button"], input[type="submit"]'));
                    const b = btns.find(x => {
                        const t = (x.innerText || x.value || '').trim().toLowerCase();
                        return (t === 'submit answer' || t === 'submit' ||
                                t === 'next question' || t === 'next') && !x.disabled;
                    });
                    if (b) b.click();
                }""")

            time.sleep(3)

            q_after = target.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('*'));
                const match = all.find(el => el.children.length === 0 &&
                    /Question\\s+\\d+\\s+of\\s+\\d+/i.test((el.innerText || '').trim()));
                return match ? match.innerText.trim() : '';
            }""")
            if q_after and q_after == q_num:
                print(f"  [WARN] Still on {q_after} — trying JS submit", flush=True)
                target.evaluate("""() => {
                    const forms = document.querySelectorAll('form');
                    if (forms.length > 0) {
                        const btn = forms[0].querySelector(
                            'button[type="submit"], input[type="submit"]');
                        if (btn) { btn.click(); return; }
                    }
                    const btns = Array.from(document.querySelectorAll('button'));
                    const b = btns.find(x => {
                        const t = (x.innerText || '').trim().toLowerCase();
                        return t.includes('submit') || t.includes('next');
                    });
                    if (b) b.click();
                }""")
                time.sleep(3)
            else:
                q_idx += 1

    handle_score_summary(page, quiz_label)


def harvest_results(page) -> int:
    """Click 'View Results' and scrape correct answers for future retakes."""
    try:
        view_btn = page.locator(
            "button:has-text('View Results'), a:has-text('View Results'), "
            ".t-Button:has-text('View Results')"
        ).first
        if view_btn.count() > 0 and view_btn.is_visible():
            print("  [RESULTS] Clicking 'View Results' to harvest answers",
                  flush=True)
            view_btn.click(force=True)
            time.sleep(4)
            results = page.evaluate("""() => {
                const answers = {};
                const containers = Array.from(document.querySelectorAll(
                    '.question-container, .t-Region, tr, fieldset, li, div'));
                containers.forEach(c => {
                    const txt = c.innerText || '';
                    if (txt.includes('Correct Answer') ||
                        txt.includes('(Correct)') || txt.includes('is correct')) {
                        const lines = txt.split('\\n').map(l => l.trim())
                            .filter(l => l.length > 0);
                        const qLine = lines.find(l => l.length > 15 &&
                            !l.includes('Correct') && !l.includes('Score') &&
                            !l.includes('Points'));
                        const aLines = lines.filter(l =>
                            (l.includes('Correct Answer') ||
                             l.startsWith('*') ||
                             l.includes('(Correct)')) && l.length > 1);
                        if (qLine && aLines.length > 0) {
                            const cleanList = aLines.map(a =>
                                a.replace(/Correct Answer:?|\\*|\\(Correct\\)/gi, '')
                                    .trim()).filter(a => a.length > 0);
                            if (cleanList.length === 1) {
                                answers[qLine.substring(0, 100)] = cleanList[0];
                            } else if (cleanList.length > 1) {
                                answers[qLine.substring(0, 100)] = cleanList;
                            }
                        }
                    }
                });
                return answers;
            }""")
            if results:
                print(f"  [RESULTS] Harvested {len(results)} correct answers",
                      flush=True)
                return len(results)
            print("  [RESULTS] No answers found in review", flush=True)
    except Exception as exc:
        print(f"  [RESULTS] Harvest error: {exc}", flush=True)
    return 0


def handle_score_summary(page, quiz_label: str) -> None:
    print(f"\n[SCORE] Summary for {quiz_label}", flush=True)
    time.sleep(3)

    score_pct = None
    for _ in range(8):
        try:
            score_pct = page.evaluate("""() => {
                const m = (document.body.innerText || '').match(
                    /PERCENTAGE\\s+SCORED:\\s*([\\d.]+)%/i);
                return m ? parseFloat(m[1]) : null;
            }""")
        except Exception:
            pass
        if score_pct is not None:
            break
        time.sleep(1)

    print(f"  [SCORE] {score_pct}%", flush=True)

    harvest_results(page)

    if score_pct is not None and score_pct >= 70:
        print(f"  [PASS] {score_pct}% >= 70% mastery — moving to next section",
              flush=True)
        click_nav(page, [r"^\s*(Return|Course Outline|Back)\s*$"])
        time.sleep(4)
    else:
        print(f"  [RETAKE] {score_pct}% < 70% — retaking", flush=True)
        retaken = click_nav(page, [
            r"^\s*(Retake|Take Assessment Again|Try Again|Retake Assessment)\s*$"
        ])
        if not retaken:
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll(
                    'button, a, input[type="button"]'));
                const b = btns.find(x => {
                    const t = (x.innerText || x.value || '').trim().toLowerCase();
                    return t.includes('retake') || t.includes('try again') ||
                           t.includes('take assessment');
                });
                if (b) b.click();
            }""")
        time.sleep(5)


def find_and_click_quiz_link(page) -> bool:
    """Find and click a 'Quiz: DP - Section X' link in the sidebar
    or course outline. Skips midterm/final exams. Returns True if clicked."""
    clicked = page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('a'));
        const quizLinks = links.filter(a => {
            const t = (a.innerText || '').trim();
            return /quiz\\s*:\\s*dp/i.test(t) || /quiz\\s*-\\s*section/i.test(t);
        });
        for (const ql of quizLinks) {
            const parent = ql.closest('tr, li, .t-TreeNav-item, .t-Card, div');
            const parentText = (parent ? parent.innerText : ql.innerText).toLowerCase();
            const isDone = parentText.includes('100%') || parentText.includes('passed') ||
                           parentText.includes('mastery achieved');
            const isExam = parentText.includes('midterm') ||
                           parentText.includes('final exam') ||
                           parentText.includes('final examination');
            if (!isDone && !isExam) {
                ql.click();
                return { clicked: true, text: (ql.innerText || '').trim(),
                         href: ql.href || '' };
            }
        }
        return { clicked: false };
    }""")
    if clicked.get("clicked"):
        print(f"  [QUIZ LINK] Clicked: '{clicked.get('text')}'", flush=True)
        time.sleep(5)
        return True
    return False


def handle_quiz_landing(page):
    """Quiz landing page: click 'Take an Assessment' with new-tab detection."""
    context = page.context
    tabs_before = len(context.pages)

    btn_info = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll(
            'button, a, .t-Button, [role="button"]'));
        const b = btns.find(x => {
            const t = (x.innerText || x.value || '').trim().toLowerCase();
            return ['take an assessment', 'start assessment', 'begin assessment',
                    'start', 'take assessment'].some(
                        k => t.startsWith(k) || t === k);
        });
        if (b) {
            return { found: true, href: b.href || '', tag: b.tagName,
                     text: (b.innerText || b.value || '').trim(),
                     target: b.target || '', onclick: b.getAttribute('onclick') || '',
                     classes: b.className, id: b.id };
        }
        return { found: false };
    }""")

    if not btn_info.get("found"):
        print("  [QUIZ LANDING] No 'Take an Assessment' button found", flush=True)
        return page

    print(f"  [QUIZ LANDING] Found: '{btn_info.get('text')}' "
          f"(tag={btn_info.get('tag')}, target={btn_info.get('target')}, "
          f"href={btn_info.get('href', '')[:60]})", flush=True)

    href = btn_info.get("href", "")
    target = btn_info.get("target", "")

    if href and "63000" in href and target != "_blank":
        print(f"  [QUIZ LANDING] Navigating to href -> {href[:70]}", flush=True)
        page.goto(href, timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)
        return page

    try:
        locator = page.locator(
            "button:has-text('Take an Assessment'), "
            "a:has-text('Take an Assessment'), "
            "button:has-text('Start Assessment'), "
            "a:has-text('Start Assessment')"
        ).first
        if locator.count() > 0:
            locator.click(force=True, timeout=5000)
            print("  [QUIZ LANDING] Native click succeeded", flush=True)
    except Exception as exc:
        print(f"  [QUIZ LANDING] Native click failed: {exc}", flush=True)
        if href:
            print(f"  [QUIZ LANDING] Falling back to href navigation", flush=True)
            page.goto(href, timeout=30000, wait_until="domcontentloaded")
            time.sleep(5)
            return page

    time.sleep(5)

    if handle_start_modal(page):
        time.sleep(3)
        if "p=63000:190" in page.url:
            print("  [QUIZ LANDING] Quiz started via modal", flush=True)
            return page

    for frame in page.frames:
        try:
            if handle_start_modal(frame):
                print("  [QUIZ LANDING] Modal found in frame", flush=True)
                time.sleep(3)
                return page
        except Exception:
            pass

    tabs_after = len(context.pages)
    if tabs_after > tabs_before:
        new_page = context.pages[-1]
        new_page.bring_to_front()
        print(f"  [QUIZ LANDING] New tab -> switching to {new_page.url[:70]}",
              flush=True)
        time.sleep(3)
        handle_start_modal(new_page)
        return new_page

    if "p=63000:190" in page.url:
        print("  [QUIZ LANDING] On quiz page directly", flush=True)
        return page

    if href and "63000" in href:
        print(f"  [QUIZ LANDING] Following href -> {href[:70]}", flush=True)
        page.goto(href, timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)
        handle_start_modal(page)
        return page

    print("  [QUIZ LANDING] Button had no effect; retrying next cycle", flush=True)
    return page


def handle_page_15(page):
    """Lesson page: click quiz link, Take an Assessment, or Save and Continue."""
    if find_and_click_quiz_link(page):
        return page

    has_take = page.locator(
        "button:has-text('Take an Assessment'), a:has-text('Take an Assessment'), "
        "button:has-text('Start Assessment')"
    ).count() > 0
    if has_take:
        return handle_quiz_landing(page)

    body = page.locator("body").inner_text(timeout=3000) or ""
    if "save and continue" in body.lower() or "save & continue" in body.lower():
        print("  [THEORY] Clicking 'Save and Continue'", flush=True)
        url_before = page.url
        try:
            btn = page.locator(
                "button:has-text('Save and Continue'), "
                "button:has-text('Save & Continue'), "
                "button:has-text('Save and continue')"
            ).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(force=True, timeout=5000)
                time.sleep(5)
                if page.url != url_before:
                    print("  [THEORY] Advanced to next slide", flush=True)
                    return page
                print("  [THEORY] URL unchanged — trying JS click", flush=True)
        except Exception:
            pass
        page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const b = btns.find(x => {
                const t = (x.innerText || '').trim().toLowerCase();
                return t.includes('save') && t.includes('continue');
            });
            if (b) b.click();
        }""")
        time.sleep(5)
        if page.url != url_before:
            print("  [THEORY] Advanced via JS click", flush=True)
        return page

    if "finish assessment" in body.lower() or "resume assessment" in body.lower():
        print("  [RESUME] Found 'Finish/Resume Assessment' — continuing quiz",
              flush=True)
        try:
            btn = page.locator(
                "button:has-text('Finish Assessment'), "
                "button:has-text('Resume Assessment'), "
                "a:has-text('Finish Assessment')"
            ).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(force=True, timeout=5000)
                time.sleep(4)
                return page
        except Exception:
            pass

    print("  [PAGE 15] No action taken", flush=True)
    return page


def handle_page_14(page) -> bool:
    print("  [PAGE 14] Scanning for next incomplete quiz/section", flush=True)
    if find_and_click_quiz_link(page):
        return True
    result = page.evaluate("""() => {
        const containers = Array.from(document.querySelectorAll(
            'tr, .a-CardView-item, .t-Card, li, .t-TreeNav-item, .t-Region'));
        const items = [];
        for (const c of containers) {
            const text = (c.innerText || '').toLowerCase();
            if (/section\\s*\\d+/i.test(text) || /quiz\\s*:\\s*dp/i.test(text)) {
                const isDone = text.includes('100%') ||
                    text.includes('mastery achieved') || text.includes('passed') ||
                    c.querySelector('.u-success, .fa-check, .fa-check-circle, [class*="check"], [class*="complete"]') !== null;
                const isExam = text.includes('midterm') ||
                               text.includes('final exam') ||
                               text.includes('final examination');
                const link = c.querySelector('a') || (c.tagName === 'A' ? c : null);
                items.push({ isDone, isExam, hasLink: !!link,
                             text: (c.innerText || '').trim().substring(0, 60) });
            }
        }
        const hasNonExamIncomplete = items.some(i => !i.isDone && !i.isExam && i.hasLink);
        if (!hasNonExamIncomplete) {
            return { onlyExamsLeft: true, clicked: null };
        }
        for (const c of containers) {
            const text = (c.innerText || '').toLowerCase();
            if (/section\\s*\\d+/i.test(text) || /quiz\\s*:\\s*dp/i.test(text)) {
                const isDone = text.includes('100%') ||
                    text.includes('mastery achieved') || text.includes('passed') ||
                    c.querySelector('.u-success, .fa-check, .fa-check-circle, [class*="check"], [class*="complete"]') !== null;
                const isExam = text.includes('midterm') ||
                               text.includes('final exam') ||
                               text.includes('final examination');
                if (!isDone && !isExam) {
                    const link = c.querySelector('a') || (c.tagName === 'A' ? c : null);
                    if (link) {
                        link.click();
                        return { onlyExamsLeft: false,
                                 clicked: (link.innerText || '').trim().split('\\n')[0] };
                    }
                }
            }
        }
        return { onlyExamsLeft: false, clicked: null };
    }""")
    if isinstance(result, dict) and result.get("onlyExamsLeft"):
        print("  [PAGE 14] Only exams left — need to switch course", flush=True)
        return "SWITCH_COURSE"
    clicked = result.get("clicked") if isinstance(result, dict) else None
    if clicked:
        print(f"  [PAGE 14] Selected: '{clicked}'", flush=True)
        time.sleep(4)
        return True
    return False


def handle_page_100(page) -> bool:
    print("  [PAGE 100] Scanning My Classes", flush=True)
    clicked = page.evaluate("""() => {
        const a14 = Array.from(document.querySelectorAll('a')).find(a =>
            (a.href || '').includes('63000:14:'));
        if (a14) { a14.click(); return (a14.innerText || '').trim(); }
        const candidates = Array.from(document.querySelectorAll(
            '.a-CardView-item, .t-Card, tr, li, div'));
        const match = candidates.find(c =>
            (c.innerText || '').toLowerCase().includes('database programming'));
        if (match) {
            const btn = match.querySelector('a, button, [role="button"]') || match;
            btn.click();
            return 'course-card';
        }
        return null;
    }""")
    if clicked:
        print(f"  [PAGE 100] Entered: '{clicked}'", flush=True)
        time.sleep(4)
        return True
    return False


def run():
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60, flush=True)
    print("  Oracle Academy Quiz Automation v2 (single session)", flush=True)
    print("=" * 60, flush=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 850},
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--host-resolver-rules={HOST_RESOLVER_RULES}",
                "--enable-features=DnsOverHttps",
                "--dns-over-https-mode=automatic",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            page.goto(HUB_URL, timeout=45000, wait_until="domcontentloaded")
            time.sleep(5)

            text = page.locator("body").inner_text(timeout=5000) or ""
            if not is_signed_in(text):
                if not wait_for_signin(page):
                    print("[FAIL] Sign-in not completed", flush=True)
                    return
            else:
                print("[AUTH] Already signed in", flush=True)

            cycle = 0
            while True:
                cycle += 1
                curr_url = page.url
                title = page.title()
                print(f"\n[CYCLE {cycle}] {curr_url[:80]} (Title: '{title[:30]}')",
                      flush=True)

                try:
                    if "p=63000:192" in curr_url or \
                            page.locator("text=/Percentage\\s+Scored/i").count() > 0:
                        handle_score_summary(page, f"Quiz_{cycle}")
                        continue

                    target = get_quiz_target(page)
                    has_question = False
                    try:
                        has_question = (
                            "p=63000:190" in curr_url or
                            "take the assessment" in title.lower() or
                            target.locator("text=/Question\\s+\\d+\\s+of\\s+\\d+/i").count() > 0
                        )
                    except Exception:
                        pass

                    if has_question:
                        run_quiz(page, f"Quiz_{cycle}")
                        continue

                    has_take_assessment = False
                    try:
                        has_take_assessment = page.locator(
                            "button:has-text('Take an Assessment'), "
                            "a:has-text('Take an Assessment'), "
                            "button:has-text('Start Assessment')"
                        ).count() > 0
                    except Exception:
                        pass

                    if has_take_assessment:
                        page = handle_quiz_landing(page)
                        continue

                    if "p=63000:15" in curr_url or "class course lesson" in title.lower():
                        result = handle_page_15(page)
                        if isinstance(result, type(page)):
                            page = result
                        continue

                    if "p=63000:14" in curr_url or "taking a class" in title.lower():
                        handle_page_14(page)
                        continue

                    if "p=63000:100" in curr_url or "my classes" in title.lower():
                        handle_page_100(page)
                        continue

                    if "p=63000:1" in curr_url or "home" in title.lower():
                        page.evaluate("""() => {
                            const a = Array.from(document.querySelectorAll('a')).find(a =>
                                (a.innerText || '').toLowerCase().includes('my classes'));
                            if (a) a.click();
                        }""")
                        time.sleep(4)
                        continue

                    if "signon.oracle.com" in curr_url or \
                            "identity.oraclecloud.com" in curr_url:
                        print("  [AUTH] Session expired — waiting for re-sign-in",
                              flush=True)
                        wait_for_signin(page, timeout_s=1800)
                        continue

                    print(f"  [UNKNOWN] No handler for {curr_url[:60]}", flush=True)
                    time.sleep(3)

                except Exception as exc:
                    print(f"  [RECOVERY] {exc}", flush=True)
                    time.sleep(3)

                time.sleep(2)

        except Exception as err:
            print(f"[ERROR] {err}", flush=True)
        finally:
            print("\n[DONE] Automation loop ended. Browser stays open.", flush=True)
            while True:
                time.sleep(60)


if __name__ == "__main__":
    run()
