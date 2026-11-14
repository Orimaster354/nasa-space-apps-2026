# Scientific Data Pipeline

> Contract version **0.2.0 (draft)**. Owner: Backend. Describes the *intended* pipeline; nothing here is implemented yet.
>
> **Verification note.** Statements about SPHEREx data products (file layout, extension names, flag meanings, spectral calibration, release naming, access methods) are not asserted here as facts. They must be confirmed against IRSA's current SPHEREx documentation during the IRSA spike (OD-12). Where this document describes mission characteristics (e.g. 102 spectral bands), treat them as "to confirm".

## 1. Four tiers of information

Every value the app shows belongs to exactly one tier, and the UI and AI must not blur them.

| Tier | What it is | Where it lives | Examples | Who creates it |
|---|---|---|---|---|
| **1. Raw observation** | Files and metadata exactly as the archive provides them | `backend/data/` cache; `Observation.sourceMetadata` | FITS files, headers, archive flags | NASA/IRSA |
| **2. Processed data** | Raw data read and normalized without changing the science content | In-memory arrays; display rasters | Parsed arrays, normalized metadata, rendered PNG with a stretch, resampled image on a common grid | Backend |
| **3. Derived measurement** | Numbers computed from tier 1-2, with method and uncertainty | `Measurement`, `SpectralData`, `ComparisonResult` | Aperture flux, flux change, spectrum, compatibility verdict | Backend (deterministic code) |
| **4. AI-generated explanation** | Natural-language interpretation of tier 3 results | `AIResponse.message` | "This change is within the noise." | LLM, constrained by backend |

Rules:
- A higher tier is always traceable to the lower tier (IDs, `method`, `observationIds`).
- Tier 2 rendering (stretch, color) is for viewing. It is **not** measurement.
- Measurements are computed from data arrays, never from rendered PNGs.
- Tier 4 never introduces numbers that are not in tier 3.

## 2. IRSA data access

1. Resolve the user's object name/coordinates to RA/Dec (name resolver, OD-4).
2. Query IRSA for SPHEREx observations covering that position (and optionally a time/release filter).
3. Record each result as an `Observation` (metadata only). Save raw archive values in `sourceMetadata`; normalize only the fields the contract defines, and omit any that the archive did not provide.
4. Download image files on demand (when an image, comparison, measurement, or spectrum needs them) into `backend/data/`.

Open items (OD-12): which data products are in scope (e.g. calibrated spectral images vs higher-level products), which releases, which access method (IRSA offers programmatic services; choose one after the spike), authentication requirements if any, and download size/time budget. Pre-warm the cache for demo objects.

Failure handling: archive unreachable → `UPSTREAM_UNAVAILABLE`; no coverage → empty `Page` with `coverageNote`; partial download → `Availability.status = "failed"` with a retry hint.

## 3. FITS retrieval and parsing

- Open files read-only; never modify cached originals.
- Extract: image array(s), any uncertainty/variance information, any quality/flag information, and headers.
- Do **not** assume every file has all of these. Absent planes are recorded as missing and surface as warnings (`uncertainty` omitted, `qualityFlags` limited).
- Keep units exactly as given in headers. Any unit conversion is explicit, minimal, and written into `method`/`extractionMethod`.
- Map archive flags to `QualityFlag` entries (`origin: "source"`), adding our `severity` classification. The mapping table is maintained next to the code and reviewed against archive documentation.

## 4. WCS interpretation

- Read the sky WCS from headers and build a pixel↔sky transform with a standard library (library choice is an implementation decision).
- Expose a reduced `WcsSummary` to the frontend with `approximate: true` if the solution has terms not captured by the summary (e.g. distortion).
- Precise pixel→sky for the UI goes through `GET /observations/{id}/pixel-to-sky` (OD-6).
- Wavelength information: for instruments where wavelength varies with position on the detector, there may be a separate spectral calibration/WCS. How SPHEREx encodes this must be confirmed (OD-12). The consequence for the product is important: one image may cover a **range** of wavelengths (hence `Observation.wavelengthRangeUm`), and the wavelength at a given source position must be derived per position, not assumed to be a single value per image.
- Validate before use: WCS present, finite, footprint consistent with the image size. Failure ⇒ image/comparison `unavailable` or `failed`, never silently guessed.

## 5. Image alignment and observation comparison

**Compatibility gate (always runs first).** Before any blink, overlay, difference, or flux comparison, the backend evaluates:

| Check | Question | Fail/warn examples |
|---|---|---|
| `footprint` | Do the observations overlap the region of interest? | No overlap ⇒ `fail` |
| `wavelength` | Do they cover compatible wavelengths **at the compared position**? | Non-overlapping ranges ⇒ `fail` (different bands are not a time-series comparison) |
| `calibration` | Is flux calibration comparable (same release / same calibration basis)? | Different releases ⇒ `warn` or `notEvaluated` unless verified |
| `alignment` | Can they be registered, and what is the residual? | Residual larger than a stated threshold ⇒ `warn`/`fail` |
| `quality` | Are flags/masks affecting the region? | Masked region ⇒ `warn`/`fail` |
| `dataRelease` | Same release? | Different ⇒ `warn` |
| `background` | Is the background (e.g. the modelled zodiacal light, which is not subtracted from the archive image) comparable between the two? | Large background change ⇒ `warn`; not evaluated ⇒ `notEvaluated` |

Candidates ([DATA_MODELS.md §10](DATA_MODELS.md)) come from the same compatibility-gated measurements; their `score` ranks sources for human follow-up and is never a probability or a validation.

Verdict mapping: any `fail` ⇒ `not_comparable`; no `fail` but any `warn` or `notEvaluated` ⇒ `comparable_with_caveats`; all `pass` ⇒ `comparable`; insufficient information ⇒ `unknown`. Thresholds are implementation choices and must be documented in code and tested. **`notEvaluated` is shown to the user**, never treated as `pass`.

**Alignment.** Resample the target (or both) onto a common grid using WCS reprojection, with a method that suits the use (flux-conserving for photometry, any for display; choice is an implementation decision). Record `alignment.method` and `residualArcsec` if measured. Do not align by eye or image features without recording it.

**Modes.**
- `blink`, `overlay`: aligned `baseImage`/`targetImage` on one grid.
- `difference`: `differenceImage` plus stated caveats. Differences between observations at different times can come from alignment error, resampling, noise, detector/calibration differences, and real change. Scope of allowed pairs is OD-13.

## 6. Brightness measurements

- Computed in the backend from data arrays: e.g. aperture photometry in a user/region-defined aperture, with a documented background estimate.
- Each `Measurement` states `method` (aperture size, background method, alignment used), `unit`, and `uncertainty` + `uncertaintyBasis` if obtainable.
- Flux-change measurements are only produced if the compatibility verdict permits it (not `not_comparable`) and carry the verdict's warnings forward.
- No measurement is reported without units. Non-detections and masked regions are reported as such, not as zero.

## 7. Uncertainty and quality assessment

- **Uncertainty.** Use archive-provided uncertainty/variance information if present and propagate it explicitly; if absent, either estimate from the data with a stated method (`uncertaintyBasis`) or omit the field. Never display error bars the backend did not produce.
- **Quality.** Source flags are preserved; our derived flags (e.g. region partially masked, low coverage, alignment residual high) are added with `origin: "derived"`. `qualitySummary` is a documented, rule-based roll-up.
- **Significance.** Where a change is reported, the backend also supplies enough to judge it (value, uncertainty). The AI is told the comparison to uncertainty; it does not decide significance thresholds itself.

## 8. Spectral data handling

- Mission design reference: a fixed set of spectral bands across the near-infrared (documented as 102 bands; confirm against official documentation). Exposed as `expectedBandCount` for display of coverage and gaps.
- A spectrum for a position is **assembled from multiple observations**, because a single image covers only part of the wavelength range. Each `SpectralPoint` therefore carries its own `observationId` and `observedAt`.
- Points from different observation times are not automatically comparable for variable sources; the backend adds a warning when the time spread is not small or unknown.
- Bands with no usable data are emitted as `available: false` points with a reason, so gaps are visible rather than interpolated away. No interpolation or smoothing is applied to the delivered points unless explicitly declared in `extractionMethod` (and then the original points should remain available).
- Flux units come from the archive; any conversion is stated.
- How bands are mapped to wavelengths and which pixels contribute at a position: OD-12.

<a id="9-what-the-ai-companion-may-conclude"></a>
## 9. What the AI companion may conclude

**Inputs.** The AI receives only: the user's message, `ChatContext`, and structured results fetched through backend service tools (`Observation`, `ComparisonResult`, `Measurement`, `SpectralData`, `AstronomicalObject`). It never receives raw FITS files or unprocessed arrays and never computes numbers.

**May do:**
- Explain what a measurement, comparison, quality flag, or spectral feature *means*, in plain language and with general astronomy background.
- Restate numbers exactly as provided, with units and uncertainty.
- Say whether a result is within its stated uncertainty, using the numbers provided.
- Point out caveats present in the data (masks, compatibility `warn`/`fail`, gaps, mixed observation dates).
- Suggest next steps (open another observation, run a comparison, add a note) as `suggestedActions`.
- State clearly that it cannot answer, or that data is unavailable.

**Must not do:**
- Invent or alter numbers, uncertainties, dates, wavelengths, IDs, or flag meanings.
- Declare a detection, variability, transient, or physical interpretation (e.g. specific molecule, redshift, object class) beyond what the structured results support. General background may be given but labeled as such, not as a finding about this data.
- Treat `notEvaluated` or `unknown` as passing.
- Compare observations the compatibility check marked `not_comparable`, or present a difference as real change when the verdict has caveats without stating them.
- Claim to have examined an image's pixels.
- Answer from memory about archive specifics (file formats, calibration) as if verified.

**Enforced by the backend, not only by the prompt:**
- `references` lists the result IDs supplied to the model; the response is rejected or flagged if its content cites numbers absent from them (practical implementation decided during build; at minimum compare numeric tokens).
- `caveats` are generated by the backend from result warnings and always returned with the answer.
- If tools return `unavailable`/`failed`, the model is told so and must say so.

**Contract support.** `AIResponse` keeps the AI-generated text (`message`), the results it relied on (`references`), and `caveats` as separate fields, so any client can keep explanation distinct from measured values. How that is presented is the frontend developer's decision.

## 10. Testing expectations (backend)

- Unit tests for each processing function with small synthetic arrays and a synthetic WCS (no network).
- Tests for compatibility verdict logic, covering each check and the verdict mapping.
- Tests that missing planes/headers produce omissions and warnings rather than crashes or zeros.
- Contract tests: endpoint responses validate against the contract types.
- A recorded-fixture mode for IRSA responses so tests and demos run offline.
