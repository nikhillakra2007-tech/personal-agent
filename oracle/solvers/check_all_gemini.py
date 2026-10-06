import os
import urllib.request
import json
import time
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

candidates = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-pro-latest"
]

prompt = "Solve Oracle SQL: SELECT TRUNC(ROUND(456.98)) FROM dual; Is the result 456? Answer True or False."
payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")

for m in candidates:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={API_KEY}"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ans = data['candidates'][0]['content']['parts'][0]['text'].strip().replace("\n", " ")
            print(f"[{m}] SUCCESS ({time.time()-t0:.1f}s): {ans[:80]}")
    except Exception as e:
        print(f"[{m}] ERROR: {e}")
