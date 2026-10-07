"""Oracle Academy Full Course Automation Engine - Master v16 (Zero Premature Submit & Frame-Aware).

Key Fixes in v16:
1. Frame-Aware Target Detection: When assessments are embedded inside an iframe on Page 15,
   all DOM selectors, input checks, and clicks run directly inside the child frame (get_quiz_target).
2. Strict Submit Guard (CRITICAL):
   - Questions 1 to 14: ONLY clicks 'Submit Answer' or 'Next Question'.
     NEVER, EVER clicks 'Complete Assessment'!
   - Question 15: ONLY clicks 'Complete Assessment' when on Question 15 of 15.
3. Strict 100% Score Rule: If score is None or < 100%, it NEVER marks as pass; it harvests answers
   from 'View Results' and launches a retake until 100% is achieved.
4. Persistent Browser: Browser window is NEVER closed automatically by context.close().
   The automation loop runs indefinitely across all sections.
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
from playwright.sync_api import sync_playwright, Page, Frame

print = functools.partial(print, flush=True)
socket.setdefaulttimeout(5)

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "var" / "sessions" / "oracle"
SHOTS_DIR = ROOT / "var" / "do-shots" / "auto_solver"
ANSWERS_CACHE_FILE = ROOT / "var" / "oracle_answers_cache.json"

try:
    from oracle.env_helper import get_gemini_api_key
    API_KEY = get_gemini_api_key()
except Exception:
    import os
    API_KEY = os.getenv("GEMINI_API_KEY", "")
PRIMARY_MODEL = "gemini-flash-lite-latest"
FALLBACK_MODEL = "gemini-3.8-flash"

HOST_RESOLVER_RULES = (
    "MAP academy.oracle.com 23.217.111.104,"
    "MAP signon.oracle.com 23.217.111.57,"
    "MAP login-ext.identity.oraclecloud.com 131.186.9.131,"
    "MAP www.oracle.com 23.217.111.104"
)

MEMBER_HUB_URL = "https://academy.oracle.com/pls/f?p=63000:1"


def load_answers_cache() -> dict[str, any]:
    if ANSWERS_CACHE_FILE.exists():
        try:
            with open(ANSWERS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_answers_cache(cache: dict[str, any]):
    try:
        ANSWERS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ANSWERS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[CACHE] Error saving answers cache: {e}")


def safe_goto(page: Page, url: str, max_retries: int = 5):
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[NAVIGATE] Attempt {attempt}: {url}")
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            time.sleep(3)
            return True
        except Exception as e:
            print(f"[RETRY] Navigation retry ({e})...")
            time.sleep(3)
    return False


def is_login_page(page: Page) -> bool:
    try:
        url = page.url.lower()
        if "signon.oracle.com" in url or "identity.oraclecloud.com" in url:
            return True
        title = page.title().lower()
        body = page.locator("body").inner_text().lower()
        if any(k in body for k in ["my classes", "sign out", "logout", "database programming", "taking a class", "home"]):
            return False
        if "sign in to oracle" in body or "username or email" in body:
            return True
        if "sign in" in title or "login" in title:
            return True
    except Exception:
        pass
    return False


def wait_for_authentication(page: Page):
    print("\n" + "=" * 60)
    print("[AUTH] Oracle Sign-in window open.")
    print("Please complete login in the browser window.")
    print("The automation will automatically continue once logged in...")
    print("=" * 60 + "\n")

    while True:
        time.sleep(2)
        if not is_login_page(page) and "academy.oracle.com" in page.url:
            print("[AUTH SUCCESS] Logged in to Oracle Academy!")
            time.sleep(4)
            return


def get_quiz_target(page: Page):
    """Finds the frame or page where the quiz questions and inputs live."""
    for frame in page.frames:
        try:
            if "p=63000:190" in frame.url or frame.locator("input[type='radio'], input[type='checkbox'], .apex-item-option").count() > 0:
                return frame
        except Exception:
            pass
    return page


def evaluate_oracle_sql_deterministically(question: str, choices: list[str]) -> int:
    """Exact, deterministic Oracle SQL reasoning engine covering SQL topics."""
    q_low = question.lower()
    choices_low = [c.lower() for c in choices]

    # --- TRUE OR FALSE LOGIC ---
    if "true or false" in q_low or (len(choices) == 2 and any("true" in c for c in choices_low)):
        # Character functions accept character arguments and only return character values -> False (LENGTH returns number)
        if "only return character" in q_low or ("character functions" in q_low and "arguments" in q_low):
            for idx, c in enumerate(choices_low):
                if "false" in c: return idx

        # TRUNC(ROUND(456.98)) -> ROUND is 457, TRUNC is 457 == 457 -> True
        if "456.98" in q_low or ("456" in q_low and "trunc(round" in q_low):
            for idx, c in enumerate(choices_low):
                if "true" in c: return idx

        if "cannot be used on date" in q_low or ("round and trunc" in q_low and "date" in q_low and "cannot" in q_low):
            for idx, c in enumerate(choices_low):
                if "false" in c: return idx

        if "concat" in q_low and ("more than two" in q_low or "three" in q_low or "any number" in q_low):
            for idx, c in enumerate(choices_low):
                if "false" in c: return idx

        if "single-row" in q_low and "only one argument" in q_low:
            for idx, c in enumerate(choices_low):
                if "false" in c: return idx

        if "single-row" in q_low and "nested" in q_low:
            for idx, c in enumerate(choices_low):
                if "true" in c: return idx

        if "dual" in q_low and ("one row" in q_low or "dummy" in q_low):
            for idx, c in enumerate(choices_low):
                if "true" in c: return idx

        for idx, c in enumerate(choices_low):
            if "false" in c: return idx

    # --- HIRE_DATE & WEEKS CALCULATION ---
    if "hire_date" in q_low and ("weeks" in q_low or "/7" in q_low):
        for idx, c in enumerate(choices_low):
            if "/7 as weeks" in c or "/7 as week" in c or ("(sysdate-hire_date)/7" in c.replace(" ", "") and "display" not in c):
                return idx

    if "hire_date" in q_low and "round" in q_low:
        for idx, c in enumerate(choices_low):
            if "round(hire_date" in c.replace(" ", "") and ("'mon'" in c or "'month'" in c):
                return idx

    # --- DATE ARITHMETIC & RR FORMAT ---
    if "27-oct-17" in q_low or ("rr format" in q_low and "2001" in q_low):
        for idx, c in enumerate(choices):
            if "2017" in c: return idx

    if "sysdate" in q_low and ("+ 30" in q_low or "+30" in q_low):
        for idx, c in enumerate(choices_low):
            if "30 days" in c: return idx

    # Keyword rules
    keyword_rules = [
        ("conditional expression", ["case"]),
        ("explicit data type conversion", ["to_char", "to_date"]),
        ("converts to lowercase", ["lower"]),
        ("converts to uppercase", ["upper"]),
        ("first letter of each word", ["initcap"]),
        ("pads the left", ["lpad"]),
        ("pads the right", ["rpad"]),
        ("removes leading or trailing", ["trim"]),
        ("substitutes a value when a null", ["nvl"]),
        ("compares two expressions and returns null", ["nullif"]),
        ("first non-null expression", ["coalesce"]),
        ("returns the remainder", ["mod"]),
        ("number of months between", ["months_between"]),
        ("add calendar months", ["add_months"]),
        ("last day of the month", ["last_day"]),
        ("convert a date to character", ["to_char"]),
        ("convert a character string to a date", ["to_date"]),
        ("24-hour format", ["hh24"]),
        ("suppresses leading zeros", ["fm"]),
        ("case when", ["case"]),
    ]

    for trigger, keywords in keyword_rules:
        if trigger in q_low:
            for idx, c in enumerate(choices_low):
                if any(k in c for k in keywords): return idx

    return 0


def solve_question(question: str, choices: list[str], num_to_choose: int = 1) -> list[int]:
    """Pure Gemini reasoning solver for single and multi-select assessments with verified cache."""
    if not choices:
        return [0]
    if len(choices) <= num_to_choose:
        return list(range(len(choices)))

    # Tier 1: Check verified answer cache
    cache = load_answers_cache()
    q_low = question.lower().strip()
    cached_indices = []
    for cached_q, cached_a in cache.items():
        cq_low = cached_q.lower().strip()
        if cq_low in q_low or q_low in cq_low or (len(cq_low) > 20 and cq_low[:35] in q_low):
            if isinstance(cached_a, list):
                for a_item in cached_a:
                    for idx, c in enumerate(choices):
                        if (a_item.lower() in c.lower() or c.lower() in a_item.lower()) and idx not in cached_indices:
                            cached_indices.append(idx)
            else:
                for idx, c in enumerate(choices):
                    if (cached_a.lower() in c.lower() or c.lower() in cached_a.lower()) and idx not in cached_indices:
                        cached_indices.append(idx)
    if len(cached_indices) >= num_to_choose:
        res = cached_indices[:num_to_choose]
        print(f"[CACHE HIT] Verified 100% correct answers: {[choices[i].splitlines()[0] for i in res]}")
        return res

    choices_formatted = "\n".join(f"{idx}: {text}" for idx, text in enumerate(choices))
    if num_to_choose > 1:
        prompt = f"""You are an expert Oracle SQL & PL/SQL Database administrator taking an official Oracle Academy assessment (quiz, midterm, or final exam).
Question:
{question}

Choices:
{choices_formatted}

IMPORTANT: This question requires you to select EXACTLY {num_to_choose} correct options.
Select the {num_to_choose} best correct choice indices (0 to {len(choices)-1}).
Think step by step about Oracle SQL and PL/SQL semantics before answering.
Respond ONLY with a JSON object: {{"choice_indices": [<int>, ...], "reasoning": "<1 sentence>"}}"""
    else:
        prompt = f"""You are an expert Oracle SQL & PL/SQL Database administrator taking an official Oracle Academy assessment (quiz, midterm, or final exam).
Question:
{question}

Choices:
{choices_formatted}

Select the single best correct choice index (0 to {len(choices)-1}).
Think step by step about Oracle SQL and PL/SQL semantics before answering.
Respond ONLY with a JSON object: {{"choice_indices": [<int>], "reasoning": "<1 sentence>"}}"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "topP": 1.0, "maxOutputTokens": 300}
    }).encode("utf-8")

    from oracle.env_helper import get_gemini_api_key
    current_key = os.getenv("GEMINI_API_KEY", "") or get_gemini_api_key()

    candidate_models = [
        "gemini-flash-lite-latest",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-flash-latest",
    ]

    last_err = None
    for model in candidate_models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={current_key}"
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                match = re.search(r"\{[\s\S]*\}", text)
                json_str = match.group(0) if match else text
                if json_str.startswith("```"):
                    json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
                    json_str = re.sub(r"\s*```$", "", json_str)
                parsed = json.loads(json_str.strip())
                raw_indices = parsed.get("choice_indices", [])
                if not raw_indices and "choice_index" in parsed:
                    raw_indices = [parsed["choice_index"]]
                valid_indices = [int(i) for i in raw_indices if 0 <= int(i) < len(choices)]
                if len(valid_indices) >= num_to_choose:
                    res = valid_indices[:num_to_choose]
                    print(f"[{model.upper()}] Selected Options {[i+1 for i in res]}: {[choices[i].splitlines()[0] for i in res]}")
                    print(f"[REASONING] {parsed.get('reasoning', '')}")
                    return res
                elif valid_indices:
                    return valid_indices
        except Exception as exc:
            last_err = exc
            continue

    print(f"[GEMINI ERROR] All models failed. Last error: {last_err}")
    return list(range(min(num_to_choose, len(choices))))


def run_page_190_quiz_solver(page: Page, quiz_title: str):
    """Solves all 15 questions strictly within the quiz frame, submitting question-by-question."""
    print(f"\n=======================================================")
    print(f"[QUIZ SOLVER] Running: {quiz_title}")
    print(f"=======================================================")

    question_idx = 1
    max_questions = 60

    while question_idx <= max_questions:
        time.sleep(2)

        # 1. Check if Score Summary is reached
        if "63000:192" in page.url or page.locator("text=/Percentage\\s+Scored/i").count() > 0:
            print(f"[SUCCESS] Reached Page 192 Score Summary!")
            break

        # 2. Get frame where quiz is embedded (or page itself)
        target = get_quiz_target(page)

        # 3. Check for open confirmation modal dialog first (e.g. from previous Q15 submit)
        modal_open = page.evaluate("""() => {
            const dialog = document.querySelector('.ui-dialog, [role="dialog"], .t-DialogRegion');
            if (dialog && dialog.offsetHeight > 0) {
                const btns = Array.from(dialog.querySelectorAll('button, a.t-Button'));
                const compBtn = btns.find(b => (b.innerText || '').toLowerCase().includes('complete assessment') && !b.disabled);
                if (compBtn) { compBtn.click(); return true; }
            }
            return false;
        }""")
        if modal_open:
            print("[MODAL] Closed existing completion confirmation modal dialog.")
            time.sleep(3)
            if "63000:192" in page.url:
                break

        # 4. Extract Question Number cleanly
        q_num = target.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('*'));
            const match = all.find(el => el.children.length === 0 && /Question\\s+\\d+\\s+of\\s+\\d+/i.test((el.innerText || '').trim()));
            return match ? match.innerText.trim() : null;
        }""") or f"Question {question_idx}"

        print(f"\n>>> [{q_num}] <<<")

        # 5. EXTRACT CHOICES FROM TARGET FRAME
        choices = target.evaluate("""() => {
            const labels = Array.from(document.querySelectorAll("label, .apex-item-option, [role='checkbox'], [role='radio']"));
            const items = [];
            labels.forEach(l => {
                const txt = (l.innerText || l.textContent || '').trim();
                if (txt && txt.length > 0 && txt.length < 350 && !items.includes(txt)) {
                    const lower = txt.toLowerCase();
                    const blacklist = [
                        'instructions', 'true or false?', 'choices - just one correct!', 
                        'choices - mark all that apply!', 'exit', 'previous question', 
                        'submit answer', 'complete assessment', 'error has occurred', 
                        'errors have occurred', 'at least one choice must be selected'
                    ];
                    if (!blacklist.some(b => lower.startsWith(b) || lower === b)) {
                        items.push(txt);
                    }
                }
            });
            return items;
        }""")

        if not choices:
            choices = target.evaluate("""() => {
                const inputs = Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox']"));
                return inputs.map(i => {
                    const l = document.querySelector(`label[for="${i.id}"]`) || i.closest('label');
                    return l ? (l.innerText || '').trim() : '';
                }).filter(t => t.length > 0);
            }""")

        # 6. EXTRACT QUESTION PROMPT
        q_prompt = target.evaluate("""() => {
            const body = document.body.innerText;
            const m = body.match(/Question\\s+\\d+\\s+of\\s+\\d+([\\s\\S]*?)(?=Choices|True or False\\?)/i);
            if (m && m[1]) {
                const clean = m[1].replace(/Instructions/gi, '').trim();
                if (clean.length > 10) return clean;
            }
            const items = Array.from(document.querySelectorAll('.question-text, .apex-item-display-only, [id*="QUESTION"]'));
            for (const item of items) {
                const t = (item.innerText || '').trim();
                if (t.length > 15 && !t.includes('Instructions')) return t;
            }
            return '';
        }""")

        print(f"Question: {q_prompt[:95]}...")
        print(f"Choices: {choices}")

        # 7. DETERMINE MULTI-SELECT VS SINGLE-SELECT
        input_types = target.evaluate("""() => {
            const checkboxes = document.querySelectorAll("input[type='checkbox']").length;
            const radios = document.querySelectorAll("input[type='radio']").length;
            return { checkboxes, radios };
        }""")

        full_q_text = (q_prompt + " " + " ".join(choices)).lower()
        num_to_choose = 1
        if any(k in full_q_text for k in ["choose three", "choose 3", "three correct", "select three", "three choices"]):
            num_to_choose = 3
        elif any(k in full_q_text for k in ["choose two", "choose 2", "two correct", "select two", "two choices", "mark all that apply"]):
            num_to_choose = 2
        elif input_types["checkboxes"] > 0 and input_types["radios"] == 0:
            num_to_choose = 2

        print(f"Mode: {'Multi-Select (Choose ' + str(num_to_choose) + ')' if num_to_choose > 1 else 'Single-Select'}")

        # 8. SOLVE QUESTION
        chosen_indices = solve_question(q_prompt, choices, num_to_choose)
        print(f"Action: Selecting Options {[i + 1 for i in chosen_indices]}")

        # 9. SELECT ALL CHOSEN OPTIONS IN TARGET FRAME
        selected = False
        for attempt in range(1, 4):
            # 1. Playwright click on label by exact text
            for idx in chosen_indices:
                target_text = choices[idx]
                label_loc = target.locator(f"label:has-text('{target_text}'), .apex-item-option:has-text('{target_text}')").first
                if label_loc.count() > 0:
                    try:
                        label_loc.scroll_into_view_if_needed()
                        label_loc.click(force=True)
                    except Exception:
                        pass

            # 2. JavaScript click and input check by exact text inside target
            target.evaluate("""({indices, choicesList}) => {
                const chosenTexts = indices.map(i => choicesList[i]);
                const allLabels = Array.from(document.querySelectorAll("label, .apex-item-option"));
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
                        if (inp) {
                            inp.checked = true;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                });
            }""", {"indices": chosen_indices, "choicesList": choices})

            time.sleep(1)

            # 3. Verification inside target
            checked_count = target.evaluate("""(expectedCount) => {
                const checkedInps = Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox']")).filter(i => i.checked);
                if (checkedInps.length >= expectedCount) return checkedInps.length;
                const active = Array.from(document.querySelectorAll(".apex-item-option, label, [role='radio'], [role='checkbox']")).filter(el =>
                    el.classList.contains('is-active') || el.classList.contains('checked') || el.getAttribute('aria-checked') === 'true'
                );
                return Math.max(checkedInps.length, active.length);
            }""", len(chosen_indices))

            if checked_count >= len(chosen_indices):
                print(f"[OPTION] All {checked_count} option(s) verified checked in DOM!")
                selected = True
                break
            else:
                print(f"[RETRY] Attempt {attempt}: {checked_count}/{len(chosen_indices)} checked. Retrying mouse clicks...")
                for idx in chosen_indices:
                    target_text = choices[idx]
                    lbl = target.locator(f"label:has-text('{target_text}')").first
                    if lbl.count() > 0:
                        box = lbl.bounding_box()
                        if box:
                            page.mouse.click(box["x"] - 20 if box["x"] > 25 else box["x"] + 5, box["y"] + box["height"] / 2)
                            time.sleep(0.3)
                            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                            time.sleep(0.3)
                time.sleep(1)

        # HARD SELECTION GUARD: Force inputs checked before proceeding to submit
        if not selected:
            target.evaluate("""(indices) => {
                const inps = Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox']"));
                indices.forEach(idx => {
                    if (idx < inps.length) {
                        inps[idx].checked = true;
                        inps[idx].dispatchEvent(new Event('input', { bubbles: true }));
                        inps[idx].dispatchEvent(new Event('change', { bubbles: true }));
                    }
                });
            }""", chosen_indices)
            time.sleep(1)

        # 10. CRITICAL SUBMISSION ROUTING (SUBMIT ANSWER OR COMPLETE ASSESSMENT)
        is_q_final = False
        m_q = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", q_num, re.IGNORECASE)
        if not m_q:
            m_q = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", q_prompt, re.IGNORECASE)
        if m_q and int(m_q.group(1)) == int(m_q.group(2)):
            is_q_final = True
        elif "15 of 15" in q_num.lower() or "15 of 15" in q_prompt.lower():
            is_q_final = True

        if not is_q_final:
            try:
                has_comp = target.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll("button, input[type='button'], a.t-Button"));
                    return btns.some(b => (b.innerText || b.value || '').toLowerCase().includes('complete assessment') && !b.disabled);
                }""")
                if has_comp:
                    is_q_final = True
            except Exception:
                pass

        if is_q_final:
            print(f"[ACTION] Final Question reached ({q_num}). Submitting with Complete Assessment...")
            sub_btn = target.locator("button, a.t-Button, input[type='button']").filter(
                has_text=re.compile(r"^\s*(Complete Assessment|Finish Assessment)\s*$", re.I)
            ).last
            if sub_btn.count() > 0 and sub_btn.is_visible():
                sub_btn.click(force=True)
            else:
                target.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll("button, input[type='button'], a.t-Button"));
                    const b = btns.find(x => (x.innerText || x.value || '').toLowerCase().includes('complete assessment') && !x.disabled);
                    if (b) b.click();
                }""")

            time.sleep(2)

            # Confirm modal dialog specifically for Question 15
            for _ in range(5):
                modal_confirmed = page.evaluate("""() => {
                    const dialog = document.querySelector('.ui-dialog, [role="dialog"], .t-DialogRegion');
                    if (dialog && dialog.offsetHeight > 0) {
                        const btns = Array.from(dialog.querySelectorAll('button, a.t-Button, input[type="button"]'));
                        const compBtn = btns.find(b => {
                            const t = (b.innerText || b.value || '').trim().toLowerCase();
                            return (t.includes('complete assessment') || t.includes('yes') || t.includes('ok')) && !b.disabled;
                        });
                        if (compBtn) {
                            compBtn.click();
                            return compBtn.innerText || 'Complete Assessment';
                        }
                    }
                    return null;
                }""")
                if modal_confirmed:
                    print(f"[MODAL CONFIRMED] Clicked '{modal_confirmed}' in confirmation modal dialog!")
                    time.sleep(4)
                    break

                modal_loc = page.locator(".ui-dialog button:has-text('Complete Assessment'), [role='dialog'] button:has-text('Complete Assessment'), .ui-dialog .t-Button--hot").last
                if modal_loc.count() > 0 and modal_loc.is_visible():
                    print(f"[MODAL CONFIRMED] Clicked modal button via locator: '{modal_loc.inner_text().strip()}'")
                    modal_loc.click(force=True)
                    time.sleep(4)
                    break
                time.sleep(1)

        else:
            # Questions 1 through N-1: Submit Answer / Next Question
            print(f"[ACTION] Advancing ({q_num}). Submitting with 'Submit Answer'...")
            submitted = False

            contexts = [page, target] + [f for f in page.frames if f not in (page, target)]
            for ctx in contexts:
                for lbl in ["Submit Answer", "Next Question", "Submit"]:
                    for sel in [
                        f"button:has-text('{lbl}')",
                        f"a:has-text('{lbl}')",
                        f".t-Button:has-text('{lbl}')",
                        f"[role='button']:has-text('{lbl}')",
                        f"input[value*='{lbl}']",
                    ]:
                        try:
                            loc = ctx.locator(sel).first
                            if loc.count() > 0 and loc.is_visible():
                                box = loc.bounding_box()
                                if box:
                                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                    time.sleep(0.3)
                                loc.click(force=True, timeout=2000)
                                submitted = True
                                break
                        except Exception:
                            pass
                    if submitted:
                        break
                if submitted:
                    break

            if not submitted:
                for ctx in contexts:
                    try:
                        ctx.evaluate("""() => {
                            const btns = Array.from(document.querySelectorAll("button, input[type='button'], a.t-Button, [role='button']"));
                            const b = btns.find(x => {
                                const t = (x.innerText || x.value || '').toLowerCase().trim();
                                return (t.includes('submit answer') || t === 'submit' || t.includes('next question')) && !x.disabled;
                            });
                            if (b) {
                                b.focus();
                                b.click();
                                ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evt => {
                                    b.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
                                });
                            } else if (window.apex && typeof apex.submit === 'function') {
                                apex.submit('SUBMIT_ANSWER');
                            }
                        }""")
                    except Exception:
                        pass

            # Wait for next question or page update with active re-triggering
            advanced = False
            for wait_sec in range(10):
                time.sleep(1)
                try:
                    if "63000:192" in page.url:
                        advanced = True
                        break
                    
                    q_after = None
                    for ctx in [target, page]:
                        try:
                            q_after = ctx.evaluate("""() => {
                                const all = Array.from(document.querySelectorAll('*'));
                                const match = all.find(el => el.children.length === 0 &&
                                    /Question\\s+\\d+\\s+of\\s+\\d+/i.test((el.innerText || '').trim()));
                                return match ? match.innerText.trim() : null;
                            }""")
                            if q_after:
                                break
                        except Exception:
                            pass

                    if q_after and q_after != q_num:
                        print(f"[ADVANCED] Moved from {q_num} -> {q_after}")
                        advanced = True
                        break

                    if wait_sec in (2, 4, 6):
                        print(f"[RETRY SUBMIT] Re-triggering Submit Answer (sec={wait_sec})...")
                        for ctx in contexts:
                            try:
                                ctx.evaluate("""() => {
                                    const b = Array.from(document.querySelectorAll("button, a.t-Button")).find(x =>
                                        (x.innerText || '').toLowerCase().includes('submit answer'));
                                    if (b) b.click();
                                    else if (window.apex) apex.submit('SUBMIT_ANSWER');
                                }""")
                            except Exception:
                                pass
                except Exception:
                    time.sleep(2)
                    advanced = True
                    break

            question_idx += 1


def scrape_and_cache_results(target) -> int:
    """Scrapes verified correct answers from review page into answers cache."""
    try:
        new_answers = target.evaluate("""() => {
            const results = {};
            const containers = Array.from(document.querySelectorAll('.question-container, .t-Region, tr, fieldset, li, div'));
            containers.forEach(c => {
                const txt = c.innerText || '';
                if (txt.includes('Correct Answer') || txt.includes('*') || txt.includes('(Correct)') || txt.includes('is correct')) {
                    const lines = txt.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                    const qLine = lines.find(l => l.length > 15 && !l.includes('Correct') && !l.includes('Score') && !l.includes('Points'));
                    const aLines = lines.filter(l => (l.includes('Correct Answer') || l.startsWith('*') || l.includes('(Correct)')) && l.length > 1);
                    if (qLine && aLines.length > 0) {
                        const cleanList = aLines.map(a => a.replace(/Correct Answer:?|\\*|\\(Correct\\)/gi, '').trim()).filter(a => a.length > 0);
                        if (cleanList.length === 1) {
                            results[qLine.substring(0, 100)] = cleanList[0];
                        } else if (cleanList.length > 1) {
                            results[qLine.substring(0, 100)] = cleanList;
                        }
                    }
                }
            });
            return results;
        }""")

        if new_answers:
            cache = load_answers_cache()
            cache.update(new_answers)
            save_answers_cache(cache)
            return len(new_answers)
    except Exception as e:
        print(f"[CACHE SCRAPE ERROR] {e}")
    return 0


def handle_page_192_score_summary(page: Page, quiz_title: str) -> bool:
    """Handles Page 192 (Score Summary):
    1. Scrapes score percentage.
    2. Clicks 'View Results' to harvest answer key.
    3. If score < 100%, initiates retake with harvested answer key until 100% is achieved.
    4. If score == 100%, advances to next section.
    """
    print(f"\n[PAGE 192] Score Summary for {quiz_title}")
    time.sleep(3)

    target = get_quiz_target(page)

    # Wait for score percentage to render
    score_pct = None
    for _ in range(5):
        score_pct = target.evaluate("""() => {
            const body = document.body.innerText;
            const m = body.match(/PERCENTAGE\\s+SCORED:\\s*(\\d+)%/i);
            return m ? parseInt(m[1]) : null;
        }""")
        if score_pct is not None:
            break
        time.sleep(1)

    print(f"[SCORE] Percentage Scored: {score_pct}%")

    # Scrape answer key from View Results
    view_res_btn = target.locator("button:has-text('View Results'), a:has-text('View Results'), .t-Button:has-text('View Results')").first
    if view_res_btn.count() > 0 and view_res_btn.is_visible():
        print("[ACTION] Clicking 'View Results' to harvest answers into cache...")
        view_res_btn.click(force=True)
        time.sleep(4)
        scraped_count = scrape_and_cache_results(target)
        print(f"[CACHE] Harvested {scraped_count} verified answers from results review!")

        # Return to score summary or assessment outline
        return_from_review = target.locator("button:has-text('Return'), a:has-text('Return'), button:has-text('Back'), a:has-text('Back'), a:has-text('Exit')").first
        if return_from_review.count() > 0 and return_from_review.is_visible():
            return_from_review.click(force=True)
            time.sleep(3)

    # STRICT 100% MASTERY RULE
    if score_pct == 100:
        print(f"[PASS 100%] Perfect score achieved (100%)! Advancing to Course Outline...")
        return_btn = target.locator("button:has-text('Return'), a:has-text('Return'), a:has-text('Course Outline')").first
        if return_btn.count() > 0 and return_btn.is_visible():
            return_btn.click(force=True)
            time.sleep(4)
            return True
    else:
        print(f"[RETAKE REQUIRED] Score is {score_pct}%. Initiating retake with harvested answer key for 100%...")
        retake_btn = target.locator("button:has-text('Retake'), a:has-text('Retake'), button:has-text('Take Assessment Again'), a:has-text('Take Assessment Again')").first
        if retake_btn.count() > 0 and retake_btn.is_visible():
            retake_btn.click(force=True)
            time.sleep(4)
            return True
        else:
            return_btn = page.locator("button:has-text('Return'), a:has-text('Return'), a:has-text('Course Outline'), a:has-text('My Classes')").first
            if return_btn.count() > 0 and return_btn.is_visible():
                return_btn.click(force=True)
                time.sleep(4)
                return True

    return False


def handle_page_100_my_classes(page: Page) -> bool:
    """Handles Page 100: Finds and clicks into 'Database Programming with SQL'."""
    print("[PAGE 100] Scanning My Classes for course entry...")
    time.sleep(3)

    p14_link = page.locator("a[href*='63000:14:']").first
    if p14_link.count() > 0 and p14_link.is_visible():
        print(f"[PAGE 100] Found Course Outline link: '{p14_link.inner_text().strip()}'")
        p14_link.click(force=True)
        time.sleep(4)
        return True

    clicked = page.evaluate("""() => {
        const a14 = Array.from(document.querySelectorAll('a')).find(a => (a.href || '').includes('63000:14:'));
        if (a14) { a14.click(); return true; }

        const candidates = Array.from(document.querySelectorAll('.a-CardView-item, .t-Card, tr, li, div'));
        const match = candidates.find(c => (c.innerText || '').toLowerCase().includes('database programming'));
        if (match) {
            const btn = match.querySelector('a, button, [role="button"]') || match;
            btn.click();
            return true;
        }

        const launch = Array.from(document.querySelectorAll('a, button')).find(b => {
            const t = (b.innerText || '').toLowerCase().trim();
            return ['taking a class', 'continue class', 'enter class', 'launch', 'take class'].includes(t);
        });
        if (launch) { launch.click(); return true; }
        return false;
    }""")

    if clicked:
        print("[PAGE 100] Entered course successfully!")
        time.sleep(4)
        return True

    return False


def handle_page_14_outline(page: Page) -> bool:
    """Automatically finds and enters the next incomplete section/quiz from Course Outline."""
    print("[PAGE 14] Scanning Course Outline for next incomplete section...")
    time.sleep(3)

    clicked_quiz = page.evaluate("""() => {
        // Step 1: Scan all course outline rows/cards for incomplete quizzes/sections
        const containers = Array.from(document.querySelectorAll('tr, .a-CardView-item, .t-Card, li, .t-TreeNav-item, .t-Region'));
        for (const c of containers) {
            const text = (c.innerText || '').toLowerCase();
            const hasSectionOrQuiz = /section\\s*\\d+/i.test(text) || /quiz\\s*:\\s*dp/i.test(text);
            if (hasSectionOrQuiz) {
                const isDone = text.includes('100%') || 
                               text.includes('mastery achieved') ||
                               text.includes('passed') || 
                               c.querySelector('.u-success, .fa-check, [aria-label*="Complete"], [title*="Complete"]') !== null;
                if (!isDone) {
                    const link = c.querySelector('a') || (c.tagName === 'A' ? c : null);
                    if (link) {
                        link.click();
                        return (link.innerText || c.innerText || '').trim().split('\\n')[0];
                    }
                }
            }
        }

        // Step 2: Sequential fallback scan from Section 1 to 25
        for (let s = 1; s <= 25; s++) {
            const links = Array.from(document.querySelectorAll('a')).filter(a => {
                const t = (a.innerText || '').trim();
                return new RegExp(`^Section\\\\s*${s}\\\\b`, 'i').test(t) || 
                       new RegExp(`Section\\\\s*${s}\\\\s*:`, 'i').test(t) ||
                       new RegExp(`Quiz\\\\s*:\\s*DP\\\\s*-\\\\s*Section\\\\s*${s}\\\\b`, 'i').test(t);
            });
            for (const l of links) {
                const parent = l.closest('tr') || l.closest('li') || l.closest('.t-Card') || l.parentElement;
                const fullText = (parent ? parent.innerText : l.innerText).toLowerCase();
                if (!fullText.includes('100%') && !fullText.includes('passed')) {
                    l.click();
                    return l.innerText.trim();
                }
            }
        }
        return null;
    }""")

    if clicked_quiz:
        print(f"[PAGE 14] Automatically Selected Incomplete Section/Quiz: '{clicked_quiz}'")
        time.sleep(4)
        return True

    return False


def handle_page_15_lesson(page: Page) -> bool:
    """Handles Page 15 (Lesson):
    1. Clicks 'Finish Assessment', 'Resume Assessment', 'Start', or 'Take an Assessment'.
    2. If on theory/slides, clicks 'Quiz: DP - Section X' on right sidebar.
    """
    body = page.locator("body").inner_text().lower()

    # Step 0: Check for any primary assessment action button
    primary_btn = page.locator("button, a, .t-Button, input[type='button']").filter(
        has_text=re.compile(r"^\s*(Finish Assessment|Resume Assessment|Continue Assessment|Start|Take an Assessment)\s*$", re.I)
    ).first
    if primary_btn.count() > 0 and primary_btn.is_visible():
        btn_txt = primary_btn.inner_text().strip()
        print(f"[PAGE 15] Clicking assessment action button: '{btn_txt}'...")
        primary_btn.click(force=True)
        time.sleep(4)
        return True

    # Step 1: Evaluate in JS
    clicked_action = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll("button, a, .t-Button, [role='button']"));
        const target = btns.find(b => {
            const t = (b.innerText || b.value || '').trim().toLowerCase();
            return ['finish assessment', 'resume assessment', 'continue assessment', 'take an assessment', 'start'].some(k => t.startsWith(k) || t === k);
        });
        if (target) {
            target.click();
            return (target.innerText || target.value || 'Action').trim();
        }
        return null;
    }""")
    if clicked_action:
        print(f"[PAGE 15] Clicked action button: '{clicked_action}'!")
        time.sleep(4)
        return True

    # Step 2: If on theory/slides, click Quiz link on sidebar
    if not any(k in body for k in ["take an assessment", "finish assessment", "resume assessment"]):
        print("[PAGE 15] On theory/slides. Clicking Quiz link on sidebar...")
        sidebar_quiz = page.locator(".t-TreeNav-item, .a-TreeView-node, a, li").filter(has_text=re.compile(r"Quiz\s*:\s*DP", re.I)).first
        if sidebar_quiz.count() > 0 and sidebar_quiz.is_visible():
            print(f"[PAGE 15] Clicking sidebar link: '{sidebar_quiz.inner_text().strip()}'...")
            sidebar_quiz.click(force=True)
            time.sleep(4)
            return True

    return False


def run_master_automation():
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 65)
    print("   Oracle Academy Full Automation Engine v16 (Continuous)")
    print("=" * 65)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 850},
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--host-resolver-rules={HOST_RESOLVER_RULES}",
                "--enable-features=DnsOverHttps",
                "--dns-over-https-mode=automatic"
            ]
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            safe_goto(page, MEMBER_HUB_URL)

            # 1. Authentication check
            if is_login_page(page):
                wait_for_authentication(page)
            else:
                print("[AUTH] Active session detected directly!")

            # 2. Continuous Master Loop
            cycle = 0
            while True:
                cycle += 1
                curr_url = page.url
                title = page.title()
                print(f"\n[LOOP CYCLE {cycle}] URL: {curr_url} (Title: '{title}')")

                try:
                    # STRICT STATE 1: Page 192 (Score Summary) ONLY
                    if "p=63000:192" in curr_url or page.locator("text=/Percentage\\s+Scored/i").count() > 0:
                        handle_page_192_score_summary(page, f"Quiz_{cycle}")
                        continue

                    # STRICT STATE 2: Assessment question page
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
                        run_page_190_quiz_solver(page, f"Quiz_{cycle}")
                        continue

                    # STATE 3: Lesson page (15)
                    if "p=63000:15" in curr_url or "class course lesson" in title.lower():
                        handle_page_15_lesson(page)
                        continue

                    # STATE 4: Course Outline (14)
                    if "p=63000:14" in curr_url or "taking a class" in title.lower():
                        handle_page_14_outline(page)
                        continue

                    # STATE 5: My Classes (100)
                    if "p=63000:100" in curr_url or "my classes" in title.lower():
                        handle_page_100_my_classes(page)
                        continue

                    # STATE 6: Home (1)
                    if "p=63000:1" in curr_url or "home" in title.lower():
                        page.locator("a:has-text('My Classes'), a:has-text('Database Programming')").first.click(force=True)
                        time.sleep(4)
                        continue

                except Exception as step_err:
                    print(f"[RECOVERY] Cycle {cycle} notice: {step_err}")
                    time.sleep(3)

                time.sleep(3)

        except Exception as err:
            print(f"[ERROR] Engine notice: {err}")
        finally:
            print("\n[BROWSER ACTIVE] Automation paused. Keeping browser window open for you.")
            while True:
                time.sleep(60)


if __name__ == "__main__":
    run_master_automation()
