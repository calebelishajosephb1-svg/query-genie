"""
unsql/config.py
---------------
Provider picker + config store (~/.unsql_config, JSON, 0600 on POSIX).

Fields: provider, api_key, model, base_url.
Env overrides (win over the file): UNSQL_PROVIDER, UNSQL_API_KEY,
UNSQL_MODEL, UNSQL_BASE_URL.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .redact import register_secret

CONFIG_PATH = Path.home() / ".unsql_config"

# tag -> definition. `kind` selects the wire protocol in ai.py.
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "name": "Anthropic (Claude)",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-5-20250929",
        "key_hint": "sk-ant-...",
        "key_required": True,
    },
    "openai": {
        "name": "OpenAI (GPT)",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "key_hint": "sk-...",
        "key_required": True,
    },
    "gemini": {
        "name": "Google Gemini",
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-pro",
        "key_hint": "AIza...",
        "key_required": True,
    },
    "openrouter": {
        "name": "OpenRouter",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4.5",
        "key_hint": "sk-or-...",
        "key_required": True,
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "kind": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
        "key_hint": "nvapi-...",
        "key_required": True,
    },
    "ollama": {
        "name": "Ollama (local)",
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1",
        "key_hint": None,
        "key_required": False,
    },
    "lmstudio": {
        "name": "LM Studio (local)",
        "kind": "openai",
        "base_url": "http://localhost:1234/v1",
        "default_model": None,
        "key_hint": None,
        "key_required": False,
    },
    "custom": {
        "name": "Custom OpenAI-compatible endpoint",
        "kind": "openai",
        "base_url": None,
        "default_model": None,
        "key_hint": "key (blank if none)",
        "key_required": False,
    },
}

_ORDER = list(PROVIDERS.keys())


# ── storage ──────────────────────────────────────────────────────────────────


def _load() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    payload = json.dumps(data, indent=2)
    if sys.platform == "win32":
        CONFIG_PATH.write_text(payload, encoding="utf-8")
    else:
        fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)


# ── public API ───────────────────────────────────────────────────────────────


def load_config() -> dict:
    """Config from disk, overlaid with UNSQL_* environment variables."""
    cfg = _load()
    for env, field in (
        ("UNSQL_PROVIDER", "provider"),
        ("UNSQL_API_KEY", "api_key"),
        ("UNSQL_MODEL", "model"),
        ("UNSQL_BASE_URL", "base_url"),
    ):
        val = os.environ.get(env)
        if val:
            cfg[field] = val
    if cfg.get("api_key"):
        register_secret(cfg["api_key"])
    return cfg


def is_configured(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else load_config()
    tag = cfg.get("provider")
    if tag not in PROVIDERS or not cfg.get("model") or not cfg.get("base_url"):
        return False
    if PROVIDERS[tag]["key_required"] and not cfg.get("api_key"):
        return False
    return True


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    return value or (default or "")


def run_wizard(existing: dict | None = None) -> dict:
    """Interactive provider / key / model picker. Returns the saved config."""
    existing = existing or {}
    print("\n  AI provider setup")
    print("  " + "-" * 44)
    for i, tag in enumerate(_ORDER, 1):
        print(f"   {i}. {PROVIDERS[tag]['name']}")
    while True:
        choice = _ask("\n  Provider number", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(_ORDER):
            tag = _ORDER[int(choice) - 1]
            break
        if choice.lower() in PROVIDERS:
            tag = choice.lower()
            break
        print("  Not a valid choice.")

    spec = PROVIDERS[tag]
    base_url = spec["base_url"] or _ask(
        "  Base URL (OpenAI-compatible)", existing.get("base_url") or "http://localhost:1234/v1"
    )
    if spec["kind"] == "openai" and not base_url.rstrip("/").endswith("/v1"):
        print("  note: OpenAI-compatible endpoints usually end in /v1")

    api_key = ""
    if spec["key_hint"] is not None:
        api_key = _ask(f"  API key ({spec['key_hint']})", existing.get("api_key", ""))
        if spec["key_required"] and not api_key:
            print("  This provider needs a key.")
            return run_wizard(existing)

    model = _ask("  Model", existing.get("model") or spec["default_model"] or "")
    while not model:
        model = _ask("  Model")

    cfg = {"provider": tag, "api_key": api_key, "model": model, "base_url": base_url.rstrip("/")}
    _save(cfg)
    register_secret(api_key)
    print(f"  Saved to {CONFIG_PATH}\n")
    return cfg


def ensure_config() -> dict:
    cfg = load_config()
    if is_configured(cfg):
        return cfg
    return run_wizard(cfg)


def set_model(model: str) -> dict:
    cfg = _load()
    cfg["model"] = model
    _save(cfg)
    return load_config()
