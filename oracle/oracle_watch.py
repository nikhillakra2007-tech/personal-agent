"""Watch Oracle Academy outage status (quiz readiness probe).

Headless probe of the class URL on the saved `oracle` session profile.
Reports one of: UP (quiz markers present), OUTAGE (Oracle banner),
DNSFLAKE (transient resolution failure), DOWN (other error).

Usage:
    .venv\\Scripts\\python.exe scripts\\oracle_watch.py [--profile oracle]

Exit codes: 0 = UP, 1 = still out/unreachable, 2 = usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

QUIZ_URL = ("https://academy.oracle.com/pls/f?p=63000"
            ":15:408416021490716::::P15_ID,P15_CC")
COURSE_URL = ("https://academy.oracle.com/pls/f?p=63000:100:"
              "405864089372249:::100::&cs=3q6Xlcg6OEVNW4rcLCkRlr4wOrGFcnKdBj"
              "6OPGG4WVrDCOWcUlJMtn1F4Zodxr-LjYdIEXGqOq7wNAODhCGstCg")
WATCH_URLS = [("course", COURSE_URL), ("quiz", QUIZ_URL)]
OUTAGE_MARK = "currently experiencing technical difficulties"
UP_MARKS = ("Take an Assessment", "Database Programming with SQL",
            "Section 4")


def main(argv: list[str]) -> int:
    profile = "oracle"
    if "--profile" in argv:
        i = argv.index("--profile")
        if i + 1 >= len(argv):
            print("usage error: --profile needs a value")
            return 2
        profile = argv[i + 1]
    from lakra.control.profiles import validate_profile_name
    try:
        name = validate_profile_name(profile)
    except Exception as exc:
        print(f"refused: {exc}")
        return 2
    from lakra.execution.browser.sessions import BrowserSessions
    sessions = BrowserSessions(ROOT / "var" / "sessions" / name,
                               headless=False)
    sessions.launch()
    try:
        page = sessions._require_context().new_page()
        results = {}
        for label, url in WATCH_URLS:
            text = ""
            for attempt in range(5):
                try:
                    page.goto(url, wait_until="load", timeout=15000)
                    page.wait_for_timeout(5000)
                    text = page.locator("body").inner_text()
                    break
                except Exception as exc:
                    if "ERR_NAME_NOT_RESOLVED" in str(exc):
                        page.wait_for_timeout(2000)
                        continue
                    results[label] = f"DOWN: {str(exc)[:120]}"
                    text = None
                    break
                if attempt == 4:
                    results[label] = "DNSFLAKE after 5 tries"
                    text = None
            if text is None:
                continue
            if OUTAGE_MARK in text:
                results[label] = f"OUTAGE (body len={len(text)})"
            else:
                found = [m for m in UP_MARKS if m in text]
                results[label] = (f"UP markers={found} (len={len(text)})"
                                  if found else
                                  f"UNKNOWN (len={len(text)}) {text[:200]!r}")
    finally:
        sessions.close()
    for label, res in results.items():
        print(f"{label}: {res}")
    return 0 if any(r.startswith("UP") for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
