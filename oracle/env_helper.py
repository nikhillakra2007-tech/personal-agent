"""Environment and secret management for Oracle suite."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(env_path: Path | None = None) -> dict[str, str]:
    """Load key-value pairs from .env into os.environ if not already present."""
    if env_path is None:
        cur = Path(__file__).resolve().parent
        for _ in range(4):
            candidate = cur / ".env"
            if candidate.is_file():
                env_path = candidate
                break
            cur = cur.parent

    values: dict[str, str] = {}
    if env_path and env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            values[k] = v
            if k not in os.environ:
                os.environ[k] = v
    return values


def get_gemini_api_key() -> str:
    """Retrieve Gemini API key from environment or local .env."""
    load_env()
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please add it to your .env file or environment variables."
        )
    return key


def get_student_email() -> str:
    """Retrieve optional student email from environment or local .env."""
    load_env()
    return os.environ.get("ORACLE_STUDENT_EMAIL", "")
