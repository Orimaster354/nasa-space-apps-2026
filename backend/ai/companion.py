"""The AI companion: builds evidence, asks the LLM to interpret it, checks the answer, and falls back to a deterministic summary.

Flow for one message:
  1. Build numbered facts from stored results (ai.facts). No data selected -> no LLM call, a short "select something" reply.
  2. Per-visitor and global quotas (ai.limits). Over quota -> automated summary, no LLM call.
  3. Ask the provider chain for JSON {answer, citedFactIds, hypotheses}. Validate it (ai.grounding). On failure retry ONCE with the
     problems quoted back, then fall back to the automated summary.
  4. Caveats come from the backend's own warnings, never only from the model. References come from the facts the model cited.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from . import fallback, facts, grounding
from .limits import QuotaLimiter
from .llm import LLMUnavailable, ProviderChain

log = logging.getLogger("spx.ai")

SYSTEM_PROMPT = """You are the astronomy companion in a web tool that shows SPHEREx infrared sky images. Your job is to INTERPRET measurements that our software has already computed. You do not measure, calculate or look at images.

Hard rules:
1. Use ONLY the FACTS provided. Never invent a measurement, date, identifier or any other number. If the facts cannot answer the question, say what is missing and which measurement or comparison would provide it.
2. Write every quantity as digits, copied exactly from a fact (you may round it). Never do arithmetic to create a new number. Do not write numbers as words.
3. List in citedFactIds the ID of every fact you used. Never mention fact IDs in the answer text.
4. Separate evidence from speculation. State what the measurements show in `answer`. Put possible explanations in `hypotheses`, each phrased as a possibility, never as a finding.
5. Never claim a discovery, confirmation or proof. A change is only worth noting if it is large compared with its uncertainty; say when it is not. Do not treat a warning or a check that is not "pass" as if it passed.
6. SPHEREx wavelength matters: frames of the same band can sample different wavelengths, so a brightness difference may be spectral rather than a change over time. Mention this when the facts show it.
7. Do not describe what an image looks like. You cannot see images.
8. Be concise (under 150 words in `answer`). Suggest one sensible next step for the user to try.

Reply with ONE JSON object and nothing else: {"answer": "<text>", "citedFactIds": ["F1"], "hypotheses": ["<possibility>", ...]}"""

_TONE = {
    "beginner": "The user is a beginner: use plain language, define any technical term in a few words, and keep it friendly and encouraging.",
    "advanced": "The user is experienced: be precise and compact, and use standard astronomy terms.",
}


def build_user_prompt(message: str, mode: str, pack: facts.EvidencePack) -> str:
    payload = {"mode": mode, "style": _TONE.get(mode, _TONE["beginner"]), "question": message,
               "facts": [{"id": f.id, "text": f.text} for f in pack.facts]}
    return json.dumps(payload, ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Companion:
    def __init__(self, services, chain: ProviderChain, limiter: QuotaLimiter):
        self.services, self.chain, self.limiter = services, chain, limiter

    # ---- public ---------------------------------------------------------------------------------------------------
    def respond(self, message: str, context: dict | None, client_id: str, investigation_id: str | None = None) -> dict:
        ctx = context or {}
        mode = ctx.get("mode") if ctx.get("mode") in _TONE else "beginner"
        pack = facts.build(self.services, ctx)
        caveats = pack.caveats()
        content, cited, outcome = None, [], ""

        if not pack.has_data:
            content = fallback.summarize(pack)
            outcome = "no_data"
        elif not self.chain.configured:
            outcome, note = "no_provider", "No AI provider is configured, so this is an automated summary."
            caveats.insert(0, note)
        else:
            ok, why = self.limiter.try_acquire(client_id)
            if not ok:
                outcome = "quota"
                caveats.insert(0, f"{why} Showing an automated summary built from the measurements instead.")
            else:
                result, outcome = self._ask(message, mode, pack)
                if result:
                    content, cited = self._compose(result), result.cited
                    caveats.append("This explanation was written by an AI from the measurements listed; it is not a scientific finding.")
                else:
                    caveats.insert(0, "The AI answer could not be used (it was unavailable or failed the check against the measurements), "
                                      "so this is an automated summary instead.")
        if content is None:
            content = fallback.summarize(pack)
        log.info("chat outcome=%s mode=%s facts=%d cited=%d", outcome, mode, len(pack.facts), len(cited))

        by_id = {f.id: f for f in pack.facts}
        refs, seen = [], set()
        for fid in (cited or [f.id for f in pack.facts if f.target_type]):
            f = by_id.get(fid)
            if f and f.target_type and (f.target_type, f.target_id) not in seen:
                seen.add((f.target_type, f.target_id))
                refs.append({"targetType": f.target_type, "targetId": f.target_id})
        return {
            "message": {"messageId": "msg_" + uuid.uuid4().hex[:10], "role": "assistant", "content": content, "createdAt": _now()},
            "references": refs, "caveats": caveats[:facts.MAX_CAVEATS + 2], "suggestedActions": self._actions(pack),
            "availability": {"status": "available"},
        }

    # ---- internals ------------------------------------------------------------------------------------------------
    def _ask(self, message: str, mode: str, pack: facts.EvidencePack):
        user = build_user_prompt(message, mode, pack)
        texts, numbers = pack.texts(), pack.numbers()
        problems: list[str] = []
        for attempt in (1, 2):
            prompt = user if attempt == 1 else user + "\n\nYOUR PREVIOUS REPLY WAS REJECTED. Fix these problems and reply again with the JSON only:\n- " + "\n- ".join(problems)
            try:
                raw, provider = self.chain.generate(SYSTEM_PROMPT, prompt)
            except LLMUnavailable:
                return None, "llm_unavailable"
            verdict = grounding.validate(raw, texts, numbers)
            if verdict.ok:
                return verdict.parsed, f"llm_ok_attempt{attempt}"
            problems = verdict.problems
            log.warning("AI answer rejected (attempt %d, provider %s): %s", attempt, provider, "; ".join(problems)[:300])
        return None, "rejected"

    @staticmethod
    def _compose(p: grounding.Parsed) -> str:
        text = p.answer
        if p.hypotheses:
            text += "\n\nPossible explanations (hypotheses, not established findings):\n" + "\n".join(f"- {h}" for h in p.hypotheses)
        return text

    @staticmethod
    def _actions(pack: facts.EvidencePack) -> list[dict]:
        out: list[dict] = []
        if pack.comparison_id:
            out.append({"kind": "open_comparison", "label": "Open this comparison", "targetId": pack.comparison_id})
        elif len(pack.observation_ids) >= 2:
            out.append({"kind": "run_comparison", "label": "Compare the first two selected observations",
                        "params": {"baseObservationId": pack.observation_ids[0], "targetObservationId": pack.observation_ids[1], "mode": "blink"}})
        if pack.observation_ids:
            out.append({"kind": "open_observation", "label": "Open the selected observation", "targetId": pack.observation_ids[0]})
        if pack.has_data:
            out.append({"kind": "add_note", "label": "Add a note about this"})
        return out
