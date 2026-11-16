# Is anything hard-coded? What is verified?

Audited 2026-11-15. Short answer: **no science result is typed into the code.** Every image, flux, spectrum, uncertainty and candidate is computed from NASA's data at the time it is built. What is written in the code is listed below, with how each item was checked.

## Typed into the code, and how it was verified

| Item | Where | Status |
|---|---|---|
| Position and proper motion of the five curated targets (Barnard's Star, WISE 0855-0714, RW Aurigae, R Draconis, Epsilon Indi B) | `backend/services/targets.py` | **Re-verified against live SIMBAD on 2026-11-15**: all five positions agree to 0.000 arcsec and all ten proper-motion values are identical. They are also the values the app would now fetch for itself (`services/pm_lookup.py`). The table exists so these targets work offline and for spellings the name service does not know. |
| Proper motion for every other searched name | fetched live | Not typed: SIMBAD via CDS Sesame at search time, with the source and retrieval date shown. Checked against Gliese 876 (+957.7, -673.6 mas/yr, matches the literature). |
| Mission facts: 102 spectral bands, six detectors, 0.75 to 5 micrometres, 6.15 arcsec pixels | `services/spectrum.py`, Navi's guide text | From IRSA's SPHEREx documentation and the archive's own metadata. The **band numbers are our estimate** (each detector's range split into 17 log-spaced channels; this matches the measured bandwidth) and the app says so on the spectrum page. The wavelengths themselves come from the archive. |
| Quality-flag meanings | `processing/quality.py` | From IRSA's flag table; bits IRSA does not define are shown as "no definition in IRSA's table". |
| Thresholds: 3 percent repeatability floor, quarter-bandwidth wavelength match, candidate validation limits, 8 same-wavelength frames, 5 sigma motion | `processing/photometry.py`, `compare.py`, `astrometry.py` | Our own documented choices, each measured on real data (`docs/SPECTRUM_VALIDATION.md`, `docs/CANDIDATE_VALIDATION.md`). Not NASA values; labelled as heuristics wherever shown. |
| The ranking score | `services/candidates.py` | A heuristic, documented in `SCORE_BASIS`, never a probability. |
| Wording: Navi's tour lines, the glossary, guide facts, the data credits | frontend, `ai/facts.py`, `services/credits.py` | Text, not data. The acknowledgment sentences are copied word for word from IRSA's pages. The guided activity's hints are generated from the real candidate's measurements. |

## Not in the live site

- **Mock data**: the frontend ships sample fixtures (`frontend/js/api/mock/`) for development. The deployed build switches them off at build time (`useMock: false`, checked on the live site), and every "sample data" label is rewritten.
- **The stored demo data** (pictures, spectra, candidates) are real results computed by this app from IRSA's files, then stored; they were rebuilt after each correction (for example the aperture fix) and can be recomputed with `scripts/ship_finalize.py`.

## Known gaps in verification

- The absolute flux scale has not been compared with independent photometry (catalogue services were unreachable); no aperture correction is applied (`docs/SPECTRUM_VALIDATION.md`).
- Detector-to-detector calibration offsets could not be tested.
- Targets resolved by name only (before 2026-11-15) had been measured at their year-2000 position; this was found and fixed, and those stored results were discarded.
