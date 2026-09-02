"""
unsql/ai.py
-----------
Provider-agnostic streaming AI client.

Three wire protocols cover every supported provider:
  openai    — OpenAI, OpenRouter, NVIDIA NIM, Ollama, LM Studio, custom
  anthropic — Claude
  gemini    — Google Generative Language API

Everything streams; callers receive plain text deltas.
"""
from __future__ import annotations

import json
from typing import Callable, Iterator

import httpx

from .config import PROVIDERS

TIMEOUT = httpx.Timeout(connect=15.0, read=None, write=60.0, pool=None)


class AIError(RuntimeError):
    pass


def _status_message(status: int, body: str) -> str:
    body = body.strip()[:300]
    if status in (401, 403):
        return f"Auth rejected by the provider ({status}). Run `set apikey;`. {body}"
    if status == 404:
        return f"Model or endpoint not found ({status}). Run `set model;`. {body}"
    if status == 429:
        return f"Rate limited ({status}). Wait a moment and retry. {body}"
    if status == 402:
        return f"Provider billing/credits issue ({status}). {body}"
    return f"AI request failed ({status}). {body}"


def _sse_lines(response: httpx.Response) -> Iterator[str]:
    for raw in response.iter_lines():
        line = raw.strip()
        if line.startswith("data:"):
            yield line[5:].strip()


class AIClient:
    """One client per config; `stream()` yields text deltas."""

    def __init__(self, cfg: dict) -> None:
        self.provider = cfg.get("provider", "openai")
        self.kind = PROVIDERS.get(self.provider, {}).get("kind", "openai")
        self.api_key = cfg.get("api_key", "") or ""
        self.model = cfg.get("model", "")
        self.base_url = (cfg.get("base_url") or "").rstrip("/")

    # ── public ───────────────────────────────────────────────────────────────

    def stream(self, system: str, user: str, max_tokens: int = 16000) -> Iterator[str]:
        if self.kind == "anthropic":
            yield from self._anthropic(system, user, max_tokens)
        elif self.kind == "gemini":
            yield from self._gemini(system, user, max_tokens)
        else:
            yield from self._openai(system, user, max_tokens)

    def complete(
        self,
        system: str,
        user: str,
        on_delta: Callable[[str], None] | None = None,
        max_tokens: int = 16000,
    ) -> str:
        parts: list[str] = []
        for delta in self.stream(system, user, max_tokens):
            parts.append(delta)
            if on_delta:
                on_delta(delta)
        text = "".join(parts).strip()
        if not text:
            raise AIError("The model returned an empty response.")
        return text

    # ── protocols ────────────────────────────────────────────────────────────

    def _openai(self, system: str, user: str, max_tokens: int) -> Iterator[str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=TIMEOUT) as client:
            with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=headers, json=body
            ) as res:
                if res.status_code >= 400:
                    raise AIError(_status_message(res.status_code, res.read().decode("utf-8", "ignore")))
                for payload in _sse_lines(res):
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield text

    def _anthropic(self, system: str, user: str, max_tokens: int) -> Iterator[str]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "stream": True,
        }
        with httpx.Client(timeout=TIMEOUT) as client:
            with client.stream("POST", f"{self.base_url}/messages", headers=headers, json=body) as res:
                if res.status_code >= 400:
                    raise AIError(_status_message(res.status_code, res.read().decode("utf-8", "ignore")))
                for payload in _sse_lines(res):
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("type") == "content_block_delta":
                        text = (chunk.get("delta") or {}).get("text")
                        if isinstance(text, str) and text:
                            yield text

    def _gemini(self, system: str, user: str, max_tokens: int) -> Iterator[str]:
        url = (
            f"{self.base_url}/models/{self.model}:streamGenerateContent"
            f"?alt=sse&key={self.api_key}"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        with httpx.Client(timeout=TIMEOUT) as client:
            with client.stream("POST", url, json=body) as res:
                if res.status_code >= 400:
                    raise AIError(_status_message(res.status_code, res.read().decode("utf-8", "ignore")))
                for payload in _sse_lines(res):
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for cand in chunk.get("candidates", []):
                        for part in (cand.get("content") or {}).get("parts", []):
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                yield text
