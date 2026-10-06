import sqlite3
import shutil
import tempfile
from pathlib import Path

candidate_paths = [
    Path(r"C:\Users\nikhi\AppData\Local\Google\Chrome\User Data\Default\Network\Cookies"),
    Path(r"C:\Users\nikhi\AppData\Local\BraveSoftware\Brave-Browser\User Data\Profile 1\Network\Cookies"),
    Path(r"C:\Users\nikhi\AppData\Local\BraveSoftware\Brave-Browser\User Data\Profile 2\Network\Cookies"),
    Path(r"C:\Users\nikhi\AppData\Local\BraveSoftware\Brave-Browser\User Data\Profile 3\Network\Cookies"),
    Path(r"C:\Users\nikhi\OneDrive\Desktop\coding\Personal-Agents\Lakra-2.0\var\sessions\oracle\Default\Network\Cookies")
]

for p in candidate_paths:
    if not p.exists():
        continue
    # Copy to temp because browser might have it locked
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_name = tmp.name
    try:
        shutil.copyfile(str(p), tmp_name)
        conn = sqlite3.connect(tmp_name)
        rows = conn.cursor().execute("SELECT host_key, name, last_access_utc FROM cookies WHERE host_key LIKE '%oracle%'").fetchall()
        print(f"Path: {p}")
        print(f"  Total oracle cookies: {len(rows)}")
        for r in rows:
            if "session" in r[1].lower() or "app" in r[1].lower() or "token" in r[1].lower() or "auth" in r[1].lower():
                print(f"    {r[0]} -> {r[1]}")
        conn.close()
    except Exception as e:
        print(f"Path: {p} error: {e}")
    finally:
        Path(tmp_name).unlink(missing_ok=True)
