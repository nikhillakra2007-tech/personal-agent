import json
import os
import re
import urllib.request
from pathlib import Path

# Load from environment or .env
API_KEY = os.getenv("GEMINI_API_KEY", "")
if not API_KEY:
    for p in [Path(".env"), Path("../.env"), Path("../../.env")]:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if API_KEY:
            break

def solve_with_gemini_flash_lite(question: str, choices: list[str]) -> int:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent?key={API_KEY}"
    choices_formatted = "\n".join(f"{idx}: {text}" for idx, text in enumerate(choices))
    prompt = f"""You are an expert Oracle SQL Database administrator taking an official Oracle Academy assessment.
Question:
{question}

Choices:
{choices_formatted}

Select the single best correct choice index (0 to {len(choices)-1}).
Respond ONLY with a JSON object: {{"choice_index": <int>, "reasoning": "<1 sentence>"}}"""

    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text.strip())
        return int(parsed["choice_index"])

# Test Q7
q7 = "Which of the following SQL statements will correctly display the last name and the number of weeks employed for all employees in department 90?"
c7 = [
  'SELECT last_name, (SYSDATE-hire_date)/7 AS WEEKS\nFROM employees\nWHERE department_id = 90;',
  'SELECT last name, (SYSDATE_hire_date)/7 DISPLAY WEEKS\nFROM employees\nWHERE department id = 90;',
  'SELECT last_name, # of WEEKS\nFROM employees\nWHERE department_id = 90;',
  'SELECT last_name, (SYSDATE-hire_date)AS WEEK\nFROM employees\nWHERE department_id = 90;'
]
idx7 = solve_with_gemini_flash_lite(q7, c7)
print("Q7 Solved by Gemini Flash Lite:", idx7, c7[idx7].splitlines()[0])
