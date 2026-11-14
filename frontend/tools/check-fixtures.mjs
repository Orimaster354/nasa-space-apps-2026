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

const ids = new Set();
for (const o of load("objects.json")) {
  const w = `object ${o.objectId}`;
  need(o, ["objectId", "name", "aliases", "coordinates", "resolver"], w);
  if (!(o.coordinates.raDeg >= 0 && o.coordinates.raDeg < 360)) errors.push(`${w}: raDeg out of range`);
  if (!(o.coordinates.decDeg >= -90 && o.coordinates.decDeg <= 90)) errors.push(`${w}: decDeg out of range`);
  if (!/illustrative/i.test(o.resolver.name)) errors.push(`${w}: resolver must be labelled illustrative`);
  noNulls(o, w); ids.add(o.objectId);
}
for (const o of load("observations.json")) {
  const w = `observation ${o.observationId}`;
  need(o, ["observationId", "objectIds", "center", "image", "qualityFlags", "qualitySummary"], w);
  o.objectIds.forEach(id => { if (!ids.has(id)) errors.push(`${w}: unknown objectId ${id}`); });
  if (o.observedAt && !/Z$/.test(o.observedAt)) errors.push(`${w}: observedAt must be UTC with Z`);
  if (o.image.status !== "available" && !o.image.statusReason) errors.push(`${w}: statusReason required when not available`);
  noNulls(o, w);
}
if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log("fixtures OK");
