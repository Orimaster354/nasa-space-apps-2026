"""LLM provider adapters. Providers are configuration, not code: a provider with no key or model is simply not used.

Every request is TEXT ONLY: a system prompt and a user prompt. There is no code path that attaches an image, a file or pixel data
(a test asserts the request bodies contain only text parts), because scientific judgements are made by deterministic code.

Keys travel only in request headers, never in URLs, logs, error messages or responses.
NOT yet verified against a live key (none had been created when this was written); request/response shapes follow the providers'
public documentation and are exercised in tests with a mock transport.
"""
from __future__ import annotations

import logging
import time
from typing import Protocol

import httpx

log = logging.getLogger("spx.ai")


class LLMError(Exception):
    """One provider failed. The message is safe to log (never contains a key or the prompt)."""


class RateLimited(LLMError):
    """The provider answered HTTP 429: too many requests or the free quota is used up."""


class LLMUnavailable(Exception):
    """Every configured provider failed or none is configured."""


class Provider(Protocol):
    name: str

    def generate(self, system: str, user: str) -> str: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: float = 12.0, client: httpx.Client | None = None):
        self._key, self.model = api_key, model
        self._base = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def build_request(self, system: str, user: str) -> tuple[str, dict, dict]:
        url = f"{self._base}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2, "maxOutputTokens": 1200},
        }
        return url, headers, body

    def generate(self, system: str, user: str) -> str:
        url, headers, body = self.build_request(system, user)
        try:
            r = self._client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"gemini request failed ({type(exc).__name__})") from None
        if r.status_code == 429:
            raise RateLimited("gemini returned HTTP 429 (quota or rate limit)")
        if r.status_code != 200:
            raise LLMError(f"gemini returned HTTP {r.status_code}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError, ValueError):
            raise LLMError("gemini returned an unexpected response shape") from None


class OpenAICompatibleProvider:
    """Any service that implements the chat-completions API (for example Groq, OpenRouter, a local server)."""

    name = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 12.0, client: httpx.Client | None = None):
        self._key, self.model, self._base = api_key, model, base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def build_request(self, system: str, user: str) -> tuple[str, dict, dict]:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        body = {"model": self.model, "temperature": 0.2, "max_tokens": 1200, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        return f"{self._base}/chat/completions", headers, body

    def generate(self, system: str, user: str) -> str:
        url, headers, body = self.build_request(system, user)
        try:
            r = self._client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"openai-compatible request failed ({type(exc).__name__})") from None
        if r.status_code == 429:
            raise RateLimited("openai-compatible service returned HTTP 429 (quota or rate limit)")
        if r.status_code != 200:
            raise LLMError(f"openai-compatible service returned HTTP {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise LLMError("openai-compatible service returned an unexpected response shape") from None


class ProviderChain:
    """Try providers in order; the first that returns text wins."""

    def __init__(self, providers: list[Provider], max_total_s: float = 20.0, clock=time.monotonic):
        self.providers = providers
        self.max_total_s, self._clock = max_total_s, clock          # stop trying further providers after this long: a visitor should not stare at a spinner
        self.rate_limited = False               # set when any provider said 429 during generate(); read with take_rate_limited()

    def take_rate_limited(self) -> bool:
        flag, self.rate_limited = self.rate_limited, False
        return flag

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    def generate(self, system: str, user: str) -> tuple[str, str]:
        if not self.providers:
            raise LLMUnavailable("no AI provider is configured")
        started = self._clock()
        for p in self.providers:
            if self._clock() - started > self.max_total_s:
                log.warning("AI chain gave up after %.0f s", self._clock() - started)
                break
            try:
                return p.generate(system, user), p.name
            except RateLimited as exc:
                self.rate_limited = True
                log.warning("AI provider %s failed: %s", p.name, exc)
            except LLMError as exc:
                log.warning("AI provider %s failed: %s", p.name, exc)
        raise LLMUnavailable("every configured AI provider failed")


def providers_from_settings(s) -> list[Provider]:
    out: list[Provider] = []
    for i in (1, 2, 3):
        kind = getattr(s, f"llm_provider_{i}").strip().lower()
        key = getattr(s, f"llm_api_key_{i}").get_secret_value().strip()
        model = getattr(s, f"llm_model_{i}").strip()
        base = getattr(s, f"llm_base_url_{i}").strip()
        if not (kind and key and model):
            continue
        if kind == "gemini":
            out.append(GeminiProvider(key, model, base, s.llm_timeout_s))
        elif kind == "openai_compatible" and base:
            out.append(OpenAICompatibleProvider(key, model, base, s.llm_timeout_s))
        else:
            log.warning("AI provider %d ignored: unknown type '%s' or missing base URL", i, kind)
    return out
