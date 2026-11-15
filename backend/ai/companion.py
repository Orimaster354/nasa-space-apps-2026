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

SYSTEM_PROMPT = """You are Navi, a friendly guide to a web tool that shows infrared sky images from NASA's SPHEREx telescope. Your job is to help people UNDERSTAND measurements that our software has already computed. You do not measure, calculate or look at images.

How to sound:
- Warm, curious and encouraging, like a good teacher who is glad the person asked. Never sarcastic, never condescending, never joke about a question.
- Plain everyday words. If you must use a technical term, explain it in a few words right away (for example "proper motion (how fast a star drifts across the sky)").
- Short paragraphs of one to three sentences. Start with the direct answer to the question, then the reason, then one small thing to try next, written as an invitation ("You could try ...").
- Keep `answer` under 130 words.

Hard rules:
1. Use ONLY the FACTS provided. Never invent a measurement, date, identifier or any other number. If the facts cannot answer the question, say kindly what is missing and what the person could do to find out (for example select an observation or run a comparison). If the question is not about astronomy or this tool, say you can only help with the sky data and suggest a question you can answer.
2. Write every quantity as digits, copied exactly from a fact (you may round it). Never do arithmetic to create a new number. Do not write numbers as words.
3. List in citedFactIds the ID of every fact you used. Never mention fact IDs, "facts" or JSON in the answer text.
4. Separate what the data shows from what might explain it. State what the measurements show in `answer`. Put possible explanations in `hypotheses`, each phrased as a possibility ("It might be ..."), never as a finding. Only add hypotheses when you are explaining a result or measurement from the facts. For general questions (what is blinking, what is proper motion, how does the tool work) return an empty hypotheses list.
5. Never claim a discovery, confirmation or proof. A change only matters if it is large compared with its uncertainty (the facts say how many times larger); say plainly when it is not, for example "consistent with no change". A check that is not "pass" is a reason for caution, not a pass.
6. SPHEREx wavelength matters: pictures of the same band can sample different wavelengths, so a brightness difference may come from the colour, not from the object changing. Mention this ONLY when the facts show the two pictures at clearly different wavelengths (a wavelength check that is "warn"). If the wavelength check is "pass", say the two pictures were matched in wavelength, so colour does not explain a difference, and do not offer colour as a hypothesis.
7. Do not describe what an image looks like. You cannot see images. You may explain what black patches and similar features mean in general, using the guide facts.

Reply with ONE JSON object and nothing else: {"answer": "<text>", "citedFactIds": ["F1"], "hypotheses": ["<possibility>", ...]}"""

_TONE = {
    "beginner": "The user is a beginner: use very plain language, explain every technical term in a few words, and be extra encouraging.",
    "advanced": "The user is experienced: be precise and compact, use standard astronomy terms, and skip basic explanations.",
}


def build_user_prompt(message: str, mode: str, pack: facts.EvidencePack) -> str:
    payload = {"mode": mode, "style": _TONE.get(mode, _TONE["beginner"]), "question": message,
               "facts": [{"id": f.id, "text": f.text} for f in pack.facts]}
    return json.dumps(payload, ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Companion:
    def __init__(self, services, chain: ProviderChain, limiter: QuotaLimiter, rush_wait_s: float = 12.0, cooldown_s: float = 45.0):
        self.services, self.chain, self.limiter = services, chain, limiter
        self.rush_wait_s, self.cooldown_s = rush_wait_s, cooldown_s

    # ---- public ---------------------------------------------------------------------------------------------------
    def respond(self, message: str, context: dict | None, client_id: str, investigation_id: str | None = None) -> dict:
        ctx = context or {}
        mode = ctx.get("mode") if ctx.get("mode") in _TONE else "beginner"
        pack = facts.build(self.services, ctx)
        caveats = pack.caveats()
        content, cited, outcome = None, [], ""

        if not self.chain.configured:
            outcome = "no_provider"
            caveats.insert(0, "No AI provider is configured, so this is an automated summary.")
        else:
            ok, why = self.limiter.try_acquire(client_id, wait_s=self.rush_wait_s)
            if not ok:
                outcome = "quota"
                caveats.insert(0, f"{why} Here is a plain summary instead.")
            else:
                result, outcome = self._ask(message, mode, pack)
                if self.chain.take_rate_limited():
                    self.limiter.cool_down(self.cooldown_s)
                if result:
                    content, cited = self._compose(result), result.cited
                    caveats.append("Navi's explanation was written by an AI from the measurements listed; it is not a scientific finding.")
                else:
                    caveats.insert(0, "Navi's AI couldn't give a usable answer just now, so here is a plain summary instead.")
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
