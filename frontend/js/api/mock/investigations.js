// @ts-check
/**
 * MOCK persistence for contract operations #13-#17 (investigations), backed by localStorage.
 * In real mode the backend stores these (OD-7); the function signatures stay the same.
 */

import { ApiClientError } from "../types.js";
import { loadJson, saveJson } from "../../core/storage.js";
import { delay } from "./fixtures.js";

const KEY = "spherex.mock.investigations.v1";
const all = () => /** @type {any[]} */ (loadJson(KEY, []));

/** #13 */
export async function listInvestigations(opts = {}) {
  await delay(0.4);
  const items = all().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .map(({ investigationId, title, objectId, updatedAt }) => ({ investigationId, title, objectId, updatedAt }));
  const limit = opts.limit ?? 50, offset = opts.offset ?? 0;
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
}

/** #14 @param {{title: string, objectId?: string, notes: any[], bookmarks: any[], history: any[]}} input */
export async function createInvestigation(input) {
  await delay(0.4);
  const now = new Date().toISOString();
  const inv = { investigationId: `mock-inv-${Date.now().toString(36)}`, ...input, createdAt: now, updatedAt: now, messages: [] };
  if (!input.objectId) delete inv.objectId;
  saveJson(KEY, [...all(), inv]);
  return inv;
}

/** #15 */
export async function getInvestigation(/** @type {string} */ id) {
  await delay(0.4);
  const inv = all().find(i => i.investigationId === id);
  if (!inv) throw new ApiClientError("Investigation not found.", { code: "NOT_FOUND" });
  return inv;
}

/** #16 full replace, `messages` ignored */
export async function updateInvestigation(/** @type {string} */ id, /** @type {any} */ input) {
  const cur = await getInvestigation(id);
  const next = { ...cur, ...input, messages: cur.messages, investigationId: id, updatedAt: new Date().toISOString() };
  saveJson(KEY, all().map(i => (i.investigationId === id ? next : i)));
  return next;
}

/** #17 */
export async function deleteInvestigation(/** @type {string} */ id) {
  await delay(0.4);
  saveJson(KEY, all().filter(i => i.investigationId !== id));
}
