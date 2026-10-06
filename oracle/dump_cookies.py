import sqlite3
from pathlib import Path
import datetime

db_path = Path("var/sessions/oracle/Default/Network/Cookies")
if not db_path.exists():
    print("Cookies DB does not exist at", db_path)
    exit(1)

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
rows = cur.execute("SELECT host_key, name, is_persistent, has_expires, expires_utc, last_access_utc FROM cookies WHERE host_key LIKE '%oracle%'").fetchall()
print(f"Total oracle cookies: {len(rows)}")
for r in rows:
    # Chromium timestamps are microseconds since Jan 1, 1601 UTC
    # 11644473600 seconds between 1601 and 1970
    exp_s = (r[4] / 1000000) - 11644473600 if r[4] > 0 else 0
    exp_dt = datetime.datetime.fromtimestamp(exp_s, datetime.timezone.utc) if exp_s > 0 else "SESSION"
    print(f"Host: {r[0]:35} | Name: {r[1]:20} | Expires: {exp_dt}")

conn.close()
