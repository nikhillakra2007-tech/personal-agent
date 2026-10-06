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

MODEL = "gemini-flash-lite-latest"

def test_multi_solver(question, choices):
    # Detect num choices
    full_text = (question + " " + " ".join(choices)).lower()
    num_to_choose = 1
    if any(k in full_text for k in ["choose three", "choose 3", "three correct", "select three"]):
        num_to_choose = 3
    elif any(k in full_text for k in ["choose two", "choose 2", "two correct", "select two", "mark all that apply"]):
        num_to_choose = 2

    choices_formatted = "\n".join(f"{idx}: {text}" for idx, text in enumerate(choices))
    
    if num_to_choose > 1:
        prompt = f"""You are an expert Oracle SQL Database administrator taking an official Oracle Academy assessment.
Question:
{question}

Choices:
{choices_formatted}

IMPORTANT: This question requires you to select EXACTLY {num_to_choose} correct options.
Select the {num_to_choose} best correct choice indices (0 to {len(choices)-1}).
Respond ONLY with a JSON object: {{"choice_indices": [<int>, ...], "reasoning": "<1 sentence>"}}"""
    else:
        prompt = f"""You are an expert Oracle SQL Database administrator taking an official Oracle Academy assessment.
Question:
{question}

Choices:
{choices_formatted}

Select the single best correct choice index (0 to {len(choices)-1}).
Respond ONLY with a JSON object: {{"choice_indices": [<int>], "reasoning": "<1 sentence>"}}"""

    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text.strip())
        indices = parsed.get("choice_indices", [])
        print(f"Question: {question[:60]}... (num_to_choose={num_to_choose})")
        print(f"Indices: {indices}")
        for i in indices:
            print(f"  -> {choices[i]}")
        print(f"Reasoning: {parsed.get('reasoning')}\n")

# Test 1: Single choice
test_multi_solver(
    "The answer to the following script is 457. SELECT TRUNC(ROUND(456.98)) FROM dual;",
    ["True", "False"]
)

# Test 2: Choose two
test_multi_solver(
    "Which of the following functions can be used on DATE datatypes? (Choose two)",
    ["ROUND", "TRUNC", "SUBSTR", "INSTR"]
)

# Test 3: Choose three
test_multi_solver(
    "Which of the following are valid single-row character functions in Oracle SQL? (Choose three)",
    ["LENGTH", "UPPER", "SUM", "CONCAT", "AVG"]
)
