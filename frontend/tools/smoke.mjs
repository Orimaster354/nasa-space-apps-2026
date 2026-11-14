// Headless smoke test (no browser): module linking, the mock service layer, store behaviour,
// and the real client's error mapping. Canvas is stubbed, so image PIXELS are not tested here.
// Run: node frontend/tools/smoke.mjs
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const mem = new Map();
globalThis.window = { localStorage: { getItem: k => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) }, location: { origin: "http://localhost" } };
globalThis.fetch = async u => {
  const url = new URL(u);
  if (url.protocol === "file:") return { ok: true, json: async () => JSON.parse(readFileSync(url, "utf8")) };
  throw new TypeError("network down");                                  // real-mode network failure
};
const fakeCtx = (w, h) => ({ fillStyle: "", createRadialGradient: () => ({ addColorStop() {} }), fillRect() {}, getImageData: () => ({ data: new Uint8ClampedArray(w * h * 4) }),
  createImageData: () => ({ data: new Uint8ClampedArray(w * h * 4) }), putImageData() {} });
globalThis.document = { createElement: () => { const c = { width: 0, height: 0, getContext() { return fakeCtx(c.width, c.height); }, toBlob(cb) { cb(new Blob(["x"])); } }; return c; } };

let failed = 0;
const ok = (cond, msg) => { if (!cond) { failed++; console.error("FAIL", msg); } };
const rejects = async (p, code, msg) => { try { await p; ok(false, `${msg}: should reject`); } catch (e) { ok(e.code === code || (code === "NETWORK" && e.network), `${msg}: got ${e.code}`); } };
const imp = rel => import(new URL(`../${rel}`, import.meta.url).href);

const { CONFIG } = await imp("js/config.js"); CONFIG.mockLatencyMs = 0;

// 1. Every module links (catches missing/misnamed exports).
const mods = readdirSync(path.join(root, "js"), { recursive: true }).filter(f => f.endsWith(".js") && !/^(app|landing)\.js$/.test(f));
for (const m of mods) { try { await imp(`js/${m}`); } catch (e) { ok(false, `import ${m}: ${e.message}`); } }

// 2. Mock service layer.
const api = await imp("js/api/mock/mockApi.js");
const names = ["Barnard", "WISE 0855", "RW Aur", "R Dra", "Epsilon Indi B"];
for (const n of names) ok((await api.searchObjects(n)).items.length === 1, `search ${n}`);
ok((await api.resolveObject({ ra: 269.45, dec: 4.69 })).name === "Barnard's Star", "resolve by coordinates");
await rejects(api.resolveObject({ ra: 400, dec: 0 }), "INVALID_REQUEST", "bad RA");
await rejects(api.getObject("nope"), "NOT_FOUND", "unknown object");
ok((await api.listObservations("mock-obj-empty")).coverageNote, "empty coverage note");

const b = (await api.listObservations("mock-obj-barnard")).items;
ok(b.length === 6 && b[0].observedAt < b[5].observedAt, "barnard observations ordered");
const first = await api.getObservationImage(b[4].observationId), second = await api.getObservationImage(b[4].observationId);
ok(first.availability.status === "processing" && first.availability.retryAfterSeconds, "processing first");
ok(second.availability.status === "available" && second.imageUrl && second.wcs?.approximate === true, "available after polling");
ok((await api.getObservationImage((await api.listObservations("mock-obj-rdra")).items[3].observationId)).availability.status === "unavailable", "unavailable image state");
const sky = (await api.pixelToSky(b[0].observationId, 500, 300)).sky; ok(Math.abs(sky.raDeg - 269.452) < 1e-6, "pixel centre = object centre");

const c1 = await api.createComparison({ baseObservationId: b[0].observationId, targetObservationId: b[1].observationId, mode: "overlay" });
ok(c1.compatibility.verdict === "comparable_with_caveats" && c1.baseImage?.imageUrl && !c1.differenceImage, "overlay comparison");
ok(c1.compatibility.checks.some(k => k.name === "calibration" && k.result === "notEvaluated"), "notEvaluated surfaced");
const c2 = await api.createComparison({ baseObservationId: b[0].observationId, targetObservationId: b[5].observationId, mode: "difference" });
ok(c2.differenceImage?.imageUrl && c2.warnings.some(w => /release/i.test(w)), "difference + release caveat");
const w = (await api.listObservations("mock-obj-wise0855")).items;
const c3 = await api.createComparison({ baseObservationId: w[0].observationId, targetObservationId: w[1].observationId, mode: "blink" });
ok(c3.compatibility.verdict === "not_comparable" && !c3.baseImage && c3.compatibility.checks.find(k => k.name === "wavelength").result === "fail", "not_comparable hides images");
ok((await api.getComparison(c1.comparisonId)).comparisonId === c1.comparisonId, "getComparison");

for (const o of ["barnard", "wise0855", "rwaur", "rdra", "epsindib"]) {
  const s = await api.getSpectrum(`mock-obj-${o}`);
  ok(s.expectedBandCount === 102 && s.points.length === 102 && s.points.some(p => !p.available) && s.points.some(p => p.available && p.fluxUncertainty === undefined), `spectrum ${o}`);
}
ok((await api.getSpectrum("mock-obj-empty")).availability.status === "unavailable", "spectrum unavailable");

const cands = (await api.listCandidates("mock-obj-barnard")).items;
ok(cands.length === 3 && cands[0].score >= cands[1].score && cands.every(c => c.status === "candidate" && c.track.length === 6 && c.scoreBasis), "candidates");
ok((await api.listCandidates("mock-obj-empty")).coverageNote, "no candidates note");
const m = await api.createMeasurement({ kind: "aperture_flux", observationIds: [b[2].observationId], region: { center: sky, radiusArcsec: 12 } });
ok(m.unit && m.uncertainty !== undefined && m.uncertaintyBasis && m.qualityFlags.length === 1 && m.warnings.length, "measurement has unit, uncertainty, flags");
ok((await api.getObjectExtras("mock-obj-barnard")).properMotion.unit === "arcsec/yr" && !(await api.getObjectExtras("mock-obj-rdra")).properMotion, "catalogue extras optional");
ok((await api.getGuidedInvestigation("spot-the-shift")).steps.length === 3, "guided");

const ctx = { activeView: "viewer", objectId: "mock-obj-barnard", observationIds: [b[0].observationId], candidateId: cands[0].candidateId };
const beg = await api.sendChatMessage({ message: "Why is this candidate listed?", context: ctx }, { mode: "beginner" });
const adv = await api.sendChatMessage({ message: "Why is this candidate listed?", context: ctx }, { mode: "advanced" });
ok(beg.message.content !== adv.message.content && beg.references.length === 2 && beg.caveats.length >= 2 && beg.suggestedActions.length, "chat modes, references, caveats, actions");
ok(/ranks|ranking/i.test((await api.sendChatMessage({ message: "does the score mean probability?", context: {} })).message.content), "score disclaimer");
await rejects(api.sendChatMessage({ message: "please simulate error", context: {} }), "AI_UNAVAILABLE", "AI error path");
await rejects(api.sendChatMessage({ message: "  ", context: {} }), "INVALID_REQUEST", "empty message");
ok((await api.getChatSuggestions("compare")).length > 0, "suggestions");

const inv = await api.createInvestigation({ title: "t", objectId: "mock-obj-barnard", notes: [], bookmarks: [], history: [] });
ok((await api.listInvestigations()).total === 1 && (await api.getInvestigation(inv.investigationId)).title === "t", "investigation create/list/get");
await api.updateInvestigation(inv.investigationId, { title: "t2", notes: [], bookmarks: [], history: [] });
ok((await api.getInvestigation(inv.investigationId)).title === "t2", "investigation update");
await api.deleteInvestigation(inv.investigationId);
ok((await api.listInvestigations()).total === 0, "investigation delete");

// 3. Store: events dedupe, URL round trip.
const store = await imp("js/core/store.js");
store.logEvent("observation_viewed", "a"); store.logEvent("observation_viewed", "a"); store.logEvent("observation_viewed", "b");
ok(store.getState().history.length === 2, "history dedupes consecutive duplicates");
store.update({ objectId: "o1", bandIndex: 12, compareMode: "overlay" });
const q = store.toQuery(); store.update({ objectId: null, bandIndex: null, compareMode: "blink" }); store.fromQuery(q);
ok(store.getState().objectId === "o1" && store.getState().bandIndex === 12 && store.getState().compareMode === "overlay", "URL state round trip");

// 4. Real client (backend swap): contract ops hit HTTP; failures map to ApiClientError; proposed ops degrade.
CONFIG.useMock = false;
const real = (await imp("js/api/client.js?real")).api;
await rejects(real.getObject("x"), "NETWORK", "network failure is a separate case");
await rejects(real.listCandidates("x"), "NETWORK", "candidates (contract #19) hit HTTP in real mode");
await rejects(real.getGuidedInvestigation("x"), "DATA_UNAVAILABLE", "frontend-only op unavailable in real mode");
ok(Array.isArray(await real.getChatSuggestions("x")), "suggestions degrade gracefully");

if (failed) { console.error(`${failed} check(s) failed`); process.exit(1); }
console.log("smoke OK");
