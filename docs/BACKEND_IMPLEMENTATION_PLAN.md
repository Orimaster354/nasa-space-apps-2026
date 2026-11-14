# Backend Implementation Plan

> Owner: Backend developer. Branch: `back-end`. Written 2026-11-14 for a 48-hour hackathon.
> Companion: [BACKEND_CHECKLIST.md](BACKEND_CHECKLIST.md). Shared source of truth: [ARCHITECTURE.md](ARCHITECTURE.md), [FRONTEND_BACKEND_CONTRACT.md](FRONTEND_BACKEND_CONTRACT.md) (v0.1.0), [DATA_MODELS.md](DATA_MODELS.md), [DEVELOPMENT_OWNERSHIP.md](DEVELOPMENT_OWNERSHIP.md), [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md), [SCIENTIFIC_DATA_PIPELINE.md](SCIENTIFIC_DATA_PIPELINE.md).
> Contract status: **v0.2.0** (merged 2026-11-14 from the frontend developer's proposal plus backend needs; see the contract changelog). §4 below summarizes what is agreed and what is still open. Real-data findings so far: [backend/scripts/PROBE_FINDINGS.md](../backend/scripts/PROBE_FINDINGS.md).

## 0. How to read the evidence in this document

| Tag | Meaning |
|---|---|
| **[Verified]** | Read directly from a source during planning (source named). |
| **[Recommendation]** | Our judgment; reasonable, not checked against data. |
| **[Unverified]** | Assumption. Must be checked against real SPHEREx data/docs before relying on it (tracked in [§12](#12-assumptions-to-verify-against-real-data)). |

Limits of this research: the official challenge page `spaceappschallenge.org/2026/challenges/planet-x-and-spherex/` was found by search but its body text and resource list could **not** be retrieved (a direct fetch failed with a certificate error; the search snippet showed only the title). No NASA starter repository, sample notebook list, or recommended-library list from the challenge itself was verified. **Please paste the challenge's official resource links** so §2 can be completed. No repository URLs have been invented.

---

## 1. Existing state and inconsistencies found

**Repo [Verified]:** branch `back-end` (from `main` at `fe7cdbb`). `backend/{api,data,processing,tests}/` exist but contain only `.gitkeep`. No code, no dependency file. `docs/FRONTEND_SPECIFICATION.md` does **not** exist. Only Python 3.11.9 is confirmed installed locally.

### Inconsistencies and gaps in the existing shared docs

| # | Finding | Impact | Resolution |
|---|---|---|---|
| G1 | The contract had no models/endpoints for **candidates** or **proper motion**. | Frontend and backend would invent incompatible shapes. | **Resolved in contract 0.2.0** (§4). Guided investigations stay frontend-scripted. |
| G2 | **Resolved in 0.2.0.** `GET /observations/{id}/image` had no **cutout center/size**. IRSA serves ~70 MB full MEFs [Verified: IRSA data products] but supports sky **cutouts** [Verified: IRSA data access]. A usable viewer needs a cutout around the object. | Image would be either huge or ambiguous. | Field-of-view parameters and common pixel grid (§4). |
| G3 | `Observation` has no **bandpass** and the compatibility check enum has no **background** check. IRSA IMAGE is **not zodiacal-light subtracted** [Verified]; the ZODI HDU holds a modeled background. Zodi level varies with epoch/sky position [Unverified], so an epoch-to-epoch difference can include a background change. | Difference images/flux changes could be misinterpreted. | Add `bandpass` to `Observation`, `"background"` to check names (§4). |
| G4 | `Measurement.kind` has no position/motion kinds. | Proper motion would be unrepresentable. | §4. |
| G5 | The contract's `SpectralData` assumes assembling a spectrum from many observations. IRSA also publishes a **DR1 All-Sky Cube** (HEALPix tiles, 102 channels) [Verified: IRSA data products]. | A much simpler spectral route may exist. | Open decision D-5 (§13). |
| G6 | Investigations have no owner notion; no auth; judges use the site concurrently. | Judges' notes/chats would mix. | Anonymous per-browser client ID (§4, decided). |
| G7 | Contract §11/OD list marks the backend branch name as undecided. | Docs stale. | Branch is `back-end`; update OD-10 when merging docs. |
| G8 | `DEVELOPMENT_OWNERSHIP.md` lists `backend/ai/` as planned and a services layer as undecided. | Layout ambiguity. | Layout fixed in §3. |
| G9 | Images are surface brightness (MJy/sr) [Verified]; "flux" in `SpectralData`/`Measurement` needs a stated conversion (pixel solid angle). | Unit mistakes. | Every measurement states unit and conversion method. |

---

## 2. External resources assessment

### 2.1 Official NASA / IPAC resources [Verified unless stated]

| Resource | What it implements | Reuse | Extend / build ourselves | Saves time? |
|---|---|---|---|---|
| **IRSA SPHEREx data access docs** (`caltech-ipac.github.io/spherex-archive-documentation/spherex-data-access`) | Documents access: browsable HTTPS directories, **SIA2** (`https://irsa.ipac.caltech.edu/SIA`, `COLLECTION` required, returns `access_url`, `cloud_access`), **cutouts** via `?center=RA,Dec&size=...` on an MEF URL, **TAP**, AWS mirror, astroquery/pyvo. Collections: `spherex_dr1`, `spherex_dr1_deep`, `spherex_dr1_cube`, `spherex_qr3`, `spherex_qr3_deep`. QR2 not searchable via SIA2. New data can lag SIA2 by ~a day. No auth or rate limits stated. | Use as the retrieval spec. | Error handling, caching, concurrency limits, retries. | **Yes, major.** |
| **IRSA SPHEREx data products docs** (`.../spherex-data-products`) | Spectral Image MEF layout: HDUs `IMAGE` (MJy/sr, 2040x2040, zodi **not** subtracted), `FLAGS` (bitmap), `VARIANCE`, `ZODI`, `EPSF`/`PSF`, `WCS-WAVE` (lookup table; ~1 nm interpolation accuracy; "intended for visualization", use CWAVE/CBAND of the Spectral WCS product for science). Six detectors per pointing; filename holds observation ID and detector. Flag bit meanings are in **Table 16 of the SPHEREx Explanatory Supplement** (not fetched). | Use as the FITS spec. | Flag decoding, wavelength handling per pixel, solid-angle conversion. | **Yes, major.** |
| **IRSA tutorial: SPHEREx cutouts** (`.../irsa-tutorials/spherex-cutouts`) | Working workflow: `astroquery.ipac.irsa.Irsa.query_tap` on tables `spherex.artifact` (cols `uri`) joined to `spherex.plane` (cols `time_bounds_lower` = observation time, `energy_bandpassname` e.g. `SPHEREx-D2`, `poly` footprint) with `CONTAINS(POINT('ICRS',ra,dec), p.poly)`; builds cutout URLs `https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<deg>`; reads cutout MEF HDUs 0-6; wavelength via `WCS(header, fobj=hdulist, key="W")`; parallel fetch with a 10-worker thread pool; packages `numpy astropy astroquery matplotlib`. | **Reuse the cutout pattern directly. Do not reuse its TAP discovery query**: measured at about 310 s (and HTTP 502 for some positions) versus about 1.3 s for SIA2. | Caching, validation, rendering, quality handling. | **Yes, this is our Phase 2 backbone.** |
| **IRSA cutout tool docs** (`irsa.ipac.caltech.edu/data/SPHEREx/docs/cutout_tool.html`), **IRSA SPHEREx intro / PSF / source-discovery tutorials** | Exist per search results. **Not read** in detail. | Review during Phase 2/3. | n/a | Likely |
| **IRSA QR overview** | Release notes; sources conflict on whether QR2 is the API default [Verified via search]. | Treat DR1/QR3 as current per the data-access page; recheck. | n/a | n/a |
| **Official challenge page** | Not retrieved (see §0). | n/a | n/a | Unknown |

Library compatibility [Recommendation, Unverified in this environment]: `astropy`, `astroquery`, `numpy`, `scipy`, `reproject`, `photutils`, `fastapi`, `uvicorn`, `pydantic`, `httpx` should install on Python 3.11; nothing has been installed or tested here.

### 2.2 Third-party projects (not official NASA)

- **Other teams' Space Apps 2026 repositories** appeared in web search (blink comparators, "sky diary" viewers, etc.). **[Unverified: not opened, licenses unknown.]** They are competitors' work; do **not** copy. Not part of this plan.
- **`spxquery`** (a Python package whose documentation appeared in search results). Origin and maintainers **not verified**. Do not depend on it without review; the IRSA tutorial pattern already covers retrieval.

### 2.3 WISEView (third-party reference, NOT SPHEREx)

Sources read: the GitHub repo page, the GitHub API metadata, the `wv/` directory listing, and the README. **Not read: any source code.**

| Fact [Verified] | Value |
|---|---|
| Repo | `backyardworlds/wiseview`, description "WiseView: Visualizing Motion and Variability of Faint WISE Sources" |
| Language / activity | JavaScript; last push 2021-09-01; not archived; 78 commits |
| Data | unWISE image blinks (WISE/NEOWISE-based), not SPHEREx |
| License | README says the software is dedicated to the public domain ("free and unencumbered software released into the public domain"). The GitHub API reports **no license object**. **Decision: reference only, no code copied.** |
| URL-driven view parameters | `ra, dec, size, band, speed, trimbright, linear, color, mode, coadd_mode, zoom` (meanings of the parameters are **not explained** in the README; inferred from names only) |
| Citation | README asks users to cite WiseView; ASCL 1806.004 |

| WISEView (what it shows) | Our SPHEREx equivalent | Reuse | New / different |
|---|---|---|---|
| Coordinate search (RA/Dec in URL) | Object name or RA/Dec search | Behavioral reference only | Name resolution, SIA/TAP discovery |
| Multi-epoch image blinks of one position | Chronological frames of a position | Concept | **Fundamental difference:** SPHEREx pixels each carry a different wavelength and a position is revisited roughly every six months [Unverified cadence]; frames of the **same band** across epochs may be few. WISE-style dense same-band time series should not be assumed. |
| Playback speed, brightness/contrast (`trimbright`), linear/other scaling, zoom | Equivalent viewer controls | Concept; the frontend implements | Backend supplies stretch metadata; per-image units MJy/sr |
| Motion/variability investigation by eye | Same, plus measurements, candidates, AI | Concept | Quantified measurements with uncertainty; zodi-aware comparisons; compatibility checks |
| unWISE coadds | SPHEREx spectral-image cutouts (6 detectors, bands 1-6) | None | Entire retrieval and calibration pipeline |

Nothing in WISEView's data retrieval, calibration, or pipeline is assumed compatible with SPHEREx.

---

## 3. Backend foundation (Phase 1)

**Stack [decided]:** Python 3.11, **FastAPI**, Pydantic models mirroring the contract, `pyproject.toml` + `pip` in a venv.

Proposed layout (new directories created when work starts; none exist yet except under `backend/`):

```text
backend/
├── pyproject.toml
├── app/                    # FastAPI app factory, settings, logging, error handlers
├── api/                    # routers + Pydantic schemas (contract models). No science.
├── services/               # use-case orchestration (objects, observations, comparisons, spectra, candidates, investigations)
├── processing/             # IRSA client, FITS/WCS, alignment, photometry, quality, rendering (no HTTP/LLM imports)
├── ai/                     # provider adapter, tools, context builder, grounding check, guided templates
├── store/                  # SQLite persistence (investigations, cache index)
├── data/                   # git-ignored cache (FITS cutouts, rendered images)
└── tests/                  # unit (synthetic), integration (recorded fixtures), real-data (marked, opt-in)
```

Rules: `processing` imports nothing from `api`/`ai`/`services`; `ai` calls `services` (never raw FITS); API response models == contract models.

Foundation tasks: settings via environment variables (`.env` git-ignored; `.env.example` committed with names only), structured logging with request IDs, error handlers mapping to the contract's `ApiError` envelope, `GET /api/v1/health` returning `{status, contractVersion}`, CORS configured for the frontend origin, pytest layout with markers `synthetic`, `recorded`, `realdata`.

---

## 4. Contract v0.2.0: what is agreed and what is open

Agreed (in `FRONTEND_BACKEND_CONTRACT.md` and `DATA_MODELS.md`):

| Addition | Where |
|---|---|
| `Candidate` model and `GET /objects/{objectId}/candidates` (#19). Field names are the frontend's (`track`, `score`, `scoreBasis`, `warnings`, `status: "candidate"`) plus `description`, `evidence`, `isProbability: false`, optional `evidenceRefs`/`limitations`/`properMotion` | contract §3, §5; data models §10 |
| `ProperMotion` (`value`, `unit`, `source`, required `provenanceKind`: catalogue / derived / unvalidated displacement) on `AstronomicalObject.properMotion` and `Candidate` | data models §11 |
| `Observation.bandpass` | data models §2 |
| Image field-of-view parameters (`centerRa`, `centerDec`, `fovArcmin`) and the **common pixel grid** guarantee with `ObservationImage.gridId`, replacing a separate frame-sequence operation | contract §5 |
| `ComparisonResult.comparisonKind` (`same_band_epochs` / `cross_band`), compatibility check `background`, `Measurement.kind` `position_centroid` / `derived_motion` | contract §3 |
| `ChatContext.candidateId` and `mode` (`beginner` / `advanced`); `"candidate"` as AI-reference and bookmark target | contract §3 |

Not adopted: the frontend's proposed frame-sequence operation (#20), because the common-grid guarantee gives the same blink behavior with per-observation image requests.

**Guided investigations are frontend-scripted** and are not part of the contract. The backend's job is to supply real candidates and evidence; the earlier idea of backend `GuidedChallenge` endpoints is dropped (P2 only if time remains).

Still open: anonymous client ID for server-side investigations (OD-7; the frontend currently saves investigations in the browser), co-adding/stacking images (OD-16), and overlays from other catalogues (OD-17).

Small optional contract additions found while building, **not yet in the contract** (needs the frontend developer): a `bandpass` query filter on `GET /objects/{id}/observations`; optional `centerRa` / `centerDec` / `fovArcmin` on `GET /observations/{id}/pixel-to-sky`; and the default field of view, which is now **10 arcmin** (was 3; 10 arcmin is about 98 native 6.15-arcsec pixels across). All are implemented in the backend. The frontend data layer reads `?fov=` and `?band=` from the page URL (default band `SPHEREx-D3`).

---

## 5. Phase 2: NASA / IRSA integration

Follow the IRSA tutorial pattern [Verified]. Do not build archive abstractions beyond a thin client.

1. **Name resolution** [Recommendation, Unverified here]: resolve names to coordinates with an astronomy name resolver available via astropy/astroquery (e.g. SIMBAD/Sesame). Record which service answered in `AstronomicalObject.resolver`. RA/Dec input skips resolution.
2. **Discovery (changed after measurement):** use **SIA2**, not TAP. SIA2 found the same 258 files for WISE 0855 in about 1.3 s; the TAP join took about 310 s and sometimes returned HTTP 502. SIA2 also returns the full image centre, footprint polygon, pixel scale (6.15 arcsec) and download URL. Details: `backend/scripts/PROBE_FINDINGS.md`.
3. **Collections/releases:** `SPX_SIA_COLLECTIONS` (default `spherex_dr1`, `spherex_dr1_deep`, `spherex_qr3`, `spherex_qr3_deep`); a new release is a config change. [Verified: data-access page.] QR2 is superseded and not searchable via SIA2.
4. **Retrieval:** cutout URL `https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<deg>` [Verified pattern]. Download concurrency <= 10 [tutorial uses 10 workers; no limits are documented, so use fewer by default and back off].
5. **Observation record:** map to `Observation` (omit anything not provided). `observationId` = stable app ID derived from the archive's unique identifier (D-6).
6. **Failure handling:** timeouts and retry with backoff; archive down -> `UPSTREAM_UNAVAILABLE`; no coverage -> empty list + `coverageNote`; partial/failed download -> image `availability.status = failed` with `retryAfterSeconds`; a request that outlives its time budget returns `202`/`processing` and finishes in the background (in-flight de-duplication per observation+cutout key).
7. **Cache:** content-addressed by URL+center+size under `backend/data/`; metadata index in SQLite. Never refetch a cached cutout.
8. **Newly ingested data** may appear in TAP/directories before SIA2 [Verified]; prefer TAP for discovery as the tutorial does.

---

## 6. Phase 3: FITS and scientific processing

For each cutout MEF [Verified structure: HDU0 header-only, then IMAGE, FLAGS, VARIANCE, ZODI, EPSF/PSF, WCS-WAVE; do not assume every file matches]:

- **Inspect first:** write a small HDU inspector and run it on real files **before** writing parsers (P0-03). Record actual header keywords for time, observation ID, detector, units. The data-products page did **not** state the time or observation-ID keywords; the tutorial's `observation_date` came from the archive table, so use TAP `time_bounds_lower` for observation time unless the header proves otherwise.
- **Image:** MJy/sr surface brightness, not zodi-subtracted [Verified]. Keep zodi-subtracted and raw both derivable (`IMAGE - ZODI` is a modeled subtraction; label it as such).
- **Quality:** decode `FLAGS` using Table 16 of the Explanatory Supplement (to be read; bit meanings are **not** invented here). Unknown bits are reported as `severity: info` with the raw bit value.
- **Uncertainty:** `VARIANCE` is variance of IMAGE (units (MJy/sr)^2) [Verified]. Propagate in quadrature for aperture sums; document correlated-noise caveat.
- **WCS:** sky via `astropy.wcs.WCS(header)`; wavelength via the WCS-WAVE lookup [Verified], accepting that it is "intended for visualization" with ~1 nm accuracy; if CWAVE/CBAND (Spectral WCS product) are needed for science, add that retrieval as a P1 task.
- **Rendering:** normalize with a stated stretch (default asinh), render PNG, record `stretch`, `wcs` summary (`approximate: true` unless verified otherwise), and a flag-mask overlay.
- **Pixel-to-sky:** backend endpoint (contract OD-6 default).
- **Alignment:** reproject the target onto the base grid (`reproject`, flux-conserving for photometry); record method and residual.
- **Compatibility gate before any comparison:** footprint overlap, detector/bandpass and wavelength-at-position compatibility, release/calibration equivalence (`notEvaluated` unless verified), background (zodi), alignment residual, quality flags. Verdict mapping per [SCIENTIFIC_DATA_PIPELINE.md §5](SCIENTIFIC_DATA_PIPELINE.md). Thresholds are set in code, documented, and tested.
- **Four tiers** stay separate (raw / processed / derived / AI explanation); see the pipeline doc.

---

## 7. Phase 4: Core science

### 7.1 Observations, comparison, brightness

- Chronological organization by `time_bounds_lower`; frames grouped by `bandpass`.
- **Frame sets** (D-4 decision): the plan supports `comparisonKind = same_band_epochs` as the primary blink, and `cross_band` labelled as spectral, never as time change. First task is to measure how many same-band epochs actually exist at candidate targets (P0-06).
- Brightness: aperture photometry from IMAGE with background annulus; convert surface brightness to flux density using pixel solid angle (the Solid Angle Pixel Map is a documented calibration product [Verified: listed]; how to apply it is **Unverified**; document the exact conversion in `method`). Uncertainty from VARIANCE; report non-detections as such.
- Basic change detection: difference of aligned same-band frames and aperture flux change with uncertainty; a change is reported with its value/uncertainty and compatibility caveats. No significance language beyond ratio-to-uncertainty.
- Quality/uncertainty visualization data: mask overlay URL, per-measurement flags.

### 7.2 Candidate generation and ranking (decided: catalog-seeded, evidence-backed)

**Approach [Recommendation]:**
1. **Seed:** objects near the field with catalog proper motion from a catalog service (which catalog is D-8; do not assume availability; record catalog name and ID).
2. **Check:** for each seed with adequate coverage, measure position (centroid) and brightness in available same-band frames, with uncertainty. Reject seeds with insufficient coverage/quality rather than guessing.
3. **Evidence:** attach `CandidateEvidence` entries pointing to real `Measurement`/`Observation` IDs. Expected catalog displacement over the observed baseline is compared to the **measured position uncertainty**; if the expected shift is below measurement capability, say so.
4. **Rank:** a transparent heuristic using stated criteria (e.g. expected displacement / position uncertainty, measurement significance, data quality, number of usable epochs). Show criteria values; `isProbability: false`; `status: "candidate"`; `limitations` lists what was not validated (calibration, blending, etc.).
5. **Stable IDs:** `candidateId` = hash of (catalog ID or position key, field key, method version).

**Not done:** blind automated source differencing (P2, only if time remains); no probabilities; no "discovery" language anywhere.

If the field has no seeds or poor data, the candidate list is empty with an explanatory `coverageNote`.

### 7.3 Proper motion and tracking

| Kind | Source | Contract representation |
|---|---|---|
| Catalog value | Catalog service | `ProperMotion.provenance.kind = "catalog"` + catalog name/ID, epoch, units, uncertainties as given |
| Derived from our observations | Our centroid fits over multiple epochs | `kind = "derived_from_observations"`, only if positions have uncertainties, WCS/astrometry validated, and enough baseline |
| Apparent displacement, not validated | Raw centroid shift | `kind = "unvalidated_displacement"`, with a note |

A pixel shift is never labeled proper motion. Centroiding on SPHEREx pixels, the PSF (EPSF HDU), blending, and the astrometric zero-point are open verification items (§12). If SPHEREx precision is insufficient for a target, the candidate says that.

### 7.4 End-to-end investigation (decided: data-driven target choice)

Probe all five listed targets (Barnard's Star, WISE 0855, RW Aurigae, R Draconis, Epsilon Indi B) for: coverage, same-band epoch count, detections at the position, quality flags, measurability of expected shift/variability. Rank by feasibility and pick the best; write the verdict per target in a short note in `docs/` or the test notebook. If none gives a motion story, the demo uses a variability or spectral story honestly. Nothing is assumed suitable in advance.

---

## 8. Phase 5: Spectral processing

- **Verified:** six bands/detectors with ranges 0.75-5.0 um and varying resolving power; a DR1 All-Sky Cube product with 102 channels exists; WCS-WAVE gives per-pixel wavelength.
- **Unverified:** how to build a spectrum at a position from spectral images vs the cube; whether the cube covers the demo targets at needed depth.
- **Plan:** P1 first prove one spectrum at one position (from images or the cube, D-5), with gaps as `available: false`. Support a documented **subset** of bands rather than claiming all 102 if processing time/coverage does not permit. Band selection and per-band flux/uncertainty are returned in the existing `SpectralData` shape. Cross-observation spectral comparison is P2.

---

## 9. Phase 6: AI companion

**Principles:** one conversational LLM; deterministic science; the LLM sees only structured results.

- **Provider adapter [decided: free tier, chain of two]:** an interface with two configured providers tried in order, then a deterministic fallback. Provider names, models, keys, and limits are configuration, not code. Current free-tier limits and tool-calling support must be checked when a provider is chosen (D-1). A Claude Pro subscription does **not** include API credits; any API key is separate and its cost/usage must be set explicitly. Keys live only in server environment variables.
- **Tools** (backend functions the LLM may request): get_object, list_observations, get_observation, get_comparison, run_comparison, get_measurements, get_spectrum, get_candidates, get_proper_motion. Each returns the same structured models as the API.
- **Context builder:** from `ChatContext` (object, observations, comparison, spectrum, selection, active view) plus the investigation's notes/summary; selected band/wavelength included when present.
- **Grounding (decided: structured output + numeric check):** the model must return JSON: `{ answer, citedResultIds[], claims[], hypotheses[], suggestedActions[] }`. The backend verifies every number in `answer` appears in a cited result (after unit/format normalization). Failure -> one retry, then fall back to the deterministic templated summary labeled "automated summary". `caveats` are generated by the backend from result warnings and always returned. Evidence and hypotheses are separate fields; hypotheses are labeled as such.
- **Never:** invent numbers/metadata, claim a discovery, treat `notEvaluated` as pass, or describe pixel content.
- **Investigation summaries:** generated from stored notes/measurements via the same grounded path.
- **Guided investigations (frontend-scripted, decided with the frontend developer):** the guided script lives in the frontend. The backend supports it by supplying real candidates, evidence and measurements, and by never producing a candidate the data does not support. The AI companion explains guided steps from the same structured results and is subject to the same numeric check. Backend-generated challenges are P2 only.
- **Failure behavior:** LLM unavailable/over quota -> deterministic summary; the contract's AI-unavailable state is used only when nothing can be produced.

---

## 10. Phase 7 and 8: Persistence and API integration

- **Persistence:** SQLite on the persistent volume for the cache index and, once OD-7 is settled, investigations. The frontend currently saves investigations in the browser, so server-side storage is only needed if both developers agree; if so, scope them by an anonymous client ID. Messages are server-managed per the contract. Concurrent edits: last write wins (OD-15).
- **API:** implement the 18 existing operations plus approved §4 additions. All responses validated against Pydantic models equal to the contract; optional fields omitted, never `null`.
- **Integration tests:** schema validation of every endpoint; missing fields; `processing`/`202` polling; `unavailable`/`failed`; real image delivery from a browser origin (CORS); AI response structure; candidate ranking explanation present; proper-motion provenance present.

---

## 11. Phase 9: Deployment and demo readiness

- **Recommendation (decided):** cloud host with a **persistent disk**; pre-cache a curated target set; arbitrary targets fetch live with a visible "fetching from IRSA" state (`processing`) and a timeout. Host choice is D-3. Requirements: outbound HTTPS, persistent volume, environment variables for keys, long-enough request timeouts or background tasks, HTTPS for the frontend origin.
- Secrets only in host environment; `.env` never committed.
- Verify actual IRSA retrieval **from the deployed host** (egress, latency), AI providers from the host, and the fallbacks.
- Repeatable demo: scripted run, pre-warmed cache, backup recording, mock-mode fallback only if clearly labeled.
- IRSA data credit: confirm the required SPHEREx/IRSA acknowledgment text and DOI from IRSA (a third-party page mentioned DOI 10.26131/IRSA652; **Unverified**); also cite WISEView as inspiration and the Space Apps rule requiring citation of resources.

---

## 12. Assumptions to verify against real SPHEREx data

| # | Assumption | Check |
|---|---|---|
| A1 | `spherex.plane`/`artifact` expose release and observation ID columns | Inspect TAP schema (P0-02) |
| A2 | Observation time = `p.time_bounds_lower` (exposure start?) | Compare with headers |
| A3 | Same-band multi-epoch frames exist at demo targets | Coverage probe (P0-06) |
| A4 | Cutout MEFs from DR1/QR3 share structure | HDU inspector across several files |
| A5 | FLAGS bit meanings (Explanatory Supplement Table 16) | Read it; build decoder table |
| A6 | Pixel-to-flux conversion via solid angle map | Read docs; validate against a known source |
| A7 | Zodi changes between epochs matter for differences | Compare ZODI between epochs |
| A8 | Positional precision is sufficient for any target's motion | Centroid repeatability test |
| A9 | WCS-WAVE interpolation is enough for the demo | Compare to CWAVE/CBAND |
| A10 | 102 spectral channels via cube vs images | Inspect cube for demo target |
| A11 | IRSA tolerates our request rate from the host | Observe; add backoff |
| A12 | Cutout size/center parameter semantics (degrees, `d` suffix) | Test requests |
| A13 | Name resolver and catalog proper-motion availability | Test with the five targets |

---

## 13. Decisions

| ID | Decision | Status |
|---|---|---|
| D-1 | Which two free LLM providers/models, and their limits | **You decide** (needs a check of current free tiers and tool-calling) |
| D-2 | Contract v0.2.0 | **Resolved** (merged 2026-11-14, frontend data layer updated). Remaining open: OD-7 (server-side investigations/client ID), OD-16 (co-add), OD-17 (overlays) |
| D-3 | Cloud host choice (persistent disk required) | **You decide** |
| D-4 | Blink frame definition | Recommended: same-band-epoch primary, cross-band labeled, finalized after the coverage probe |
| D-5 | Spectrum source: spectral images vs All-Sky Cube | Open until A10 checked |
| D-6 | `observationId` scheme | Open; derive from archive unique ID |
| D-7 | Curated demo target list | Open; set after the five-target probe |
| D-8 | Proper-motion catalog and name resolver | Open; test availability (A13) |
| D-9 | Submission freeze clock time | **You decide** (plan only protects a relative freeze) |
| D-10 | Challenge page resource links | **You provide** |

Decided in interview: FastAPI + pyproject/pip; Python 3.11; cloud + persistent disk + curated pre-cache + live fallback; free-tier LLM with a chain of two providers; catalog-seeded candidates; structured-output + numeric-check grounding; WISEView as reference only; data-driven target choice; two `docs/` files plus a `.plans` spec. Superseded after the frontend branch review: guided investigations are frontend-scripted (not backend templates), and the contract changes were merged as v0.2.0 rather than kept as an addendum.

---

## 14. Parallel development and ownership

| What the backend can do now (no frontend dependency) | Needs the contract/addendum agreed | The frontend can do independently |
|---|---|---|
| IRSA TAP probe, cutout download, HDU inspection, WCS, flag decoding, photometry, alignment, comparison logic, candidate/proper-motion logic, AI tools and provider adapter, SQLite store | Candidate, proper-motion, field-of-view and common-grid shapes (now agreed in contract 0.2.0); OD-7, OD-16, OD-17 still open | Everything in their own area, including mocks that conform to the contract |

Shared files requiring communication before change: the contract, data models, `ARCHITECTURE.md`, this plan's §4, `README.md`, `.gitignore`, `.github/workflows/`, boundary configuration (API base URL, CORS origins, `X-Client-Id`). Per [DEVELOPMENT_OWNERSHIP.md](DEVELOPMENT_OWNERSHIP.md): no duplicated business logic; no scientific calculations in the frontend; no backend logic in UI code; no uncoordinated model changes; no coupling to the other side's internals. The frontend developer owns how mocks and service access are organized; the only requirement is exact schema conformity so switching to the real API needs no shared-model change.

**Must be complete before integration:** blocking decisions in INTEGRATION_PLAN (OD-3, 5, 6, 7, 12, 14) plus OD-7; every endpoint contract-valid; image URLs loadable from the frontend origin; one real target working end to end.

---

## 15. Testing strategy

Keep **synthetic**, **recorded**, and **real-data** tests separate by pytest markers; real-data tests are opt-in (network, slow).

| Area | Synthetic | Recorded fixtures | Real data (opt-in) |
|---|---|---|---|
| IRSA integration | Query builder, response parsing, error mapping | Saved TAP responses | Live query at a test coordinate; real cutout download; missing coverage; failed request |
| FITS | Generated MEFs with/without optional HDUs, malformed headers | Small saved cutout | HDU inspection on real files |
| Processing | Synthetic star fields: alignment residual, photometry vs known flux, uncertainty propagation, quality rules, compatibility verdicts, centroid/position estimation | n/a | Repeatability of position/flux between epochs |
| Spectra | Synthetic bands with gaps | n/a | One real spectrum |
| Candidates/proper motion | Known injected shifts; catalog-vs-derived provenance; ranking criteria | Saved catalog responses | The probed targets |
| AI | Fake provider: tool invocation, context correctness, numeric-check rejection of invented numbers, evidence vs hypothesis fields, fallback path, AI explanations of guided steps use only supplied results | Saved provider responses | Smoke test with real provider |
| Integration | Pydantic/contract validation on every endpoint; error envelope; omitted-not-null | Shared example JSON (OD-3) | Full workflow: search -> observations -> image -> compare -> measure -> ask AI |

---

## 16. Execution strategy

Order by dependency; no hour estimates because the data-access outcome changes everything downstream.

**Minimum viable backend (MVB):** search an object -> real SPHEREx observations -> real rendered image -> compatibility-checked comparison -> measurement with uncertainty -> AI answer grounded in them, served through contract-valid endpoints, deployed.

**Critical path:** TAP probe (schema + one position) -> cutout download -> HDU inspection -> render image -> observation + image endpoints -> alignment + compatibility -> aperture measurement with uncertainty -> comparison endpoint -> AI tools + grounding -> deploy with cache.

**Parallel after the TAP probe works:** (a) provider adapter + fallback + fake-provider tests; (b) SQLite store + investigation endpoints; (c) candidate data model + catalog lookup; (d) deployment skeleton; (e) target feasibility probe.

**Defer:** All-Sky Cube spectra beyond one demo; cross-observation spectral comparison; automated source differencing; CWAVE/CBAND science wavelength; backend-generated guided challenges.

**Abandon if they threaten the core demo:** the candidate panel for live-fetched targets; candidates for non-curated targets beyond what time allows; second LLM provider polish; full 102-band support.

**Protected window:** a relative freeze before the submission deadline (clock time is D-9), reserved for integration fixes, demo rehearsals, and a backup recording; no new features inside it.

---

## 17. First implementation task

Run a throwaway script that (1) lists the schema of `spherex.plane` and `spherex.artifact`, (2) queries one test coordinate (e.g. one of the five targets), (3) downloads one cutout, (4) prints its HDU list, key headers, and units. Everything else depends on what this shows. See P0-01 to P0-04 in [BACKEND_CHECKLIST.md](BACKEND_CHECKLIST.md).

## 18. Sources used

- [IRSA SPHEREx data access](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-access)
- [IRSA SPHEREx data products](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products)
- [IRSA tutorial: SPHEREx cutouts](https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts)
- [WISEView repository](https://github.com/backyardworlds/wiseview) and its README
- Search result pages (QR overview, challenge listings) as noted; not all were opened.
