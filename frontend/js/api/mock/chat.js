// @ts-check
/**
 * MOCK AI companion. Scripted answers, no LLM. Returns a contract `AIResponse`
 * (message + references + caveats + suggestedActions + availability). The real
 * content comes from the backend; the browser never calls an LLM.
 */

import { ApiClientError } from "../types.js";
import { loadFixture, delay, hash } from "./fixtures.js";

/**
 * @param {{ investigationId?: string, message: string, context: Record<string, any> }} req
 * @param {{ mode?: "beginner"|"advanced" }} [opts]  `mode` is a PROPOSED hint (not in contract 0.1.0).
 */
export async function sendChatMessage(req, opts = {}) {
  await delay(4);
  const text = (req.message ?? "").trim();
  if (!text) throw new ApiClientError("Type a question first.", { code: "INVALID_REQUEST" });
  // Test hook so the error state can be demonstrated on demand.
  if (/simulate error/i.test(text)) throw new ApiClientError("The companion is unavailable right now. Please try again.", { code: "AI_UNAVAILABLE", retryable: true });

  const { chat, candidates } = await loadFixture("proposed.json");
  const mode = opts.mode ?? "beginner";
  const lower = text.toLowerCase();
  const rule = chat.rules.find((/** @type {any} */ r) => r.keywords.some((/** @type {string} */ k) => lower.includes(k))) ?? chat.default;

  const paragraphs = [...rule[mode]];
  const ctx = req.context ?? {};
  const cand = ctx.candidateId && Object.values(candidates).flat().find((/** @type {any} */ c) => c.candidateId === ctx.candidateId);
  if (cand && /candidate|why|listed|what am i/.test(lower)) {
    paragraphs.push(`Selected candidate ${cand.label}: ${cand.description} Its sample ranking score is ${cand.score.toFixed(2)} (${cand.scoreBasis}).`);
  }

  /** @type {Array<{targetType: string, targetId: string}>} */
  const references = [];
  if (ctx.objectId) references.push({ targetType: "object", targetId: ctx.objectId });
  for (const id of ctx.observationIds ?? []) references.push({ targetType: "observation", targetId: id });

  return {
    message: { messageId: `mock-msg-${hash(text + Date.now())}`, role: "assistant", content: paragraphs.join("\n\n"), createdAt: new Date().toISOString() },
    references,
    caveats: ["Illustrative mock reply: no AI model was used.", ...rule.caveats],
    suggestedActions: rule.actions,
    availability: { status: "available" },
  };
}

/** PROPOSED (not in contract): contextual suggested questions for a page. */
export async function getChatSuggestions(/** @type {string} */ view) {
  const { chat } = await loadFixture("proposed.json");
  return chat.suggestions[view] ?? chat.suggestions.default;
}
