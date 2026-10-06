import ctypes
from ctypes import wintypes
import os
import sqlite3
from pathlib import Path

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
INVALID_HANDLE_VALUE = -1

def read_locked_file(src_path: str) -> bytes:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateFileW(
        src_path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None
    )
    if handle == INVALID_HANDLE_VALUE:
        err = kernel32.GetLastError()
        raise OSError(f"Failed to open {src_path}, error code {err}")
    
    try:
        # Get file size
        size_high = wintypes.DWORD()
        size_low = kernel32.GetFileSize(handle, ctypes.byref(size_high))
        total_size = (size_high.value << 32) + size_low
        
        buffer = ctypes.create_string_buffer(total_size)
        bytes_read = wintypes.DWORD()
        success = kernel32.ReadFile(handle, buffer, total_size, ctypes.byref(bytes_read), None)
        if not success:
            err = kernel32.GetLastError()
            raise OSError(f"Failed to read {src_path}, error code {err}")
        return buffer.raw[:bytes_read.value]
    finally:
        kernel32.CloseHandle(handle)

brave_user_data = Path(r"C:\Users\nikhi\AppData\Local\BraveSoftware\Brave-Browser\User Data")
for prof in ["Default", "Profile 1", "Profile 2", "Profile 3"]:
    cookie_path = brave_user_data / prof / "Network" / "Cookies"
    if not cookie_path.exists():
        continue
    try:
        data = read_locked_file(str(cookie_path))
        tmp_db = Path(f"var/tmp/brave_{prof.replace(' ', '_')}_cookies.db")
        tmp_db.write_bytes(data)
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.cursor().execute("SELECT host_key, name FROM cookies WHERE host_key LIKE '%oracle%'").fetchall()
        print(f"Brave {prof}: {len(rows)} oracle cookies found")
        for r in rows:
            print(f"   {r[0]} | {r[1]}")
        conn.close()
    except Exception as e:
        print(f"Brave {prof} failed: {e}")
