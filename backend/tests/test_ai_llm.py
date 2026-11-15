"""Provider adapters: text-only requests, keys only in headers, error mapping, ordered fallback, configuration."""
import json

import httpx
import pytest
from pydantic import SecretStr

from ai.llm import GeminiProvider, LLMError, LLMUnavailable, OpenAICompatibleProvider, ProviderChain, providers_from_settings
from app.config import Settings

pytestmark = pytest.mark.synthetic

KEY = "SECRET-KEY-123456"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _all_keys(obj, found=None):
    found = [] if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.append(k)
            _all_keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, found)
    return found


def test_gemini_request_is_text_only_and_key_stays_in_the_header():
    g = GeminiProvider(KEY, "some-model")
    url, headers, body = g.build_request("SYSTEM", "USER")
    assert KEY not in url and headers["x-goog-api-key"] == KEY
    assert url.endswith("/models/some-model:generateContent")
    parts = [p for c in body["contents"] for p in c["parts"]] + body["systemInstruction"]["parts"]
    assert parts and all(set(p) == {"text"} for p in parts)                          # only text parts: no inlineData, no fileData
    keys = {k.lower() for k in _all_keys(body)}
    assert not keys & {"inlinedata", "inline_data", "filedata", "file_data", "image", "image_url", "images"}
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_openai_compatible_request_is_text_only():
    p = OpenAICompatibleProvider(KEY, "m", "https://example.test/v1/")
    url, headers, body = p.build_request("SYSTEM", "USER")
    assert url == "https://example.test/v1/chat/completions" and KEY not in url and KEY in headers["Authorization"]
    assert all(isinstance(m["content"], str) for m in body["messages"])


def test_gemini_success_and_error_mapping():
    ok = lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"answer": "hi"}'}]}}]})
    assert GeminiProvider(KEY, "m", client=_client(ok)).generate("s", "u") == '{"answer": "hi"}'
    quota = lambda req: httpx.Response(429, json={"error": {"message": f"key {KEY} exceeded"}})
    with pytest.raises(LLMError) as e:
        GeminiProvider(KEY, "m", client=_client(quota)).generate("s", "u")
    assert "429" in str(e.value) and KEY not in str(e.value)                          # upstream body (which may echo a key) is not copied
    weird = lambda req: httpx.Response(200, json={"unexpected": True})
    with pytest.raises(LLMError, match="unexpected response shape"):
        GeminiProvider(KEY, "m", client=_client(weird)).generate("s", "u")

    def boom(req):
        raise httpx.ConnectTimeout(f"timeout talking with {KEY}")
    with pytest.raises(LLMError) as e2:
        GeminiProvider(KEY, "m", client=_client(boom)).generate("s", "u")
    assert KEY not in str(e2.value) and "ConnectTimeout" in str(e2.value)


def test_openai_compatible_success():
    ok = lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    assert OpenAICompatibleProvider(KEY, "m", "https://x.test/v1", client=_client(ok)).generate("s", "u") == "{}"


class Fake:
    def __init__(self, name, reply=None, error=None):
        self.name, self.reply, self.error, self.calls = name, reply, error, 0

    def generate(self, system, user):
        self.calls += 1
        if self.error:
            raise LLMError(self.error)
        return self.reply


def test_chain_falls_through_in_order_and_reports_the_provider_used():
    a, b = Fake("a", error="down"), Fake("b", reply="B")
    assert ProviderChain([a, b]).generate("s", "u") == ("B", "b") and (a.calls, b.calls) == (1, 1)
    first = Fake("a", reply="A")
    second = Fake("b", reply="B")
    assert ProviderChain([first, second]).generate("s", "u") == ("A", "a") and second.calls == 0


def test_chain_raises_when_all_fail_or_none_configured():
    with pytest.raises(LLMUnavailable):
        ProviderChain([Fake("a", error="x"), Fake("b", error="y")]).generate("s", "u")
    empty = ProviderChain([])
    assert not empty.configured
    with pytest.raises(LLMUnavailable):
        empty.generate("s", "u")


def test_providers_come_from_settings_and_blank_ones_are_skipped():
    none = Settings(_env_file=None)
    assert providers_from_settings(none) == []
    s = Settings(_env_file=None, llm_provider_1="gemini", llm_api_key_1=SecretStr(KEY), llm_model_1="m1",
                 llm_provider_2="openai_compatible", llm_api_key_2=SecretStr("k2"), llm_model_2="m2", llm_base_url_2="https://x.test/v1")
    ps = providers_from_settings(s)
    assert [p.name for p in ps] == ["gemini", "openai_compatible"]
    three = Settings(_env_file=None, llm_provider_1="gemini", llm_api_key_1=SecretStr(KEY), llm_model_1="a",
                     llm_provider_2="gemini", llm_api_key_2=SecretStr(KEY), llm_model_2="b",
                     llm_provider_3="gemini", llm_api_key_3=SecretStr(KEY), llm_model_3="c")
    assert [p.model for p in providers_from_settings(three)] == ["a", "b", "c"]                      # one key can serve three models, in order
    missing_model = Settings(_env_file=None, llm_provider_1="gemini", llm_api_key_1=SecretStr(KEY))
    assert providers_from_settings(missing_model) == []


def test_the_key_never_appears_in_settings_repr():
    s = Settings(_env_file=None, llm_provider_1="gemini", llm_api_key_1=SecretStr(KEY), llm_model_1="m")
    assert KEY not in repr(s) and KEY not in str(s) and KEY not in s.model_dump_json()


def test_the_default_timeout_is_short_so_a_slow_model_fails_over_quickly():
    assert Settings(_env_file=None).llm_timeout_s == 12.0 and GeminiProvider("k", "m")._client.timeout.read == 12.0
    chain = ProviderChain([Fake("slow", error="gemini request failed (ReadTimeout)"), Fake("fast", reply="OK")])
    assert chain.generate("s", "u") == ("OK", "fast")


def test_the_test_environment_cannot_reach_a_real_provider():
    """Guards the hermetic setup in conftest.py: no provider slot may be configured while tests run, whatever backend/.env contains."""
    from app.config import settings
    for i in (1, 2, 3, 4):
        assert not getattr(settings, f"llm_provider_{i}", "") and not getattr(settings, f"llm_model_{i}", "")
        key = getattr(settings, f"llm_api_key_{i}", None)
        assert key is None or not key.get_secret_value()


def test_the_chain_stops_trying_once_its_time_budget_is_spent():
    t = [0.0]

    class SlowFail:
        def __init__(self, name): self.name, self.calls = name, 0
        def generate(self, system, user):
            self.calls += 1
            t[0] += 12.0                                                              # each attempt burns its full timeout
            raise LLMError("gemini request failed (ReadTimeout)")

    a, b, c = SlowFail("a"), SlowFail("b"), SlowFail("c")
    with pytest.raises(LLMUnavailable):
        ProviderChain([a, b, c], max_total_s=20.0, clock=lambda: t[0]).generate("s", "u")
    assert (a.calls, b.calls, c.calls) == (1, 1, 0)                                   # 12 s, 24 s: over budget, so the third is skipped
    ok = Fake("ok", reply="fine")
    assert ProviderChain([ok], max_total_s=20.0, clock=lambda: t[0]).generate("s", "u") == ("fine", "ok")
