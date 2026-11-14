// Dependency-free conformance check: mock fixtures vs contract v0.1.0 required fields,
// "optional fields omitted, never null", and the illustrative-data labelling rule.
// Run: node frontend/tools/check-fixtures.mjs
import { readFileSync } from "node:fs";

const load = f => JSON.parse(readFileSync(new URL(`../mock/${f}`, import.meta.url), "utf8"));
const errors = [];
const need = (rec, keys, where) => keys.forEach(k => { if (!(k in rec)) errors.push(`${where}: missing "${k}"`); });
const noNulls = (v, where) => {
  if (v === null) errors.push(`${where}: null (contract says omit absent fields)`);
  else if (Array.isArray(v)) v.forEach((x, i) => noNulls(x, `${where}[${i}]`));
  else if (v && typeof v === "object") Object.entries(v).forEach(([k, x]) => noNulls(x, `${where}.${k}`));
};

const ids = new Set(); const obsCount = {};
for (const o of load("objects.json")) {
  const w = `object ${o.objectId}`;
  need(o, ["objectId", "name", "aliases", "coordinates", "resolver"], w);
  if (!(o.coordinates.raDeg >= 0 && o.coordinates.raDeg < 360)) errors.push(`${w}: raDeg out of range`);
  if (!(o.coordinates.decDeg >= -90 && o.coordinates.decDeg <= 90)) errors.push(`${w}: decDeg out of range`);
  if (!/illustrative/i.test(o.resolver.name)) errors.push(`${w}: resolver must be labelled illustrative`);
  noNulls(o, w); ids.add(o.objectId);
}
for (const o of load("observations.json")) {
  obsCount[o.objectIds[0]] = (obsCount[o.objectIds[0]] ?? 0) + 1;
  const w = `observation ${o.observationId}`;
  need(o, ["observationId", "objectIds", "center", "image", "qualityFlags", "qualitySummary"], w);
  o.objectIds.forEach(id => { if (!ids.has(id)) errors.push(`${w}: unknown objectId ${id}`); });
  if (o.observedAt && !/Z$/.test(o.observedAt)) errors.push(`${w}: observedAt must be UTC with Z`);
  if (o.image.status !== "available" && !o.image.statusReason) errors.push(`${w}: statusReason required when not available`);
  noNulls(o, w);
}

// Spectra: contract SpectralData rules (gaps carry a reason and omit flux; points ordered; units present).
const spectra = load("spectra.json");
for (const [oid, sp] of Object.entries(spectra)) {
  const w = `spectrum ${sp.spectrumId}`;
  need(sp, ["spectrumId", "objectId", "availability", "position", "extractionMethod", "wavelengthUnit", "fluxUnit", "points", "warnings", "generatedAt"], w);
  if (!ids.has(sp.objectId) || sp.objectId !== oid) errors.push(`${w}: objectId mismatch`);
  if (!/illustrative/i.test(sp.fluxUnit + sp.extractionMethod)) errors.push(`${w}: must be labelled illustrative`);
  let prev = -Infinity;
  for (const p of sp.points) {
    if (p.wavelengthUm <= prev) errors.push(`${w}: points not ordered at band ${p.bandIndex}`); prev = p.wavelengthUm;
    if (!p.available && ("flux" in p || !p.unavailableReason)) errors.push(`${w}: gap band ${p.bandIndex} must omit flux and give a reason`);
    if (p.available && !("flux" in p)) errors.push(`${w}: available band ${p.bandIndex} lacks flux`);
  }
  if (!sp.points.some(p => !p.available)) errors.push(`${w}: expected some gap bands for coverage`);
  if (!sp.points.some(p => p.available && !("fluxUncertainty" in p))) errors.push(`${w}: expected some points without uncertainty for coverage`);
  noNulls(sp, w);
}

// Proposed (non-contract) mock content: must be labelled and stay inside the image.
const prop = load("proposed.json");
if (!/illustrative/i.test(prop._note)) errors.push("proposed.json: missing illustrative note");
for (const [oid, list] of Object.entries(prop.candidates)) {
  if (!ids.has(oid)) errors.push(`candidates: unknown object ${oid}`);
  for (const c of list) {
    const w = `candidate ${c.candidateId}`;
    if (!(c.score >= 0 && c.score <= 1)) errors.push(`${w}: score out of [0,1]`);
    if (!c.scoreBasis) errors.push(`${w}: scoreBasis required (score is not a probability)`);
    const n = obsCount[oid] ?? 0;
    for (const i of [0, n - 1]) {
      const x = c.startPx.x + c.velPx.x * i, y = c.startPx.y + c.velPx.y * i;
      if (x < 0 || y < 0 || x > prop.imageSize.width || y > prop.imageSize.height) errors.push(`${w}: leaves the image at frame ${i}`);
    }
  }
}
for (const g of Object.values(prop.guided)) {
  if (!Object.values(prop.candidates).flat().some(c => c.candidateId === g.answerCandidateId)) errors.push(`guided ${g.guidedId}: unknown answer candidate`);
}
if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log("fixtures OK");
