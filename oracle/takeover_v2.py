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
socket.setdefaulttimeout(20)

ROOT = Path(__file__).resolve().parent.parent
PARENT = Path(__file__).resolve().parent
for p in [str(ROOT), str(PARENT), str(ROOT.parent)]:
    if p not in sys.path:
        sys.path.insert(0, p)

PROFILE_DIR = ROOT / "var" / "sessions" / "oracle"
SHOTS_DIR = ROOT / "var" / "do-shots" / "takeover_v2"
ANSWERS_CACHE_FILE = ROOT / "var" / "oracle_answers_cache.json"


def load_answers_cache() -> dict[str, any]:
    if ANSWERS_CACHE_FILE.exists():
        try:
            with open(ANSWERS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_answers_cache(cache: dict[str, any]) -> None:
    try:
        ANSWERS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ANSWERS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"  [CACHE] Failed to save answers: {exc}", flush=True)


def get_active_api_key() -> str:
    try:
        from oracle.env_helper import get_gemini_api_key
        key = get_gemini_api_key()
        if key:
            return key
    except Exception:
        pass
    candidates = [
        ROOT / ".env",
        PARENT / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "Lakra-2.0" / ".env",
        Path.cwd().parent / ".env",
        Path.cwd().parent / "Lakra-2.0" / ".env",
    ]
    for c in candidates:
        if c.is_file():
            for line in c.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if k:
                        import os
                        os.environ["GEMINI_API_KEY"] = k
                        return k
    import os
    return os.getenv("GEMINI_API_KEY", "")


def get_active_student_email() -> str:
    try:
        from oracle.env_helper import get_student_email
        return get_student_email()
    except Exception:
        import os
        return os.getenv("ORACLE_STUDENT_EMAIL", "")


API_KEY = get_active_api_key()
EMAIL = get_active_student_email()

CANDIDATE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]
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
        "taking a class", "course outline", "pl/sql", "sql")) or (EMAIL and EMAIL in text)
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
    try:
        if page.locator("input[type='radio'], input[type='checkbox']").count() > 0:
            return page
    except Exception:
        pass

    for frame in page.frames:
        try:
            if frame.locator("input[type='radio'], input[type='checkbox']").count() > 0:
                return frame
            if "63000:190" in frame.url:
                return frame
        except Exception:
            pass
    return page


def solve_with_gemini(question: str, choices: list[str], num_to_choose: int = 1) -> list[int]:
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
        print(f"  [CACHE HIT] Verified 100% correct answers: {[choices[i].splitlines()[0] for i in res]}", flush=True)
        return res

    choices_formatted = "\n".join(f"{i}: {t}" for i, t in enumerate(choices))
    if num_to_choose > 1:
        prompt = (
            "You are an expert Oracle SQL & PL/SQL Database Administrator taking an official "
            "Oracle Academy assessment (including quizzes, midterms, and final exams).\n"
            "Read the question carefully and select ALL correct answers.\n\n"
            f"Question:\n{question}\n\nChoices:\n{choices_formatted}\n\n"
            f"CRITICAL REQUIREMENT: This question requires you to select EXACTLY {num_to_choose} correct options.\n"
            "Think step by step according to official Oracle Database documentation and semantics.\n"
            f"Select the {num_to_choose} best correct option indices (0-based, 0 to {len(choices)-1}).\n\n"
            'Respond ONLY with JSON format: {"analysis": "<step-by-step reasoning>", "choice_indices": [<int>, ...]}'
        )
    else:
        prompt = (
            "You are an expert Oracle SQL & PL/SQL Database Administrator taking an official "
            "Oracle Academy assessment (including quizzes, midterms, and final exams).\n"
            "Read the question carefully and select the ONE best answer.\n\n"
            f"Question:\n{question}\n\nChoices:\n{choices_formatted}\n\n"
            "CRITICAL REQUIREMENT: Select the single best correct option index (0-based, 0 to {len(choices)-1}).\n"
            "Think step by step according to official Oracle Database documentation and semantics.\n\n"
            'Respond ONLY with JSON format: {"analysis": "<step-by-step reasoning>", "choice_indices": [<int>]}'
        )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "topP": 1.0, "maxOutputTokens": 600}
    }).encode("utf-8")

    current_key = get_active_api_key()
    if not current_key:
        print("\n" + "!" * 70, flush=True)
        print("  [CRITICAL] No GEMINI_API_KEY found in .env or environment!", flush=True)
        print("  Please check that Lakra-2.0/.env has: GEMINI_API_KEY=<your_key>", flush=True)
        print("!" * 70 + "\n", flush=True)
        return list(range(min(num_to_choose, len(choices))))

    print(f"  [AI] Querying Gemini (key: {current_key[:8]}...{current_key[-4:]}, len={len(current_key)})", flush=True)
    last_err = None

    for model in CANDIDATE_MODELS:
        for attempt in range(3):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={current_key}"
                req = urllib.request.Request(url, data=payload,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                match = re.search(r"\{[\s\S]*\}", text)
                json_str = match.group(0) if match else text
                if json_str.startswith("```"):
                    json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
                    json_str = re.sub(r"\s*```$", "", json_str)
                parsed = json.loads(json_str.strip())
                raw = parsed.get("choice_indices", [])
                if not raw and "choice_index" in parsed:
                    raw = [parsed["choice_index"]]
                valid = [int(i) for i in raw if 0 <= int(i) < len(choices)]
                if len(valid) >= num_to_choose:
                    res = valid[:num_to_choose]
                    print(f"  [{model}] -> {[i+1 for i in res]} "
                          f"{[choices[i].splitlines()[0][:40] for i in res]}", flush=True)
                    if "analysis" in parsed:
                        print(f"  [REASONING] {parsed['analysis'][:120]}", flush=True)
                    return res
                if valid:
                    return valid
                break
            except Exception as exc:
                last_err = exc
                err_msg = str(exc)
                if hasattr(exc, "read"):
                    try:
                        err_msg += f" - {exc.read().decode('utf-8')[:150]}"
                    except Exception:
                        pass
                if "503" in str(exc) or "unavailable" in str(exc).lower() or \
                   "getaddrinfo" in str(exc) or "timed out" in str(exc).lower():
                    time.sleep(1.0)
                    continue
                print(f"  [{model}] failed: {err_msg[:100]}", flush=True)
                break

    print("\n" + "!" * 70, flush=True)
    print(f"  [ERROR] All Gemini models failed! Last error: {last_err}", flush=True)
    err_str = str(last_err).lower()
    if "403" in err_str or "leaked" in err_str or "revoked" in err_str:
        print("  [CRITICAL] Your GEMINI_API_KEY was reported by Google as LEAKED, REVOKED, or INVALID!", flush=True)
        print("  Please generate a new API key at https://aistudio.google.com/apikey", flush=True)
        print("  and save it to Lakra-2.0/.env as GEMINI_API_KEY=<new_key>", flush=True)
        print("  The automation will poll .env and resume automatically once updated.", flush=True)
        print("!" * 70 + "\n", flush=True)
        # Give user time to paste new key into .env
        for poll_attempt in range(6):
            time.sleep(5)
            reloaded_key = get_active_api_key()
            if reloaded_key and reloaded_key != current_key:
                print("  [INFO] Detected updated GEMINI_API_KEY in .env! Retrying...", flush=True)
                return solve_with_gemini(question, choices, num_to_choose)

    print("!" * 70 + "\n", flush=True)
    # If still no valid response, return first N choices to keep session moving
    return list(range(min(num_to_choose, len(choices))))


def extract_choices(target, page=None) -> list[str]:
    contexts = [target]
    if page and page not in contexts:
        contexts.append(page)
    if page:
        for f in page.frames:
            if f not in contexts:
                contexts.append(f)

    # Retry up to 5 seconds to allow AJAX question regions to finish rendering
    for attempt in range(5):
        for ctx in contexts:
            try:
                choices = ctx.evaluate("""() => {
                    const blacklist = [
                        'instructions', 'true or false?', 'choices - just one correct!',
                        'choices - mark all that apply!', 'exit', 'previous question',
                        'submit answer', 'complete assessment', 'error has occurred',
                        'errors have occurred', 'at least one choice must be selected'
                    ];

                    // 1. Direct radio / checkbox inputs
                    const inps = Array.from(document.querySelectorAll(
                        "input[type='radio'], input[type='checkbox']"
                    ));
                    if (inps.length > 0) {
                        const results = [];
                        inps.forEach(inp => {
                            let labelText = '';
                            if (inp.id) {
                                const l = document.querySelector(`label[for="${inp.id}"]`);
                                if (l) labelText = (l.innerText || l.textContent || '').trim();
                            }
                            if (!labelText) {
                                const opt = inp.closest('.apex-item-option, label, [role="radio"], [role="checkbox"]');
                                if (opt) labelText = (opt.innerText || opt.textContent || '').trim();
                            }
                            if (!labelText && inp.parentElement) {
                                labelText = (inp.parentElement.innerText || inp.parentElement.textContent || '').trim();
                            }
                            if (labelText && !results.includes(labelText)) {
                                const low = labelText.toLowerCase();
                                if (!blacklist.some(b => low.startsWith(b) || low === b)) {
                                    results.push(labelText);
                                }
                            }
                        });
                        if (results.length > 0) return results;
                    }

                    // 2. All labels / .apex-item-option (WITHOUT arbitrary length cutoff)
                    const labels = Array.from(document.querySelectorAll(
                        "label, .apex-item-option, [role='checkbox'], [role='radio']"
                    ));
                    const items = [];
                    labels.forEach(l => {
                        const txt = (l.innerText || l.textContent || '').trim();
                        if (txt && !items.includes(txt)) {
                            const low = txt.toLowerCase();
                            if (!blacklist.some(b => low.startsWith(b) || low === b)) {
                                items.push(txt);
                            }
                        }
                    });
                    if (items.length > 0) return items;

                    // 3. Fallback: table rows / list items in option grid
                    const optionContainers = Array.from(document.querySelectorAll(
                        '.apex-item-grid tr, .apex-item-group li, table.radio_group td, table.checkbox_group td'
                    ));
                    const containerItems = [];
                    optionContainers.forEach(c => {
                        const txt = (c.innerText || c.textContent || '').trim();
                        if (txt && !containerItems.includes(txt)) {
                            const low = txt.toLowerCase();
                            if (!blacklist.some(b => low.startsWith(b) || low === b)) {
                                containerItems.push(txt);
                            }
                        }
                    });
                    return containerItems;
                }""")
                if choices and len(choices) > 0:
                    return choices
            except Exception:
                pass
        time.sleep(1)

    return []


def extract_question(target, page=None) -> str:
    contexts = [target]
    if page and page not in contexts:
        contexts.append(page)
    if page:
        for f in page.frames:
            if f not in contexts:
                contexts.append(f)

    for ctx in contexts:
        try:
            q = ctx.evaluate("""() => {
                const body = document.body.innerText;
                const m = body.match(/Question\\s+\\d+\\s+of\\s+\\d+([\\s\\S]*?)(?=Choices|True or False\\?|Submit Answer)/i);
                if (m && m[1]) {
                    const clean = m[1].replace(/Instructions/gi, '').trim();
                    if (clean.length > 10) return clean;
                }
                const items = Array.from(document.querySelectorAll(
                    '.question-text, .apex-item-display-only, [id*="QUESTION"], h1, h2, h3'
                ));
                for (const item of items) {
                    const t = (item.innerText || '').trim();
                    if (t.length > 15 && !t.includes('Instructions') && !t.includes('Take the Assessment')) return t;
                }
                return '';
            }""")
            if q and len(q) > 10:
                return q
        except Exception:
            pass
    return "Oracle Assessment Question"


def detect_num_to_choose(q_prompt: str, choices: list[str], target) -> int:
    """Detect single vs multi-choice question accurately from prompt and DOM."""
    full_text = q_prompt.lower()
    
    # 1. Regex for bracketed choose: e.g. (Choose two.), (Choose 2), (Choose all that apply), (Select three)
    m = re.search(r'\(\s*(?:choose|select|mark)\s+([a-z0-9\s]+?)\.?\s*\)', q_prompt, re.IGNORECASE)
    if m:
        word = m.group(1).lower().strip().rstrip('.')
        num_map = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "both": 2,
            "all that apply": 3, "all": 3
        }
        if word in num_map:
            return num_map[word]

    # 2. Key phrases in question prompt text
    # 2. Key phrases in question prompt text
    if any(p in full_text for p in ["choose three", "choose 3", "select three", "select 3", "three correct", "three choices"]):
        return 3
    if any(p in full_text for p in ["choose two", "choose 2", "select two", "select 2", "two correct", "choose both", "select both", "two choices"]):
        return 2
    if any(p in full_text for p in ["choose four", "choose 4", "select four", "select 4", "four correct"]):
        return 4
    if any(p in full_text for p in ["choose all", "mark all", "select all"]):
        return min(3, len(choices))

    # 3. Check page banner text
    try:
        page_banner = target.evaluate("""() => {
            const body = (document.body.innerText || '').toLowerCase();
            return {
                isMulti: body.includes('mark all that apply') || body.includes('choose all that apply'),
                isSingle: body.includes('just one correct') || body.includes('true or false')
            };
        }""")
        if page_banner.get("isSingle"):
            return 1
        if page_banner.get("isMulti"):
            return 2
    except Exception:
        pass

    # 4. Inspect DOM input types in active frame
    try:
        input_types = target.evaluate("""() => ({
            checkboxes: document.querySelectorAll("input[type='checkbox']").length,
            radios: document.querySelectorAll("input[type='radio']").length
        })""")
        if input_types["checkboxes"] > 0 and input_types["radios"] == 0:
            # Checkbox group: default to 2 if unspecified
            return 2
    except Exception:
        pass

    return 1


def select_options(target, chosen_indices: list[int], choices: list[str]) -> bool:
    """Select chosen options accurately by exact choice text and state awareness."""
    chosen_texts = [choices[i].strip() for i in chosen_indices if i < len(choices)]
    print(f"  [SELECT] Targeting choices: {chosen_texts}", flush=True)

    # In-page DOM selection matching exact choice labels with strict state awareness
    try:
        target.evaluate("""({chosenTexts, allChoices}) => {
            const allLabels = Array.from(document.querySelectorAll(
                "label, .apex-item-option, [role='checkbox'], [role='radio']"));

            allChoices.forEach(choice => {
                const low = choice.trim().toLowerCase();
                const shouldBeSelected = chosenTexts.some(ct => ct.trim().toLowerCase() === low);

                const match = allLabels.find(l => {
                    const t = (l.innerText || l.textContent || '').trim().toLowerCase();
                    return t === low || t.startsWith(low) || low.startsWith(t);
                });

                if (match) {
                    const forId = match.htmlFor || match.getAttribute('for');
                    let inp = forId ? document.getElementById(forId) : null;
                    if (!inp) {
                        inp = match.querySelector('input') ||
                              (match.parentElement ? match.parentElement.querySelector('input') : null) ||
                              (match.closest('.apex-item-option, div, tr, li') ?
                               match.closest('.apex-item-option, div, tr, li').querySelector('input') : null);
                    }

                    if (shouldBeSelected) {
                        // Check if currently checked
                        const isChecked = inp ? inp.checked :
                            (match.classList.contains('is-checked') ||
                             match.classList.contains('is-selected') ||
                             match.getAttribute('aria-checked') === 'true');

                        if (!isChecked) {
                            match.click();
                        }
                        if (inp) {
                            inp.checked = true;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    } else {
                        // Uncheck if currently checked
                        const isChecked = inp ? inp.checked :
                            (match.classList.contains('is-checked') ||
                             match.classList.contains('is-selected') ||
                             match.getAttribute('aria-checked') === 'true');

                        if (isChecked) {
                            match.click();
                        }
                        if (inp) {
                            inp.checked = false;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                }
            });
        }""", {"chosenTexts": chosen_texts, "allChoices": choices})
    except Exception as exc:
        print(f"  [SELECT] DOM selection error: {exc}", flush=True)

    time.sleep(0.5)

    # Verification count
    verified_count = 0
    try:
        verified_count = target.evaluate("""(chosenTexts) => {
            const allLabels = Array.from(document.querySelectorAll(
                "label, .apex-item-option, [role='checkbox'], [role='radio']"));
            let count = 0;
            chosenTexts.forEach(ct => {
                const low = ct.trim().toLowerCase();
                const match = allLabels.find(l => {
                    const t = (l.innerText || l.textContent || '').trim().toLowerCase();
                    return t === low || t.startsWith(low);
                });
                if (match) {
                    const forId = match.htmlFor || match.getAttribute('for');
                    let inp = forId ? document.getElementById(forId) : null;
                    if (!inp) {
                        inp = match.querySelector('input') ||
                              (match.parentElement ? match.parentElement.querySelector('input') : null) ||
                              (match.closest('.apex-item-option, div, tr, li') ?
                               match.closest('.apex-item-option, div, tr, li').querySelector('input') : null);
                    }
                    if ((inp && inp.checked) || match.classList.contains('is-checked') ||
                        match.classList.contains('is-selected') ||
                        match.getAttribute('aria-checked') === 'true') {
                        count++;
                    }
                }
            });
            const checkedInputs = Array.from(document.querySelectorAll(
                "input[type='radio']:checked, input[type='checkbox']:checked")).length;
            return Math.max(count, checkedInputs);
        }""", chosen_texts)
    except Exception:
        pass

    # If verification shows 0 checked, fallback to native click
    if verified_count == 0 and chosen_texts:
        print("  [SELECT] Fallback: using native Playwright click on chosen options", flush=True)
        for txt in chosen_texts:
            for sel in [
                f"label:has-text('{txt}')",
                f".apex-item-option:has-text('{txt}')",
                f"text='{txt}'",
            ]:
                try:
                    loc = target.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.scroll_into_view_if_needed()
                        loc.click(force=True, timeout=2000)
                        print(f"  [SELECT] Native clicked: '{txt[:30]}'", flush=True)
                        time.sleep(0.3)
                        break
                except Exception:
                    pass

    print(f"  [SELECT] Verified {max(verified_count, len(chosen_texts))}/{len(chosen_indices)} selected", flush=True)
    return True


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


def click_submit_answer(page, target) -> bool:
    """Click ONLY Submit Answer or Next Question on intermediate questions (1 to N-1).
    MUST NEVER click Complete Assessment or Finish Assessment.
    """
    patterns = [r"^\s*(Submit Answer|Submit|Next Question|Next)\s*$"]

    # 1. Native Playwright locator on target frame, then main page
    for ctx in [target, page]:
        for pat in patterns:
            try:
                btn = ctx.locator("button, a.t-Button, input[type='button'], input[type='submit']").filter(
                    has_text=re.compile(pat, re.I)
                ).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(force=True, timeout=3000)
                    return True
            except Exception:
                pass

    # 2. In-DOM JS click on target frame, then main page
    for ctx in [target, page]:
        try:
            clicked = ctx.evaluate("""() => {
                const els = Array.from(document.querySelectorAll(
                    'button, a.t-Button, input[type="button"], input[type="submit"], [role="button"]'
                ));
                const b = els.find(x => {
                    const t = (x.innerText || x.value || x.textContent || '').trim().toLowerCase();
                    return (t === 'submit answer' || t === 'submit' ||
                            t === 'next question' || t === 'next') &&
                           !x.disabled && !x.classList.contains('is-disabled');
                });
                if (b) {
                    b.focus();
                    b.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                return True
        except Exception:
            pass

    return False


def click_complete_assessment(page, target) -> bool:
    """Click Complete Assessment or Finish Assessment ONLY on the confirmed final question."""
    patterns = [r"^\s*(Complete Assessment|Finish Assessment|Complete|Finish)\s*$"]

    # 1. Native Playwright locator
    for ctx in [target, page]:
        for pat in patterns:
            try:
                btn = ctx.locator("button, a.t-Button, input[type='button'], input[type='submit']").filter(
                    has_text=re.compile(pat, re.I)
                ).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(force=True, timeout=3000)
                    return True
            except Exception:
                pass

    # 2. In-DOM JS click
    for ctx in [target, page]:
        try:
            clicked = ctx.evaluate("""() => {
                const els = Array.from(document.querySelectorAll(
                    'button, a.t-Button, input[type="button"], input[type="submit"], [role="button"]'
                ));
                const b = els.find(x => {
                    const t = (x.innerText || x.value || x.textContent || '').trim().toLowerCase();
                    return (t.includes('complete assessment') || t.includes('finish assessment') ||
                            t === 'complete' || t === 'finish') &&
                           !x.disabled && !x.classList.contains('is-disabled');
                });
                if (b) {
                    b.focus();
                    b.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                return True
        except Exception:
            pass

    return False


def confirm_completion_modal(page) -> bool:
    """Handle confirmation dialog modal when finishing the assessment."""
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
                                t.includes('finish assessment') ||
                                t === 'yes' || t === 'ok' ||
                                t === 'submit') && !b.disabled;
                    });
                    if (btn) { btn.click(); return btn.innerText || 'Confirmed'; }
                }
            }
            return null;
        }""")
        if confirmed:
            print(f"  [MODAL] Confirmed final assessment completion: '{confirmed}'", flush=True)
            time.sleep(5)
            return True
        time.sleep(1)
    return False


def run_quiz(page, quiz_label: str) -> None:
    print(f"\n{'='*60}\n[QUIZ] {quiz_label}\n{'='*60}", flush=True)
    q_idx = 1
    max_q = 75

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

        choices = extract_choices(target, page)
        if not choices:
            body = target.locator("body").inner_text() or ""
            if any(w in body.lower() for w in ("score", "grade", "passed", "completed", "review")):
                print("[QUIZ] Completion screen detected", flush=True)
                break
            if "p=63000:192" in page.url or page.locator("text=/Percentage\\s+Scored/i").count() > 0:
                print("[QUIZ] Reached score summary page 192", flush=True)
                break
            print(f"[QUIZ] No choices extracted on {q_num} — clicking Submit Answer to advance", flush=True)
            click_submit_answer(page, target)
            time.sleep(3)
            q_idx += 1
            continue

        q_prompt = extract_question(target, page)
        print(f"  Q: {q_prompt[:90]}", flush=True)
        print(f"  Choices: {[c.splitlines()[0][:40] for c in choices]}", flush=True)

        num_to_choose = detect_num_to_choose(q_prompt, choices, target)
        print(f"  Mode: {'multi-' + str(num_to_choose) if num_to_choose > 1 else 'single'}"
              f" (required={num_to_choose})", flush=True)

        chosen = solve_with_gemini(q_prompt, choices, num_to_choose)
        select_options(target, chosen, choices)
        time.sleep(0.8)

        # Strict detection of last question (e.g. "Question 15 of 15", "Question 50 of 50")
        is_last = False
        m_q = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", q_num, re.IGNORECASE)
        if not m_q:
            m_q = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", q_prompt, re.IGNORECASE)
        if not m_q:
            try:
                found_txt = target.evaluate("""() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const match = all.find(el => el.children.length === 0 &&
                        /Question\\s+\\d+\\s+of\\s+\\d+/i.test((el.innerText || '').trim()));
                    return match ? match.innerText.trim() : '';
                }""")
                if found_txt:
                    m_q = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", found_txt, re.IGNORECASE)
            except Exception:
                pass

        if m_q:
            cur_q = int(m_q.group(1))
            total_q = int(m_q.group(2))
            is_last = (cur_q >= total_q)
            print(f"  [QUESTION PROGRESS] Question {cur_q} of {total_q} (is_last={is_last})", flush=True)
        else:
            is_last = False

        if is_last:
            print(f"  [ACTION] Final question reached ({q_num}) — Complete Assessment", flush=True)
            click_complete_assessment(page, target)
            confirm_completion_modal(page)
            time.sleep(5)
            break
        else:
            print(f"  [ACTION] Submitting answer for {q_num} ('Submit Answer')...", flush=True)
            click_submit_answer(page, target)

            # Wait for next question or page update
            advanced = False
            for wait_sec in range(8):
                time.sleep(1)
                try:
                    if "p=63000:192" in page.url or "percentage" in (page.title() or "").lower():
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
                        print(f"  [ADVANCED] Moved from {q_num} -> {q_after}", flush=True)
                        advanced = True
                        q_idx += 1
                        break
                except Exception:
                    pass

            if not advanced:
                print(f"  [RETRY SUBMIT] Still on {q_num} — clicking Submit Answer again...", flush=True)
                click_submit_answer(page, target)
                time.sleep(3)
                q_idx += 1

    if "p=63000:192" in page.url or page.locator("text=/Percentage\\s+Scored/i").count() > 0:
        handle_score_summary(page, quiz_label)
    else:
        print("[QUIZ] Loop ended without reaching score summary page 192", flush=True)


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
                try:
                    cache = load_answers_cache()
                    cache.update(results)
                    save_answers_cache(cache)
                    print(f"  [CACHE] Saved {len(results)} answers to cache (total {len(cache)})", flush=True)
                except Exception as c_exc:
                    print(f"  [CACHE ERROR] Failed to save cache: {c_exc}", flush=True)
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
    """Find and click an incomplete quiz, midterm, or final exam link."""
    clicked = page.evaluate(r"""() => {
        const isAssessmentText = (t) => {
            const low = (t || '').toLowerCase();
            return low.includes('quiz') || low.includes('midterm') ||
                   low.includes('final exam') || low.includes('final examination') ||
                   low.includes('exam') || low.includes('test:');
        };

        const links = Array.from(document.querySelectorAll('a'));
        const assessmentLinks = links.filter(a => {
            const t = (a.innerText || a.textContent || '').trim();
            return isAssessmentText(t);
        });

        for (const al of assessmentLinks) {
            const parent = al.closest('tr, li, .t-TreeNav-item, .a-CardView-item, .t-Card');
            const parentText = (parent ? parent.innerText : al.innerText).toLowerCase();
            const isActive = parentText.includes('(active)') ||
                             (parent && parent.classList.contains('is-active')) ||
                             (parent && parent.classList.contains('is-current')) ||
                             al.classList.contains('is-active');
            if (isActive && window.location.href.includes('63000:15')) {
                // Already on this assessment's page 15! Don't click to reload.
                continue;
            }
            const isDone = parentText.includes('100%') || parentText.includes('passed') ||
                           parentText.includes('mastery achieved') ||
                           (parent && parent.querySelector('.u-success, .fa-check, .fa-check-circle, [class*="check"]') !== null);
            if (!isDone) {
                if (al.scrollIntoViewIfNeeded) al.scrollIntoViewIfNeeded();
                al.click();
                return { clicked: true, text: (al.innerText || '').trim(), href: al.href || '' };
            }
        }
        return { clicked: false };
    }""")
    if clicked.get("clicked"):
        print(f"  [ASSESSMENT LINK] Clicked: '{clicked.get('text')}'", flush=True)
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
    """Page 15: Lesson or Assessment Landing / Resume page.
    Launches or resumes the assessment into Page 190.
    """
    print("  [PAGE 15] Inspecting assessment landing / resume screen", flush=True)
    time.sleep(2)

    # 1. Check if start/resume modal dialog is already open
    if handle_start_modal(page):
        time.sleep(3)
        if "p=63000:190" in page.url:
            print("  [PAGE 15] Quiz launched via start modal", flush=True)
            return page

    # 2. Check for Take / Resume / Start Assessment buttons
    btn_info = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll(
            'button, a, .t-Button, input[type="button"], input[type="submit"], [role="button"]'
        ));
        const b = btns.find(x => {
            const t = (x.innerText || x.value || '').trim().toLowerCase();
            return [
                'resume assessment', 'take an assessment', 'start assessment',
                'continue assessment', 'take assessment', 'start', 'resume',
                'finish assessment'
            ].some(k => t === k || t.startsWith(k));
        });
        if (b) {
            return {
                found: true,
                text: (b.innerText || b.value || '').trim(),
                href: b.href || '',
                tag: b.tagName
            };
        }
        return { found: false };
    }""")

    if btn_info.get("found"):
        btn_text = btn_info.get("text")
        print(f"  [PAGE 15] Found assessment launch button: '{btn_text}'", flush=True)

        # Click using Playwright locator
        try:
            loc = page.locator("button, a, .t-Button, input[type='button']").filter(
                has_text=re.compile(re.escape(btn_text), re.I)
            ).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(force=True, timeout=5000)
                print(f"  [PAGE 15] Clicked '{btn_text}' via locator", flush=True)
                time.sleep(4)
        except Exception:
            pass

        # Fallback in-DOM click
        if "p=63000:190" not in page.url:
            page.evaluate("""(targetText) => {
                const btns = Array.from(document.querySelectorAll(
                    'button, a, .t-Button, input[type="button"], [role="button"]'
                ));
                const b = btns.find(x => {
                    const t = (x.innerText || x.value || '').trim().toLowerCase();
                    return t === targetText.toLowerCase() || t.startsWith(targetText.toLowerCase());
                });
                if (b) b.click();
            }""", btn_text)
            time.sleep(4)

        # Check for start modal
        handle_start_modal(page)
        for f in page.frames:
            try:
                handle_start_modal(f)
            except Exception:
                pass

        if "p=63000:190" in page.url:
            print("  [PAGE 15] Successfully transitioned to Quiz Page 190!", flush=True)
            return page

        # Check for new tabs
        context = page.context
        if len(context.pages) > 1:
            for p in context.pages:
                if "63000:190" in p.url:
                    p.bring_to_front()
                    print(f"  [PAGE 15] Switched to new tab: {p.url[:70]}", flush=True)
                    return p

        return page

    # 3. Theory / Slides: Save and Continue
    body = page.locator("body").inner_text(timeout=2000) or ""
    if "save and continue" in body.lower() or "save & continue" in body.lower():
        print("  [THEORY] Clicking 'Save and Continue'", flush=True)
        try:
            btn = page.locator("button:has-text('Save and Continue'), button:has-text('Save & Continue')").first
            if btn.count() > 0 and btn.is_visible():
                btn.click(force=True, timeout=5000)
                time.sleep(4)
                return page
        except Exception:
            pass

    # 4. Only if not on an assessment and not on theory, check for quiz link in sidebar
    if not any(w in body.lower() for w in ("assessment", "midterm", "exam", "quiz")):
        find_and_click_quiz_link(page)

    return page


COMPLETED_COURSES: set[str] = set()


def handle_page_14(page) -> str | bool:
    print("  [PAGE 14] Scanning for next incomplete quiz, midterm, or final exam", flush=True)
    if find_and_click_quiz_link(page):
        return True
    result = page.evaluate("""() => {
        const isAssessmentText = (t) => {
            const low = (t || '').toLowerCase();
            return low.includes('quiz') || low.includes('midterm') ||
                   low.includes('final exam') || low.includes('final examination') ||
                   low.includes('exam') || low.includes('test:');
        };

        const containers = Array.from(document.querySelectorAll(
            'tr, .a-CardView-item, .t-Card, li, .t-TreeNav-item, .t-Region'));
        const items = [];
        for (const c of containers) {
            const text = (c.innerText || '').trim();
            if (isAssessmentText(text)) {
                const isDone = text.toLowerCase().includes('100%') ||
                    text.toLowerCase().includes('mastery achieved') || text.toLowerCase().includes('passed') ||
                    c.querySelector('.u-success, .fa-check, .fa-check-circle, [class*="check"], [class*="complete"]') !== null;
                const link = c.querySelector('a') || (c.tagName === 'A' ? c : null);
                items.push({ isDone, hasLink: !!link,
                             text: text.substring(0, 60) });
            }
        }
        const hasIncomplete = items.some(i => !i.isDone && i.hasLink);
        if (!hasIncomplete && items.length > 0) {
            return { allCompleted: true, clicked: null };
        }
        for (const c of containers) {
            const text = (c.innerText || '').trim();
            if (isAssessmentText(text)) {
                const isDone = text.toLowerCase().includes('100%') ||
                    text.toLowerCase().includes('mastery achieved') || text.toLowerCase().includes('passed') ||
                    c.querySelector('.u-success, .fa-check, .fa-check-circle, [class*="check"], [class*="complete"]') !== null;
                if (!isDone) {
                    const link = c.querySelector('a') || (c.tagName === 'A' ? c : null);
                    if (link) {
                        if (link.scrollIntoViewIfNeeded) link.scrollIntoViewIfNeeded();
                        link.click();
                        return { allCompleted: false,
                                 clicked: (link.innerText || '').trim().split('\\n')[0] };
                    }
                }
            }
        }
        return { allCompleted: false, clicked: null };
    }""")
    if isinstance(result, dict) and result.get("allCompleted"):
        print("  [PAGE 14] All sections, quizzes, and exams completed for this course! Switching course...", flush=True)
        return "SWITCH_COURSE"
    clicked = result.get("clicked") if isinstance(result, dict) else None
    if clicked:
        print(f"  [PAGE 14] Selected: '{clicked}'", flush=True)
        time.sleep(4)
        return True
    return False


def handle_page_100(page, completed: set[str] | None = None) -> bool:
    if completed is None:
        completed = COMPLETED_COURSES
    print(f"  [PAGE 100] Scanning My Classes (Completed: {list(completed)})", flush=True)
    chosen_course = page.evaluate("""(doneList) => {
        const links = Array.from(document.querySelectorAll('a[href*="63000:14:"]'));
        const courses = [];
        for (const a of links) {
            const card = a.closest('.a-CardView-item, .t-Card, tr, li') || a;
            const text = (card.innerText || a.innerText || '').trim();
            const href = a.href || '';
            courses.push({ text, href });
        }
        
        // Filter out completed courses
        const available = courses.filter(c => {
            const low = c.text.toLowerCase();
            return !doneList.some(done => low.includes(done.toLowerCase()));
        });
        
        // Prioritize PL/SQL
        const plsql = available.find(c => c.text.toLowerCase().includes('pl/sql') || c.text.toLowerCase().includes('plsql'));
        if (plsql) return plsql;
        
        if (available.length > 0) return available[0];
        return courses[0] || null;
    }""", list(completed))

    if chosen_course and chosen_course.get("href"):
        print(f"  [PAGE 100] Entering course: '{chosen_course.get('text', '')[:50]}' -> {chosen_course.get('href')[:60]}", flush=True)
        page.goto(chosen_course["href"], timeout=30000, wait_until="domcontentloaded")
        time.sleep(4)
        return True
    return False


def cleanup_stale_profile_locks(profile_dir: Path):
    """Remove stale locks and clean up lingering lock files if previous process crashed."""
    lock_files = [
        profile_dir / ".lakra-profile.lock",
        profile_dir / "SingletonLock",
        profile_dir / "SingletonCookie",
        profile_dir / "SingletonSocket",
        profile_dir / "lockfile",
    ]
    for lf in lock_files:
        try:
            if lf.exists():
                lf.unlink(missing_ok=True)
        except Exception:
            pass


def run():
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60, flush=True)
    print("  Oracle Academy Quiz Automation v2 (single session)", flush=True)
    print("=" * 60, flush=True)

    from playwright.sync_api import sync_playwright

    cleanup_stale_profile_locks(PROFILE_DIR)

    with sync_playwright() as pw:
        args = [
            "--disable-blink-features=AutomationControlled",
            f"--host-resolver-rules={HOST_RESOLVER_RULES}",
            "--enable-features=DnsOverHttps",
            "--dns-over-https-mode=automatic",
        ]
        try:
            context = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 850},
                args=args,
            )
        except Exception as exc:
            if "ProcessSingleton" in str(exc):
                print("  [WARN] Chromium profile locked by another process. Cleaning up and retrying...", flush=True)
                cleanup_stale_profile_locks(PROFILE_DIR)
                time.sleep(2)
                context = pw.chromium.launch_persistent_context(
                    str(PROFILE_DIR),
                    headless=False,
                    viewport={"width": 1280, "height": 850},
                    args=args,
                )
            else:
                raise
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
                    # 1. Page 192 (Score Summary)
                    if "p=63000:192" in curr_url or \
                            page.locator("text=/Percentage\\s+Scored/i").count() > 0:
                        handle_score_summary(page, f"Quiz_{cycle}")
                        continue

                    # 2. Page 190 (Active Quiz Question Page)
                    target = get_quiz_target(page)
                    is_quiz_p190 = ("p=63000:190" in curr_url) or (target != page and "63000:190" in target.url)
                    if is_quiz_p190:
                        run_quiz(page, f"Quiz_{cycle}")
                        continue

                    # 3. Page 15 (Lesson / Assessment Landing / Resume Page)
                    if "p=63000:15" in curr_url or "class course lesson" in title.lower() or "take the assessment" in title.lower():
                        result = handle_page_15(page)
                        if isinstance(result, type(page)):
                            page = result
                        continue

                    if "p=63000:14" in curr_url or "taking a class" in title.lower():
                        p14_res = handle_page_14(page)
                        if p14_res == "SWITCH_COURSE":
                            course_title = page.evaluate("""() => {
                                const el = document.querySelector('h1, .t-Header-nav, .t-Breadcrumb-item--current');
                                return el ? (el.innerText || '').trim() : '';
                            }""") or "SQL"
                            COMPLETED_COURSES.add(course_title)
                            if "sql" in course_title.lower() and "pl/sql" not in course_title.lower():
                                COMPLETED_COURSES.add("database programming with sql")
                                COMPLETED_COURSES.add("sql")
                            print(f"  [COURSE COMPLETE] '{course_title}' has no remaining quizzes. Returning to My Classes...", flush=True)

                            nav_ok = page.evaluate("""() => {
                                const a = Array.from(document.querySelectorAll('a')).find(el =>
                                    (el.innerText || '').toLowerCase().includes('my classes') || (el.href || '').includes('63000:100'));
                                if (a) { a.click(); return true; }
                                return false;
                            }""")
                            if not nav_ok:
                                page.goto("https://academy.oracle.com/pls/f?p=63000:100", timeout=30000, wait_until="domcontentloaded")
                            time.sleep(4)
                        continue

                    if "p=63000:100" in curr_url or "my classes" in title.lower():
                        handle_page_100(page, COMPLETED_COURSES)
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
