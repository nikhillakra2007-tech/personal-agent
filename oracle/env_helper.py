"""Environment and secret management for Oracle suite."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(env_path: Path | None = None) -> dict[str, str]:
    """Load key-value pairs from .env into os.environ, always updating with freshest values."""
    if env_path is None:
        candidates = [
            Path(__file__).resolve().parent / ".env",
            Path(__file__).resolve().parent.parent / ".env",
            Path.cwd() / ".env",
            Path.cwd() / "Lakra-2.0" / ".env",
            Path.cwd().parent / ".env",
            Path.cwd().parent / "Lakra-2.0" / ".env",
        ]
        for c in candidates:
            if c.is_file():
                env_path = c.resolve()
                break

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
