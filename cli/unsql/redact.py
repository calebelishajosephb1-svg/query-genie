"""Minimal secret redaction used by engine adapters when surfacing errors."""
from __future__ import annotations

import re

_SECRETS: set[str] = set()

_PATTERNS = [
    re.compile(r"(?i)(password|pwd|pass)\s*=\s*[^;\s]+"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|nvapi-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{8,})\b"),
    re.compile(r"://([^:/@\s]+):([^@\s]+)@"),
]


def register_secret(value: str) -> None:
    """Remember a literal secret so it is masked wherever it appears."""
    if value and len(value) >= 4:
        _SECRETS.add(value)


def redact(text: str) -> str:
    out = str(text)
    for secret in _SECRETS:
        out = out.replace(secret, "***")
    out = _PATTERNS[0].sub(lambda m: m.group(0).split("=")[0] + "=***", out)
    out = _PATTERNS[1].sub("***", out)
    out = _PATTERNS[2].sub(lambda m: f"://{m.group(1)}:***@", out)
    return out
