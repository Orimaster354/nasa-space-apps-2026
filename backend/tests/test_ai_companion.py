"""The companion end to end with a scripted fake model: grounding, retry, fallback, quotas, caveats, text-only prompts."""
import json

import pytest

from ai import facts, fallback
from ai.companion import Companion
from ai.limits import QuotaLimiter
from ai.llm import LLMError, ProviderChain
from tests.conftest import CENTER
from tests.test_api import STAR, env, _object                                     # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


class Scripted:
    """Fake provider: returns scripted replies in order (a callable gets the user prompt) and records every prompt it was sent."""

    name = "fake"

    def __init__(self, *replies, error=None):
        self.replies, self.error, self.prompts, self.systems = list(replies), error, [], []

    def generate(self, system, user):
        self.systems.append(system)
        self.prompts.append(user)
        if self.error:
            raise LLMError(self.error)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return r(user) if callable(r) else r


def j(answer, cited, hyp=()):
    return json.dumps({"answer": answer, "citedFactIds": cited, "hypotheses": list(hyp)})


@pytest.fixture
def rich(env):
    c, cut, _ = env
    oid = _object(c)["objectId"]
    ids = [i["observationId"] for i in c.get(f"/api/v1/objects/{oid}/observations").json()["items"]]
    m = c.post("/api/v1/measurements", json={"kind": "aperture_flux", "observationIds": [ids[0]],
                                             "region": {"center": {"raDeg": STAR[0], "decDeg": STAR[1]}, "radiusArcsec": 20}}).json()
    cmp_ = c.post("/api/v1/comparisons", json={"baseObservationId": ids[0], "targetObservationId": ids[1], "mode": "difference"}).json()
    ctx = {"objectId": oid, "observationIds": ids[:2], "comparisonId": cmp_["comparisonId"], "measurementIds": [m["measurementId"]], "mode": "beginner"}
    return c.app.state.services, ctx, m, cmp_, ids


def companion(services, *replies, error=None, per_client=15, global_daily=500):
    p = Scripted(*replies, error=error)
    return Companion(services, ProviderChain([p]), QuotaLimiter(per_client, global_daily)), p


def fact_id(services, ctx, target_type):
    return next(f.id for f in facts.build(services, ctx).facts if f.target_type == target_type)


def test_a_grounded_answer_is_accepted_with_references_caveats_and_actions(rich):
    svc, ctx, m, cmp_, ids = rich
    fid = fact_id(svc, ctx, "measurement")
    answer = f"The source measures {m['value']:.3g} mJy, give or take {m['uncertainty']:.2g}, so it is clearly above its noise."
    comp, p = companion(svc, j(answer, [fid], ["The source may simply be bright at this wavelength."]))
    r = comp.respond("How bright is it?", ctx, "c:one")
    text = r["message"]["content"]
    assert answer in text and "Possible explanations (hypotheses, not established findings)" in text and "bright at this wavelength" in text
    assert r["message"]["role"] == "assistant" and r["message"]["messageId"].startswith("msg_") and r["availability"] == {"status": "available"}
    assert {"targetType": "measurement", "targetId": m["measurementId"]} in r["references"]
    assert all(ref["targetType"] != "object" for ref in r["references"])          # references are what the model cited, not everything
    assert any("written by an AI" in c for c in r["caveats"])
    assert any("difference image shows noise" in c for c in r["caveats"])           # backend caveat the model never mentioned
    assert {a["kind"] for a in r["suggestedActions"]} >= {"open_comparison", "open_observation", "add_note"}
    assert len(p.prompts) == 1


def test_the_prompt_is_text_only_structured_facts_and_nothing_else(rich):
    svc, ctx, m, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]))
    comp.respond("How bright is it?", ctx, "c:two")
    payload = json.loads(p.prompts[0])
    assert set(payload) == {"mode", "style", "question", "facts"} and payload["question"] == "How bright is it?"
    assert all(set(f) == {"id", "text"} and isinstance(f["text"], str) for f in payload["facts"])
    blob = p.prompts[0]
    assert f"{m['value']:.4g}" in blob and "Method: Circular aperture" in blob          # the measurement itself reaches the model
    assert "base64" not in blob.lower() and "imageUrl" not in blob and "png" not in blob.lower()
    assert payload["mode"] == "beginner" and "you cannot see images" in p.systems[0].lower()


def test_mode_changes_the_requested_tone(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]))
    comp.respond("hi", {**ctx, "mode": "advanced"}, "c:three")
    assert json.loads(p.prompts[0])["mode"] == "advanced" and "experienced" in json.loads(p.prompts[0])["style"]


def test_an_invented_number_triggers_one_retry_that_quotes_the_problem(rich):
    svc, ctx, m, *_ = rich
    fid = fact_id(svc, ctx, "measurement")
    good = f"The source measures {m['value']:.3g} mJy."
    comp, p = companion(svc, j("The source measures 999.7 mJy.", [fid]), j(good, [fid]))
    r = comp.respond("How bright?", ctx, "c:four")
    assert len(p.prompts) == 2 and "REJECTED" in p.prompts[1] and "999.7" in p.prompts[1]
    assert good in r["message"]["content"] and not r["message"]["content"].startswith(fallback.LABEL)


def test_two_bad_answers_fall_back_to_the_automated_summary(rich):
    svc, ctx, *_ = rich
    fid = fact_id(svc, ctx, "measurement")
    comp, p = companion(svc, j("It is 999.7 mJy.", [fid]))
    r = comp.respond("How bright?", ctx, "c:five")
    assert len(p.prompts) == 2
    assert r["message"]["content"].startswith(fallback.LABEL) and "999.7" not in r["message"]["content"]
    assert any("automated summary" in c for c in r["caveats"])
    assert r["availability"] == {"status": "available"}


def test_discovery_language_is_rejected_and_not_shown(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("This confirms a new planet.", ["F0"]))
    r = comp.respond("What is it?", ctx, "c:six")
    assert "confirms a new planet" not in r["message"]["content"] and r["message"]["content"].startswith(fallback.LABEL)


def test_provider_failure_falls_back_without_leaking_details(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, error="HTTP 503 with secret details")
    r = comp.respond("hi", ctx, "c:seven")
    assert r["message"]["content"].startswith(fallback.LABEL) and "secret details" not in json.dumps(r)
    assert any("automated summary" in c for c in r["caveats"])


def test_per_visitor_allowance_then_automated_summaries(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]), per_client=2)
    for _ in range(2):
        assert not comp.respond("hi", ctx, "c:a")["message"]["content"].startswith(fallback.LABEL)
    r = comp.respond("hi", ctx, "c:a")
    assert len(p.prompts) == 2                                                       # the third message never reached the model
    assert r["message"]["content"].startswith(fallback.LABEL) and any("used your 2 AI messages" in c for c in r["caveats"])
    assert not comp.respond("hi", ctx, "c:b")["message"]["content"].startswith(fallback.LABEL)   # another visitor is unaffected


def test_global_daily_cap_protects_the_quota(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]), global_daily=1)
    comp.respond("hi", ctx, "c:a")
    r = comp.respond("hi", ctx, "c:z")
    assert len(p.prompts) == 1 and any("daily AI message allowance" in c for c in r["caveats"])


def test_nothing_selected_costs_no_ai_message(env):
    c, *_ = env
    comp, p = companion(c.app.state.services, j("x", ["F0"]), per_client=1)
    r = comp.respond("hello", {}, "c:a")
    assert not p.prompts and "Next step: Open an object" in r["message"]["content"] and r["references"] == []
    assert comp.limiter.remaining("c:a") == 1


def test_no_provider_configured_still_answers_from_the_measurements(rich):
    svc, ctx, *_ = rich
    comp = Companion(svc, ProviderChain([]), QuotaLimiter())
    r = comp.respond("hi", ctx, "c:a")
    assert r["message"]["content"].startswith(fallback.LABEL) and any("No AI provider is configured" in c for c in r["caveats"])
    assert comp.limiter.remaining("c:a") == 15


def test_unavailable_candidates_and_missing_items_are_stated_not_invented(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("I cannot say.", ["F0"]))
    r = comp.respond("Tell me about this candidate", {**ctx, "candidateId": "cand_1", "measurementIds": ["meas_nope"]}, "c:a")
    assert "Candidate details are not available" in p.prompts[0]
    assert any("could not be found: meas_nope" in c for c in r["caveats"])


def test_automated_summary_restates_the_facts_and_a_next_step(rich):
    svc, ctx, m, *_ = rich
    text = fallback.summarize(facts.build(svc, ctx))
    assert text.startswith(fallback.LABEL) and f"{m['value']:.4g}" in text and "Next step:" in text
    one_obs = facts.build(svc, {"objectId": ctx["objectId"], "observationIds": ctx["observationIds"][:1]})
    assert "Next step: Select a second observation" in fallback.summarize(one_obs)
    two_obs = facts.build(svc, {"objectId": ctx["objectId"], "observationIds": ctx["observationIds"]})
    assert "Next step: Compare two of the selected observations" in fallback.summarize(two_obs)


# ---- quota limiter on its own -------------------------------------------------------------------------------------------
def test_limiter_resets_each_day_and_after_the_client_window():
    t = [1_700_000_000.0]
    lim = QuotaLimiter(per_client=2, global_daily=3, clock=lambda: t[0], client_ttl_s=3600)
    assert lim.try_acquire("a")[0] and lim.try_acquire("a")[0] and not lim.try_acquire("a")[0]
    assert lim.try_acquire("b")[0] and not lim.try_acquire("c")[0]                    # global cap of 3 reached
    t[0] += 86400 + 10                                                                # next UTC day: global resets, "a" window expired
    assert lim.try_acquire("a")[0] and lim.remaining("a") == 1
