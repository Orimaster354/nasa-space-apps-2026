# Backend Checklist

> Companion to [BACKEND_IMPLEMENTATION_PLAN.md](BACKEND_IMPLEMENTATION_PLAN.md). Owner: backend developer (`back-end` branch).
> Format: `- [ ] ID: task` then **Dep** (dependencies) and **Done** (definition of done).
> "Contract v0.2" items need frontend approval first (plan §4). Real-data tasks are marked 🛰.

Priorities: **P0** essential for a functioning submission · **P1** important differentiators · **P2** if time permits.

---

## P0: Essential

### Data access and inspection (critical path)

- [x] **P0-01 🛰 Query IRSA for observations at a test coordinate.**
  Dep: none. Done: a script runs the TAP join (`spherex.plane` + `spherex.artifact`, `CONTAINS(POINT('ICRS',ra,dec), p.poly)`) for one coordinate and prints rows with `uri`, `time_bounds_lower`, `energy_bandpassname`; result count and any error are recorded in a note.
- [x] **P0-02 🛰 Inspect the TAP table schemas.**
  Dep: P0-01. Done: columns of both tables are listed in a note; release identifier and observation ID columns are identified or confirmed absent (closes A1).
- [x] **P0-03 🛰 Retrieve one actual SPHEREx FITS cutout and inspect its HDU structure.**
  Dep: P0-01. Done: a cutout is downloaded via `?center=<ra>,<dec>d&size=<deg>`; script prints every HDU name, shape, dtype, units and key headers; findings written down, including any difference from the IRSA docs (closes A4, A12).
- [x] **P0-04 🛰 Extract valid WCS metadata and observation timestamps.** *(Done: WCS, observation time, and the per-pixel wavelength and bandwidth from the WCS-WAVE table (`processing/fits_io.py`, bilinear from a coarse node grid; the header calls it approximate). Wavelength at the object is now used in the comparison `wavelength` check and reported on every measurement.)*
  Dep: P0-03. Done: `astropy` builds a celestial WCS from the IMAGE header; pixel↔sky round-trip error is reported; the observation time source (TAP column vs header) is confirmed against the file (closes A2); wavelength obtained at a pixel via the WCS-WAVE method.
- [x] **P0-05 Backend skeleton and health endpoint.**
  Dep: none. Done: FastAPI app starts from a clean venv using `pyproject.toml`; `GET /api/v1/health` returns `{status:"ok", contractVersion:"0.1.0"}`; settings read from env; `.env.example` lists variable names only; one passing test.
- [x] **P0-06 🛰 Coverage probe of the five candidate targets.**
  Dep: P0-01. Done: for Barnard's Star, WISE 0855, RW Aurigae, R Draconis, Epsilon Indi B: number of observations, bands, distinct epochs, same-band epoch counts, and whether the target is detected are tabulated; a written feasibility verdict per target and a recommended primary target (closes A3, informs D-4/D-7).

### Foundations

- [x] **P0-07 Error handling to the contract's `ApiError` envelope.**
  Dep: P0-05. Done: every error code in contract §4 can be produced in tests; unknown errors become `INTERNAL` with a request ID; no stack traces in responses.
- [x] **P0-08 Logging with request IDs.**
  Dep: P0-05. Done: each request logs method, path, status, duration, request ID; secrets never logged (checked by a test).
- [x] **P0-09 Pydantic models mirroring contract §3.** *(Objects, observations, images done in `api/schemas.py`; the rest as endpoints are built.)* *(Done: a model exists for every contract type and the error envelope (spectrum and candidate models exist before their endpoints). Every example in DATA_MODELS.md validates and round-trips (tests/test_contract_models.py). Optional fields are omitted, never null.)*
  Dep: P0-05. Done: a model exists for each contract type; example JSON from DATA_MODELS.md validates; omitted-not-null enforced by serialization settings (test).
- [x] **P0-10 Test structure and markers.**
  Dep: P0-05. Done: `synthetic`, `recorded`, `realdata` markers; `pytest -m "not realdata"` runs offline.

### Observations and images

- [x] **P0-11 Object resolution.**
  Dep: P0-05. Done: a name or RA/Dec returns an `AstronomicalObject` with `resolver` recorded; unknown name returns `NOT_FOUND`; invalid coordinates return `INVALID_REQUEST`; resolution tested for the five targets (closes A13 partly).
- [x] **P0-12 `GET /objects/search`, `/objects/resolve`, `/objects/{id}`.**
  Dep: P0-09, P0-11. Done: responses validate against the contract; limits enforced.
- [x] **P0-13 Observation discovery returns observation records in the agreed schema.**
  Dep: P0-02, P0-09. Done: `GET /objects/{id}/observations` returns `Page<Observation>` sorted by time; optional fields omitted when absent; empty coverage returns `items: []` + `coverageNote`; `from/to/dataRelease/limit/offset` honored.
- [x] **P0-14 `observationId` scheme (D-6).**
  Dep: P0-02. Done: IDs are stable across restarts and map back to the archive record; documented.
- [x] **P0-15 Cutout fetch + cache.**
  Dep: P0-03. Done: cutouts are cached under `backend/data/` keyed by URL+center+size; second request does not hit IRSA (test with a stub); concurrent requests for one key share one download; timeouts and retries with backoff.
- [x] **P0-16 FITS reader with defensive parsing.**
  Dep: P0-03. Done: reads IMAGE/FLAGS/VARIANCE/ZODI when present; missing HDUs produce warnings not crashes; malformed file returns `failed` availability (synthetic tests for each case).
- [x] **P0-17 Generate a frontend-displayable image on a common pixel grid.** *(Done; quality-mask overlay URL not built.)*
  Dep: P0-16, P0-04. Done: `GET /observations/{id}/image` accepts `centerRa`, `centerDec`, `fovArcmin`, reprojects the cutout onto a north-up grid with a fixed pixel scale, returns the same `gridId` for the same parameters (test: two observations from different pointings share width, height and grid), and returns `ObservationImage` with a loadable PNG URL, `width/height`, `stretch`, `wcs` (`approximate` set honestly), optional mask URL; image opens in a browser from the frontend origin (CORS verified).
- [x] **P0-18 `GET /observations/{id}/pixel-to-sky`.**
  Dep: P0-04. Done: returns sky coordinate; round-trip error under a stated tolerance in a synthetic-WCS test.
- [x] **P0-19 Quality flags decoded.** *(Done in `processing/quality.py` from IRSA online help; still to cross-check against the Explanatory Supplement.)*
  Dep: P0-16, A5. Done: flag decoder table sourced from the Explanatory Supplement (cited in code); unknown bits reported as `info` with raw value; `qualitySummary` rule documented and tested.
- [x] **P0-20 Async/“processing” behavior.** *(Done for images: a request waits up to 20 s for its image; slower downloads return HTTP 202 with `availability.status = processing` and `retryAfterSeconds = 3` while a background job continues, and the same GET later returns the image. Identical requests share one job; a failed job is reported as `failed`/`unavailable`, never stuck. The frontend already polls (frames.js, up to 6 tries). Comparisons are still synchronous: they wait for their images (up to 300 s) and never store a processing stub.)*
  Dep: P0-15. Done: slow fetch returns `processing` with `retryAfterSeconds`, then resolves; failure returns `failed` with a reason; tested with a slow stub.

### Comparison and measurement

- [x] **P0-21 Validate image alignment between two observations.** *(Done: `processing/alignment.py` matches bright stars between the two grid images and takes the median offset (robust to a moving object); wired into comparisons as the `alignment` check and `Alignment.residualArcsec`; `notEvaluated` when fewer than 3 stars match. 6 synthetic tests recover a known shift within 0.5 arcsec. Real data: 38 frame pairs, median offset 0.41 arcsec, max 1.4. It measures a residual only; it does not re-register the images.)*
  Dep: P0-16, P0-04. Done: reprojection of target onto base grid; residual (arcsec) computed from reference sources or stated as unmeasured; synthetic test with a known shift recovers it within a stated tolerance.
- [x] **P0-22 Compatibility checks and verdicts.** *(Done in `processing/compare.py` with 8 tests. `alignment` and `calibration` stay `notEvaluated`, so a comparison is at best `comparable_with_caveats`. The zodi threshold, 5%, is our heuristic.)*
  Dep: P0-21, P0-19. Done: checks for footprint, wavelength/bandpass, calibration/release, background (zodi), alignment, quality; verdict mapping implemented as in the pipeline doc; `notEvaluated` never counts as pass (unit tests for every verdict).
- [x] **P0-23 Scientifically valid comparison workflow.** *(`POST /comparisons`, `GET /comparisons/{id}`; blink/overlay/difference; cross-band differences refused; tested with a synthetic moved star.)*
  Dep: P0-22. Done: `POST /comparisons` and `GET /comparisons/{id}` return a `ComparisonResult`; `not_comparable` returns no difference image; shared grid dimensions verified; `comparisonKind` is set (`cross_band` is never presented as a temporal change).
- [x] **P0-24 Return brightness measurements with uncertainty.** *(Done: `processing/photometry.py`, `POST /measurements` (kind `aperture_flux`), `GET /measurements/{id}`. 18 photometry tests plus 6 API tests; the reported uncertainty was checked against the real scatter of 300 noisy synthetic frames. Unmeasurable regions return 422 `DATA_UNAVAILABLE`, never a zero.)*
  Dep: P0-16, P0-21. Done: `POST /measurements` returns `aperture_flux` with value, unit, uncertainty and `uncertaintyBasis`, method string (aperture, background, unit conversion); synthetic test matches the injected flux; a masked or non-detected region reports that, not zero.
- [x] **P0-25 Unit conversion documented and tested.** *(Flux density in mJy = sum of background-subtracted MJy/sr over the aperture x pixel solid angle (from the WCS) x 1e9; written in the module docstring and in every result's `method`; validated on an injected star of known total. It is a unit identity, so no external citation is needed beyond the IRSA statement that IMAGE is in MJy/sr. No aperture or colour correction.)*
  Dep: P0-24, A6. Done: MJy/sr → flux density conversion method written down, cited, and validated on a synthetic or known source.
- [x] **P0-26 Basic change detection.** *(Done: `POST /measurements` kind `flux_change` = target minus base in mJy, uncertainty in quadrature, `signalToNoise`, refused with 422 unless both frames are comparable AND at matched wavelength (within one bandwidth). Real-data check: 96 matched-wavelength pairs of one star gave |z| median 0.31, max 1.7; 715 unmatched pairs reached 4.5 sigma, which the rule prevents from being reported as change.)*
  Dep: P0-23, P0-24. Done: `flux_change` measurement with propagated uncertainty and caveats from the compatibility result; ratio-to-uncertainty is reported with no “detection” wording.

### AI (core)

- [x] **P0-27 LLM provider adapter with two configured providers and ordered fallback.** *(Done: `ai/llm.py`. Gemini and an OpenAI-compatible adapter, ordered chain, then the automated summary. Keys are `SecretStr`, sent only in headers, never logged (tested). Live check: one real call with `gemini-3.1-flash-lite` passed the grounding check; `gemini-2.5-flash` and `gemini-2.5-flash-lite` returned 404 for this key.)*
  Dep: D-1. Done: providers/models/keys come from env; call to provider 1 failing triggers provider 2, then the deterministic fallback; tested with a fake provider; keys never appear in logs or responses.
- [x] **P0-28 Tool functions returning structured results.** *(Done differently by design: no model-side tool calling. `ai/facts.py` assembles the object, observation, comparison and measurement results into numbered text facts before the model is called. The model never requests data.)*
  Dep: P0-13, P0-23, P0-24. Done: tools exist for object, observations, comparison, measurements (spectrum/candidates added later); each returns contract models.
- [x] **P0-29 AI companion receives structured measurement results.** *(Done: the prompt is JSON of `{id, text}` facts; tests assert it contains the measurement values and no image keys, URLs or base64, and that Gemini and OpenAI-compatible request bodies hold text parts only.)*
  Dep: P0-28. Done: for a context with a comparison, the prompt contains the `Measurement`/`ComparisonResult` objects (verified by a test that inspects the prompt), and no raw arrays, FITS, or image bytes. The LLM adapter has no image input path at all (a test asserts requests are text-only). *(Design rule from 2026-11-14: scientific computation first, AI interpretation second; see plan §9.)*
- [x] **P0-30 Chat endpoint.** *(Done: `POST /chat/messages`. Limits (changed 2026-11-15): per visitor 60, site-wide 10 per minute (a rush waits up to 12 s for a slot) and 1500 per day, plus a 45 s cool-down after a provider 429; when a limit is hit the visitor gets the friendly plain-language summary. Visitor key = `X-Client-Id` header, else the caller address. NOT done: persisting messages to an investigation (no investigation endpoints yet, `investigationId` is accepted and ignored).)*
  Dep: P0-27, P0-29. Done: `POST /chat/messages` returns a valid `AIResponse` with `references`, `caveats`, `suggestedActions`; honors `ChatContext.mode` and `candidateId`; messages persisted when `investigationId` given.
- [x] **P0-31 Grounding: structured output + numeric check.** *(Done: `ai/grounding.py`. Every digit-written number in answer and hypotheses must match a number in a cited fact (rounding accepted); one retry quoting the problems, then the automated summary. Known limit: numbers written as words are not caught (the prompt says to use digits).)*
  Dep: P0-30. Done: model output parsed as `{answer, citedResultIds, hypotheses}`; any number in `answer` not found in cited results triggers retry then the templated fallback; tested with fake-provider outputs that invent a number (must be rejected).
- [x] **P0-32 Verify AI explanations distinguish evidence from hypotheses.** *(Done: hypotheses are returned in a separate labelled list and shown under 'Possible explanations (hypotheses, not established findings)'; discovery/confirmation wording is rejected unless negated; backend-built caveats are always returned, whatever the model says.)*
  Dep: P0-31. Done: response separates cited evidence from labeled hypotheses; tests with fixed provider outputs assert that unsupported claims are demoted or removed; caveats always present when a comparison has `warn`/`fail`.

### Persistence, deploy, demo

- [x] **P0-33 Investigation persistence.** *(Done: `GET/POST /investigations`, `GET/PUT/DELETE /investigations/{id}`, owner-scoped to an anonymous visitor (X-Client-Id header, else the caller address), messages server-managed and appended by the chat endpoint when `investigationId` is given, limits (100 per visitor, 200 notes, 500 events). Stored in SQLite. CAVEAT: on Cloud Run the disk is temporary, so a redeploy or instance restart loses saved investigations; avoid redeploying during judging. The UI sends no X-Client-Id yet, so visitors are keyed by address until the frontend data layer adds it.)*
  Dep: P0-09. Done: SQLite store; create/get/list/update/delete per the contract; `messages` server-managed and ignored on PUT; survives restart. (Per-user scoping only if OD-7 is settled.)
- [x] **P0-34 Contract-validation tests for every implemented endpoint.** *(Done: one test per implemented operation validates the response against its model with no nulls, plus the error envelope. Operation 10 (spectrum) is not implemented yet and is asserted as such.)*
  Dep: each endpoint. Done: one test per operation validates the response model; runs offline.
- [x] **P0-35 Deploy with a persistent disk.** *(Done on Google Cloud Run (project navilink, us-central1, 2 CPU, 4 GiB, ONE instance because each instance has its own disk). Not a persistent disk: see P0-33 caveat.)*
  Dep: P0-05, D-3. Done: public URL reachable from the frontend origin; `/health` OK; cache directory persists across restarts; env keys set on the host only.
- [x] **P0-36 🛰 Real IRSA retrieval works from the deployed host.** *(Done: from the deployed host, name resolution, SIA2 discovery, cutout download, image rendering and Gemini chat all worked in a real browser test.)*
  Dep: P0-35, P0-15. Done: a live search → observations → image succeeds on the host; latency recorded; failure behaviors demonstrated.
- [x] **P0-37 Pre-cache the curated demo targets.** *(Done and verified on the REAL bundle (built 2026-11-15 by `backend/scripts/build_demo_bundle.py`, not in git): 245 frames (50 each for Barnard's Star, RW Aurigae, R Draconis, Epsilon Indi B; 45 for WISE 0855), 100 MB with slimmed cutouts. With the server's outside network blocked: all five targets resolve (0-8 ms), list in about 0.5-1.3 s from the saved listing, every frame's image is ready in under 20 ms, the PNGs download, a brightness measurement (32.2 +- 0.27 mJy) and a difference comparison (alignment 0.1 arcsec) work, a frame outside the bundle fails cleanly and retryably, and in a browser the first frame shows in 1 s. Rebuild the bundle if the grid, field of view or centring changes (image names depend on them).)*
  Dep: P0-06, D-7, P0-35. Done: each curated target loads instantly with the host's network disabled for IRSA (simulated by stub).
- [x] **P0-38 🛰 Complete one real astronomical investigation end to end.** *(Done: Epsilon Indi B, written up in `docs/INVESTIGATION_EPSILON_INDI_B.md`, reproducible with `backend/scripts/investigate_eps_indi_b.py`. Real frames, matched-wavelength pair 153 days apart, brightness 32.16 +- 0.27 and 31.42 +- 0.19 mJy, change -0.74 +- 0.34 mJy (2.2 sigma, consistent with no change), mismatched pair refused, alignment 0.36 arcsec, motion 4.55 vs catalogue 4.68 arcsec/yr (components 1.7 and 2.2 sigma off, reported as a consistency check only), AI answer citing the measurement, and a written list of what is and is not established.)*
  Dep: P0-06, P0-26, P0-32, P0-37. Done: chosen target: real observations retrieved; metadata interpreted; blink/comparison sequence rendered; at least one validated measurement with uncertainty; AI answers a user question citing those results; a short written account lists what is and isn't established.

---

## P1: Differentiators

- [ ] **P1-19 Co-add (stack) images per detector/bandpass (challenge "extra" item).**
  Dep: P0-17, OD-16. Done: contract addition agreed; stacking of same-bandpass aligned frames improves measured noise on a synthetic test, and the result is labelled as a derived stack with its input observation IDs.
- [x] **P1-20 Per-position spectrum on the fly.** *(Done: optional `ra`, `dec`, `apertureRadiusArcsec` (3 to 60); curated targets follow their proper motion per frame; the first answer waits up to 25 s, then HTTP 202 processing and the same GET returns the result (archive budget 25 s, 4 download workers, stored frames first, one probe download then give up if the archive is down); a complete spectrum is stored for good, an incomplete one for 10 minutes.)*
  Dep: P1-13. Done: `GET /objects/{id}/spectrum` accepts `ra`/`dec` and returns a spectrum for that position within a documented time budget.
- [x] **P1-21 Release-agnostic data discovery.**
  Dep: P0-13. Done: collections/releases are configuration (not hard-coded); a new release name added to config is searchable without code changes.

- [x] **P1-01 Agree contract v0.2 with the frontend developer (D-2).** Done 2026-11-14: contract 0.2.0 merged into `main`, frontend data layer updated.
- [x] **P1-02 Define the candidate-generation strategy.** *(Done: `services/candidates.py`. Catalogue-seeded: for a curated target, find the source near its expected position in each frame, track it relative to the surrounding stars, and test MOTION (validated straight-line fit) and BRIGHTNESS (flux differences between frames at matching wavelength). No candidate when neither stands out, and the page says why. The score and its basis are written in `SCORE_BASIS`; `LIMITATIONS` is returned with every candidate.)*
  Dep: P1-01. Done: the `Candidate` model is already fixed in the contract; the catalog-seeded strategy and ranking criteria (what goes in `score` and `scoreBasis`) are written in code docs; `limitations` content defined; `isProbability: false` and `status: "candidate"` enforced by Pydantic literals.
- [x] **P1-03 Proper-motion catalog lookup.** *(Done for the five curated targets: sourced proper motion with name, ID, epoch, unit and provenance `catalog`; objects without one omit the field. No uncertainty (not retrieved) and no lookup for other objects.)*
  Dep: P0-11, D-8. Done: catalog PM with name/ID/epoch/units/uncertainty returned with `provenance.kind = "catalog"` for at least one target; objects without PM omit the field.
- [x] **P1-04 Position measurement (centroid) with uncertainty.** *(Done: `processing/astrometry.py` tracks a source relative to the field stars (removing each frame's pointing offset) and fits a straight line; the result is exposed as a `derived_motion` measurement stored with each candidate and fetched by `GET /measurements/{id}`. Real data: 40 frames, 4.55 +- 0.49 arcsec/yr for Epsilon Indi B; frame-to-frame scatter of field-star positions 0.84 arcsec (the single-frame uncertainty used elsewhere). `POST /measurements` does NOT yet accept kinds `derived_motion` or `position_centroid` from a client.)*
  Dep: P0-16, P0-21. Done: `position_centroid` measurement; repeat-measurement scatter on synthetic and real data is reported (closes A8).
- [x] **P1-05 Verify position measurements and uncertainties before reporting motion.** *(Done: motion counts as measured only with at least 8 frames, 3 matched stars each, a 0.3 year baseline, scatter under 3 arcsec and 5 times its uncertainty; otherwise it is labelled an unvalidated displacement with every failed check listed. Unit tests cover each check failing alone and all together, and a candidate built from frames with a short baseline.)*
  Dep: P1-04. Done: motion is only reported when position uncertainties, WCS validity, and baseline pass documented thresholds; otherwise the result is `unvalidated_displacement` with a note; unit tests for both branches.
- [x] **P1-06 Distinguish catalog-provided proper motion from application-derived motion.** *(Done: a candidate's `properMotion` is always the catalogue value (provenance `catalog`) and its `motion` is always ours (method says 'Derived from observations' or 'Unvalidated displacement'); the AI's facts keep them apart. Tested.)*
  Dep: P1-03, P1-05. Done: API returns each with its own `provenance.kind`; a candidate cannot display a derived value labeled as catalog (test); the AI is told the provenance.
- [x] **P1-07 `GET /objects/{id}/candidates` and `GET /candidates/{id}`.** *(Done: `GET /objects/{id}/candidates` (limit, offset, minScore, sort) and `GET /candidates/{id}`; real tracks with sky and raster pixel positions per observation, evidence, warnings, limitations, a measurement in `evidenceRefs`, stable IDs. Searched in the background and stored (the bundle ships the results); the page says 'looking for candidates' while it runs.)*
  Dep: P1-02, P1-03, P1-04. Done: candidates matching contract §3 (`track` with sky and pixel positions on the image grid, `score` + `scoreBasis`, `evidence`, `warnings`, `limitations`), with real measurement IDs in `evidenceRefs`; stable IDs across calls; empty list with `coverageNote` when none.
- [x] **P1-08 Ranking is defensible.** *(Done: `isProbability` is always false; the score is a documented heuristic (motion term, brightness term, bonus, quality factor); tests prove ordering follows the criteria and that no candidate text uses discovery, confirmation or probability language.)*
  Dep: P1-07. Done: `scoreBasis` explains the heuristic; `isProbability` is always `false`; no field named or described as probability/confidence; unit tests prove ordering follows the stated criteria; documentation states ranking = prioritization, not validation.
- [x] **P1-09 Support the frontend-scripted guided investigation with real data.** *(Done: `GET /guided/spot-the-shift` (frontend-only, not in the contract) builds the guided definition from a real candidate: true hints (position, direction, size of the shift), a 14 pixel click tolerance, and honest wording that the shift is small. The UI needs one data-layer line to use it (`getGuidedInvestigation` calls `/guided/{id}`; committed on the `deploy` branch, not on `front-end`). Verified in a real browser: start, browse dates, wrong click rejected, three real hints, correct click advances.)*
  Dep: P1-07. Done: for the chosen demo target the backend returns at least one real candidate whose `track` shows a measured shift above its uncertainty (or states clearly that none exists), so the frontend's "Spot the shift" can run on real data; coordinated with the frontend developer.
- [x] **P1-10 AI companion explains guided steps from structured results.** *(Done: with a candidate in context Navi explains it from the supplied facts only; tested live with Gemini: 'did we just discover something new?' answers that nothing is established, and asking for a confidence percentage is declined. The AI does not know which guided step the user is on (the contract's ChatContext has no guided state), so help with a step is generic.)*
  Dep: P1-09, P0-31. Done: given a guided-step question with `mode: "beginner"`, the answer uses only supplied results, passes the numeric check, and avoids implying a phenomenon the data does not support.
- [x] **P1-11 AI tools for candidates and proper motion.** *(Done: candidate facts (description, evidence, score and its basis, limitations, software notes) reach the model; tests forbid confirmed/discovered/proven wording in what the model is given and in the candidate itself.)*
  Dep: P1-07, P0-28. Done: companion can answer “why is this a candidate?” citing candidate evidence IDs; never uses “confirmed”/“discovered” (test with forbidden-phrase checks).
- [x] **P1-12 Investigation summary generation.** *(Done on the backend: when `investigationId` is sent, the visitor's own saved notes reach the model as 'written by the user' and the summary keeps them apart from measurements (also in the no-AI summary). The UI's Summarise button does not send an investigationId yet, and notes kept only in the browser are invisible to the backend.)*
  Dep: P0-33, P0-31. Done: summary built from stored notes/measurements, grounded via the numeric check.
- [x] **P1-13 One real spectrum.** *(Done: `GET /objects/{id}/spectrum` returns SpectralData built from many observations: aperture photometry per frame at the wavelength at the object's pixel, one point per band (best signal-to-noise, not an average), gaps with reasons, 102 expected bands, units stated, warnings for dates, estimated band numbers, coverage and the approximate flux scale. Verified on real data (Epsilon Indi B shows the two-peaked T-dwarf shape; Barnard's Star and R Draconis are refused because they saturate) and in the real Spectrum page.)*
  Dep: P0-16, D-5, A10. Done: `GET /objects/{id}/spectrum` returns `SpectralData` for one target from the chosen source; gaps are `available: false`; units stated; bands count honestly reported vs `expectedBandCount`.
- [ ] **P1-14 Selected band/wavelength in AI context.**
  Dep: P1-13, P0-29. Done: `ChatContext` selection reaches tools; test shows a band-specific question uses that band's data.
- [ ] **P1-15 Larger field-of-view requests.**
  Dep: P0-17. Done: `fovArcmin` up to a documented maximum works with caching and time budget; larger values return `INVALID_REQUEST` with a clear message.
- [ ] **P1-16 Science-grade wavelength (CWAVE/CBAND) if the WCS-WAVE accuracy is not enough.**
  Dep: A9. Done: comparison between the two methods is recorded; decision documented.
- [x] **P1-17 Rate/backoff guard for IRSA.**
  Dep: P0-15. Done: global concurrency cap and retry-with-backoff configurable; test with a failing stub.
- [ ] **P1-18 Data acknowledgment text.**
  Dep: none. Done: required IRSA/SPHEREx acknowledgment confirmed from IRSA and surfaced to the frontend developer for display; WISEView and other resources cited in the README.

## P2: If time permits

- [ ] **P2-08 Catalogue overlays (e.g. Gaia, WISE).** Dep: OD-17, P0-17. Done: contract addition agreed; sources inside the field returned with positions on the image grid and clear provenance.

- [ ] **P2-01 Difference-image mode quality.** Dep: P0-23, P0-22. Done: zodi handling explicit and warned; difference image shown only with caveats.
- [ ] **P2-02 Automated source differencing for extra candidates.** Dep: P1-07. Done: documented false-positive behavior on synthetic data; candidates flagged with extra limitations; disabled by default if not validated.
- [ ] **P2-03 Cross-observation spectral comparison.** Dep: P1-13. Done: compatibility-gated spectrum comparison.
- [ ] **P2-04 Backend-generated guided challenges** (only if both developers want them). Dep: P1-10. Done: contract addition agreed first; each challenge gated on evidence.
- [ ] **P2-05 Streaming chat (OD-8).** Dep: P0-30. Done: agreed contract change and tested.
- [ ] **P2-06 Larger curated target set and cache warming script.** Dep: P0-37.
- [ ] **P2-07 Basic CI** running `pytest -m "not realdata"`. Dep: P0-10. Done: workflow passes on the branch.

---

## Added from the first real-data run (2026-11-14)

- [x] **P0-22b Pre-warm script for the curated demo targets.** *(`backend/scripts/prewarm.py`.)* Dep: P0-15. Done: one command downloads and renders images for the five challenge targets at the default field of view into the cache (cold frames take 3-8 s each; 4 MB per cutout).
- [x] **P0-22c Same-band timeline.** *(Frontend data layer now requests one band, default SPHEREx-D3; `?band=` overrides. Still pending in the contract.)* Dep: P0-13. Done: backend supports `bandpass` filtering (implemented) and contract/frontend agree that a blink series is requested per bandpass (contract addition pending).
- [x] **P0-22d Pagination guard.** Dep: P0-13. Done: confirmed the frontend pages through observations (Barnard's Star has 363, R Draconis 2514; the API caps a page at 200). *(Finding: the frontend does NOT page. It calls the observation list once with no limit or offset, so it gets the first 50 observations of the band (oldest first). Barnard's Star has 60 in D3 and R Draconis 576, so the UI shows only the earliest 50. Not changed on the backend (raising the default would make the UI preload up to 200 frames). The backend now sets a `coverageNote` on any truncated page, e.g. 'Showing observations 1-50 of 576 matching this view (oldest first).')*

## Cross-cutting acceptance

- [ ] **X-01 No scientific calculation in the LLM or frontend.** Done: grep/test confirms the AI package does no arithmetic on results; tools return numbers computed in `processing`.
- [ ] **X-02 Raw / processed / derived / AI tiers are separated in the API.** Done: no endpoint returns AI text in a data field or data in AI text without references.
- [ ] **X-03 No secrets committed.** Done: `git grep` for key patterns clean; `.env` ignored.
- [ ] **X-04 Provenance everywhere.** Done: measurements state method/units/uncertainty basis; proper motion states its source kind.

---

## Final demo-readiness checklist

- [ ] Deployed backend URL is stable and reachable from the frontend origin (CORS verified).
- [ ] `/health` returns the contract version that the frontend uses.
- [ ] Curated targets load without a live IRSA call; an uncurated target shows a fetching state and either succeeds or fails gracefully.
- [ ] One full investigation runs: search → observations → image → compare (blink + difference where allowed) → measurement with uncertainty → AI question with references and caveats → note saved → reload shows saved investigation.
- [ ] AI provider chain verified from the deployed host; killing provider 1 falls back to provider 2; killing both gives the labeled automated summary.
- [ ] AI cannot be made to invent a number in a scripted adversarial test (e.g. “what is the flux at 4 um?” when none exists → says unavailable).
- [ ] No response labels a candidate confirmed or presents a ranking as a probability.
- [ ] Proper-motion values show their provenance kind.
- [ ] Guided challenge appears only when evidence supports it (or is clearly marked illustrative).
- [ ] All endpoints contract-valid in the integration test run; no `null` optionals.
- [ ] Everything shown from mock/recorded data is labeled as such.
- [ ] Required data acknowledgments and resource citations present.
- [ ] Demo script run three times consecutively without intervention; backup recording saved.
- [ ] Freeze time set (D-9) and honored; no feature work after it.
