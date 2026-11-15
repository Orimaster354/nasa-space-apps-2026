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
    assert any("plain summary" in c for c in r["caveats"])
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
    assert any("plain summary" in c for c in r["caveats"])


def test_per_visitor_allowance_then_automated_summaries(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]), per_client=2)
    for _ in range(2):
        assert not comp.respond("hi", ctx, "c:a")["message"]["content"].startswith(fallback.LABEL)
    r = comp.respond("hi", ctx, "c:a")
    assert len(p.prompts) == 2                                                       # the third message never reached the model
    assert r["message"]["content"].startswith(fallback.LABEL) and any("used your 2 free AI answers" in c for c in r["caveats"])
    assert not comp.respond("hi", ctx, "c:b")["message"]["content"].startswith(fallback.LABEL)   # another visitor is unaffected


def test_global_daily_cap_protects_the_quota(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]), global_daily=1)
    comp.respond("hi", ctx, "c:a")
    r = comp.respond("hi", ctx, "c:z")
    assert len(p.prompts) == 1 and any("needs a rest" in c for c in r["caveats"])


def test_a_general_question_with_nothing_selected_still_gets_an_ai_answer_from_the_guide(env):
    c, *_ = env
    answer = "Blinking flips between two pictures of the same sky. Anything that jumps or flickers is worth a closer look."
    comp, p = companion(c.app.state.services, j(answer, ["G1"]))
    r = comp.respond("What does blinking do?", {}, "c:a")
    assert len(p.prompts) == 1 and answer in r["message"]["content"] and r["references"] == []     # guide facts have no result to point to
    assert "Blinking means flipping" in p.prompts[0]                                                  # the guide reached the model
    assert any("written by an AI" in x for x in r["caveats"]) and comp.limiter.remaining("c:a") == comp.limiter.per_client - 1


def test_the_guide_facts_obey_the_same_numeric_check(env):
    c, *_ = env
    comp, p = companion(c.app.state.services, j("Blinking uses 7 pictures.", ["G1"]))
    r = comp.respond("What does blinking do?", {}, "c:a")
    assert len(p.prompts) == 2 and "7" in p.prompts[1] and r["message"]["content"].startswith(fallback.GENERAL)


def test_nothing_selected_and_no_ai_gives_a_friendly_getting_started_note(env):
    c, *_ = env
    comp = Companion(c.app.state.services, ProviderChain([]), QuotaLimiter())
    r = comp.respond("hello", {}, "c:a")
    text = r["message"]["content"]
    assert text.startswith(fallback.GENERAL) and "A good next step:" in text and r["references"] == []


def test_no_provider_configured_still_answers_from_the_measurements(rich):
    svc, ctx, *_ = rich
    comp = Companion(svc, ProviderChain([]), QuotaLimiter())
    r = comp.respond("hi", ctx, "c:a")
    assert r["message"]["content"].startswith(fallback.LABEL) and any("No AI provider is configured" in c for c in r["caveats"])
    assert comp.limiter.remaining("c:a") == comp.limiter.per_client


def test_unavailable_candidates_and_missing_items_are_stated_not_invented(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("I cannot say.", ["F0"]))
    r = comp.respond("Tell me about this candidate", {**ctx, "candidateId": "cand_1", "measurementIds": ["meas_nope"]}, "c:a")
    assert "cand_1" not in p.prompts[0] and "Candidate" not in p.prompts[0].split("Measurement")[0].split('"facts"')[1][-300:]       # nothing about it is invented
    assert any("could not be found: meas_nope" in c and "the selected candidate" in c for c in r["caveats"])


def test_the_fallback_is_plain_language_not_a_data_dump(rich):
    svc, ctx, m, *_ = rich
    text = fallback.summarize(facts.build(svc, ctx))
    assert text.startswith(fallback.LABEL) and f"{m['value']:.4g}" in text and "A good next step:" in text
    assert "You're looking at Synthetic Test Field" in text and "A picture taken on 5 May 2025 in the SPHEREx-D3 band" in text
    assert "That is 321.6 times its uncertainty" in text or "times its uncertainty, so it is a clear signal" in text
    for jargon in ("SUR_ERROR", "Method:", "Circular aperture", "F0", "meas_", "obs_", "JSON"):
        assert jargon not in text, jargon                                                             # no identifiers, flag codes or method dumps
    assert "SPHEREx's SPHEREx" not in text and "2025-05-05T" not in text                              # no repeated names, no raw timestamps
    one_obs = facts.build(svc, {"objectId": ctx["objectId"], "observationIds": ctx["observationIds"][:1]})
    assert "A good next step: select a second observation" in fallback.summarize(one_obs)
    two_obs = facts.build(svc, {"objectId": ctx["objectId"], "observationIds": ctx["observationIds"]})
    assert "A good next step: compare two of the selected observations" in fallback.summarize(two_obs)



def test_a_weak_measurement_is_described_as_possibly_noise_and_a_strong_change_is_not_called_proof():
    from ai.facts import Fact
    weak = {"kind": "aperture_flux", "value": 3.2, "unit": "mJy", "uncertainty": 4.1, "signalToNoise": 0.8, "method": "x"}
    class S:
        def get_measurement(self, mid): return weak
        def get_object(self, i): raise AssertionError
    plain = facts.build(S(), {"measurementIds": ["meas_1"]}).facts[-1].plain
    assert "only 0.8 times its uncertainty" in plain and "could easily be noise" in plain
    strong = {**weak, "kind": "flux_change", "value": 50.0, "uncertainty": 2.0, "signalToNoise": 25.0}
    class T(S):
        def get_measurement(self, mid): return strong
    plain = facts.build(T(), {"measurementIds": ["meas_1"]}).facts[-1].plain
    assert "stands out" in plain and "does not prove" in plain and "discover" not in plain.lower() and "confirm" not in plain.lower()


def test_the_system_prompt_asks_for_a_warm_beginner_friendly_voice(rich):
    svc, ctx, *_ = rich
    comp, p = companion(svc, j("Fine.", ["F0"]))
    comp.respond("hi", ctx, "c:a")
    sp = p.systems[0].lower()
    for phrase in ("never sarcastic", "plain everyday words", "explain it in a few words", "one small thing to try next", "you cannot see images",
                   "for general questions", "return an empty hypotheses list", 'a wavelength check that is "warn"',
                   "matched in wavelength", "consistent with no change"):
        assert phrase in sp, phrase
    assert "beginner" in json.loads(p.prompts[0])["style"]


def test_a_provider_rate_limit_pauses_the_ai_for_everyone_then_recovers(rich):
    from ai.llm import RateLimited
    svc, ctx, *_ = rich
    t = [1_700_000_000.0]

    class Limited:
        name = "fake"
        def __init__(self): self.calls = 0
        def generate(self, system, user):
            self.calls += 1
            if self.calls == 1:
                raise RateLimited("gemini returned HTTP 429 (quota or rate limit)")
            return j("Back in business.", ["F0"])

    p = Limited()
    comp = Companion(svc, ProviderChain([p]), QuotaLimiter(clock=lambda: t[0], sleep=lambda s: None), rush_wait_s=0.0, cooldown_s=45.0)
    first = comp.respond("hi", ctx, "c:a")
    assert first["message"]["content"].startswith(fallback.LABEL) and p.calls == 1
    second = comp.respond("hi", ctx, "c:b")                                                            # a different visitor, but the AI is cooling down
    assert p.calls == 1 and any("catching its breath" in c for c in second["caveats"])
    t[0] += 50                                                                                          # the cool-down is over
    third = comp.respond("hi", ctx, "c:b")
    assert p.calls == 2 and "Back in business." in third["message"]["content"]


# ---- quota limiter on its own -------------------------------------------------------------------------------------------
def test_limiter_resets_each_day_and_after_the_client_window():
    t = [1_700_000_000.0]
    lim = QuotaLimiter(per_client=2, global_daily=3, clock=lambda: t[0], client_ttl_s=3600)
    assert lim.try_acquire("a")[0] and lim.try_acquire("a")[0] and not lim.try_acquire("a")[0]
    assert lim.try_acquire("b")[0] and not lim.try_acquire("c")[0]                    # global cap of 3 reached
    t[0] += 86400 + 10                                                                # next UTC day: global resets, "a" window expired
    assert lim.try_acquire("a")[0] and lim.remaining("a") == 1


def test_the_site_wide_per_minute_cap_makes_a_rush_wait_instead_of_failing():
    t = [1_700_000_000.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    lim = QuotaLimiter(per_client=100, global_daily=100, per_minute=3, clock=lambda: t[0], sleep=sleep)
    assert all(lim.try_acquire(f"v{i}")[0] for i in range(3))
    ok, why = lim.try_acquire("v9")                                                                     # no waiting allowed: refused kindly
    assert not ok and "few seconds" in why and not slept
    ok, why = lim.try_acquire("v9", wait_s=70)                                                          # willing to wait: gets a slot when the minute rolls over
    assert ok and why is None and sum(slept) >= 59
    assert lim.try_acquire("a1")[0] and lim.try_acquire("a2")[0]                                       # the new minute's window is now full too
    ok, why = lim.try_acquire("v10", wait_s=2)                                                          # not willing to wait long enough
    assert not ok and "few seconds" in why


def test_cool_down_blocks_everyone_until_it_ends():
    t = [1_700_000_000.0]
    lim = QuotaLimiter(clock=lambda: t[0], sleep=lambda s: None)
    lim.cool_down(30)
    ok, why = lim.try_acquire("a")
    assert not ok and "catching its breath" in why
    t[0] += 31
    assert lim.try_acquire("a")[0]


def test_defaults_are_generous_for_visitors_and_protective_for_the_provider():
    lim = QuotaLimiter()
    assert (lim.per_client, lim.global_daily, lim.per_minute) == (60, 1500, 10)


def test_the_softwares_notes_about_a_measurement_reach_the_model(env):
    c, *_ = env
    from tests.test_api import _observations
    _, obs = _observations(c)
    region = {"center": {"raDeg": 133.7959, "decDeg": -7.2446}, "radiusArcsec": 20}
    m = c.post("/api/v1/measurements", json={"kind": "flux_change", "observationIds": [obs[0]["observationId"], obs[1]["observationId"]], "region": region}).json()
    pack = facts.build(c.app.state.services, {"measurementIds": [m["measurementId"]]})
    text = next(f.text for f in pack.facts if f.target_type == "measurement")
    assert "Notes from the software:" in text and "times its uncertainty" in text
    assert "Base frame:" not in text and "Target frame:" not in text               # per-frame housekeeping notes stay out of the prompt


# ---- candidates and saved investigations (P1-10, P1-11, P1-12) -------------------------------------------------------------------
def test_a_saved_investigations_notes_reach_the_companion_as_the_users_own_words(env):
    c, *_ = env
    svc = c.app.state.services
    oid = _object(c)["objectId"]
    inv = svc.investigations.create("c:visitor-aaaaaaaa", {"title": "First look", "objectId": oid, "bookmarks": [],
                                                           "notes": [{"noteId": "n1", "text": "The star sits left of centre on the April frame.", "createdAt": "2026-11-15T10:00:00Z"},
                                                                     {"noteId": "n2", "text": "Check the 1.7 micrometre frames next.", "createdAt": "2026-11-15T10:05:00Z"}],
                                                           "history": [{"eventId": "e1", "type": "object_opened", "at": "2026-11-15T09:59:00Z"}]})
    comp, p = companion(svc, j("You noted that the star sits left of centre, and planned to check the 1.7 micrometre frames.", ["F1", "F2", "F3", "F4"]))
    r = comp.respond("Summarise my investigation", {"objectId": oid, "mode": "beginner"}, "c:visitor-aaaaaaaa", inv["investigationId"])
    prompt = p.prompts[0]
    assert "WRITTEN BY THE USER" in prompt and "sits left of centre" in prompt and "First look" in prompt and "2 note(s)" in prompt
    assert "you noted" in p.systems[0].lower() and "not a measurement" in p.systems[0].lower()
    assert "you noted that the star" in r["message"]["content"].lower()
    assert all(ref["targetType"] in ("observation", "comparison", "spectrum", "measurement", "object", "candidate") for ref in r["references"])   # contract types only
    other = comp.respond("Summarise my investigation", {"objectId": oid}, "c:someone-else-xxxx", inv["investigationId"])
    assert "sits left of centre" not in p.prompts[-1]                                                  # another visitor's notes are never shown


def test_the_fallback_summary_includes_the_users_notes(env):
    c, *_ = env
    svc = c.app.state.services
    inv = svc.investigations.create("c:v-aaaaaaaaa", {"title": "Mine", "notes": [{"noteId": "n1", "text": "A faint source near the edge.", "createdAt": "2026-11-15T10:00:00Z"}]})
    comp = Companion(svc, ProviderChain([]), QuotaLimiter())
    text = comp.respond("Summarise my investigation", {}, "c:v-aaaaaaaaa", inv["investigationId"])["message"]["content"]
    assert "You noted:" in text and "A faint source near the edge." in text and "Your saved investigation 'Mine'" in text


def test_the_prompt_forbids_the_discovery_words_even_in_denials_and_says_what_to_say_instead():
    """Real model runs showed answers like 'we cannot confirm' or 'a discovery would need ...' being rejected by the wording check twice and replaced by
    the generic fallback on the most predictable questions. The model is told never to write those words at all."""
    from ai import companion
    p = companion.SYSTEM_PROMPT
    for w in ("discover", "discovery", "confirm", "confirmed", "confirming", "prove", "proven"):
        assert w in p.split("5.")[1].split("6.")[0]
    assert "not even to deny them" in p and "No, nothing new has been established." in p
