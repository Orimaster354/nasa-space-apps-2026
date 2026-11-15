"""POST /api/v1/chat/messages: contract shape, validation, per-visitor limits over HTTP, and the key never leaking."""
import json
import logging

import httpx
import pytest

from ai.companion import Companion
from ai.fallback import LABEL
from ai.limits import QuotaLimiter
from ai.llm import GeminiProvider, ProviderChain
from tests.test_ai_companion import Scripted, j, rich                          # noqa: F401  (rich is a fixture)
from tests.test_api import _no_nulls, env                                      # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

URL = "/api/v1/chat/messages"


def _install(c, provider, per_client=15, global_daily=500):
    c.app.state.companion = Companion(c.app.state.services, ProviderChain([provider]), QuotaLimiter(per_client, global_daily))


def _post(c, ctx, message="How bright is it?", headers=None):
    return c.post(URL, json={"message": message, "context": ctx}, headers=headers or {})


def test_response_matches_the_contract_shape(rich):
    svc, ctx, m, *_ = rich
    from ai import facts
    from tests.test_ai_companion import fact_id
    from fastapi.testclient import TestClient  # noqa: F401
    c = _client_of(svc)
    _install(c, Scripted(j(f"It measures {m['value']:.3g} mJy.", [fact_id(svc, ctx, "measurement")])))
    r = _post(c, ctx)
    assert r.status_code == 200, r.text
    body = r.json()
    _no_nulls(body)
    assert set(body) == {"message", "references", "caveats", "suggestedActions", "availability"}
    assert body["message"]["role"] == "assistant" and body["availability"]["status"] == "available"
    assert all(set(x) == {"targetType", "targetId"} for x in body["references"])
    assert all("kind" in a and "label" in a for a in body["suggestedActions"])


def _client_of(services):
    from fastapi.testclient import TestClient
    from app.main import create_app
    return TestClient(create_app(services))


def test_validation_errors_use_the_error_envelope(env):
    c, *_ = env
    for body in ({"message": "", "context": {}}, {"message": "x" * 2001, "context": {}}, {"context": {}},
                 {"message": "hi", "context": {"mode": "expert"}}):
        r = c.post(URL, json=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"


def test_with_no_provider_the_default_app_still_answers_from_measurements(rich):
    svc, ctx, m, *_ = rich
    c = _client_of(svc)                                                             # default companion; tests never configure a provider
    assert not c.app.state.companion.chain.configured
    r = _post(c, ctx)
    assert r.status_code == 200
    body = r.json()
    assert body["message"]["content"].startswith(LABEL) and f"{m['value']:.4g}" in body["message"]["content"]
    assert any("No AI provider is configured" in x for x in body["caveats"])


def test_visitor_allowance_is_enforced_per_client_id_over_http(rich):
    svc, ctx, *_ = rich
    c = _client_of(svc)
    p = Scripted(j("Fine.", ["F0"]))
    _install(c, p, per_client=2)
    a, b = {"X-Client-Id": "visitor-aaaaaaaa"}, {"X-Client-Id": "visitor-bbbbbbbb"}
    assert not _post(c, ctx, headers=a).json()["message"]["content"].startswith(LABEL)
    assert not _post(c, ctx, headers=a).json()["message"]["content"].startswith(LABEL)
    third = _post(c, ctx, headers=a).json()
    assert third["message"]["content"].startswith(LABEL) and any("AI messages" in x for x in third["caveats"]) and len(p.prompts) == 2
    assert not _post(c, ctx, headers=b).json()["message"]["content"].startswith(LABEL)          # a different visitor is unaffected


def test_without_a_client_id_the_caller_address_is_the_key_and_junk_ids_are_ignored(rich):
    svc, ctx, *_ = rich
    c = _client_of(svc)
    p = Scripted(j("Fine.", ["F0"]))
    _install(c, p, per_client=1)
    assert not _post(c, ctx).json()["message"]["content"].startswith(LABEL)
    assert _post(c, ctx).json()["message"]["content"].startswith(LABEL)                         # same address, allowance used
    assert _post(c, ctx, headers={"X-Client-Id": "x"}).json()["message"]["content"].startswith(LABEL)   # too short to be an ID: ignored, still the address
    assert not _post(c, ctx, headers={"X-Client-Id": "a-valid-id-12345"}).json()["message"]["content"].startswith(LABEL)


def test_the_api_key_never_appears_in_responses_or_logs(rich, caplog):
    svc, ctx, *_ = rich
    secret = "AIzaSECRET-KEY-do-not-leak"
    boom = lambda req: httpx.Response(500, json={"error": {"message": f"bad key {secret}"}})
    gem = GeminiProvider(secret, "some-model", client=httpx.Client(transport=httpx.MockTransport(boom)))
    c = _client_of(svc)
    _install(c, gem)
    with caplog.at_level(logging.DEBUG):
        r = _post(c, ctx)
    assert r.status_code == 200 and r.json()["message"]["content"].startswith(LABEL)
    assert secret not in r.text and secret not in json.dumps(dict(r.headers)) and secret not in caplog.text
    assert "HTTP 500" in caplog.text                                               # the failure itself is logged, minus the secret
