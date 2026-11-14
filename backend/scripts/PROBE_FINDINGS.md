# IRSA probe findings (2026-11-14)

Target: WISE 0855 (RA 133.79513, Dec -7.24515). Scripts: `probe_irsa.py`, `probe_cutout.py`. Everything below was observed directly.

## TAP (P0-01, P0-02)
- `Irsa.query_tap` joining `spherex.artifact a` and `spherex.plane p` on `planeid` with `CONTAINS(POINT('ICRS',ra,dec), p.poly)` works. No query restricted to a collection; all rows came from `dr1`.
- Result: **258 files**, 6 detectors/bandpasses, **27 distinct observation days**, MJD 60800.70 to 61174.29 (about 12 months).
- Same-band epochs at this position: D1 8 days, D2 14, D3 13, D4 8, D5 14, D6 13.
- Useful `spherex.plane` columns (exist): `obsid`, `productid`, `datarelease`, `calibrationlevel`, `quality_flag`, `metrics_background`, `metrics_backgroundstddev`, `metrics_maglimit`, `position_samplesize` (arcsec/pixel), `position_resolution`, `energy_bounds_lower/upper` (m), `energy_bandpassname` (`SPHEREx-D1..D6`), `time_bounds_lower/upper` (MJD), `time_exposure`, `provenance_version`.
- `spherex.artifact` columns: `uri`, `producttype`, `releasetype`, `contenttype`, `contentlength`, `contentchecksum`, `ipac_pub_date`. The query did not filter `producttype`; add `producttype = 'science'` when building the real client.
- `time_bounds_lower` equals `MJD-OBS` in the file (60800.700028).

## Cutout MEF (P0-03, P0-04)
- URL `https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=0.05` returned a 29x29 pixel cutout (0.05 deg = 180 arcsec, so about 6.2 arcsec/pixel).
- HDUs: PRIMARY (header only, `VERSION`), IMAGE, FLAGS (int32), VARIANCE, ZODI, EPSF (table, 451 rows, includes `CWAVE`,`CBAND`), WCS-WAVE (table, `X`(9), `Y`(14), `VALUES`(14,9,2)).
- IMAGE `BUNIT` = `MJy / sr`. WCS is `RA---TAN-SIP` / `DEC--TAN-SIP`: **distortion terms present**, so `WcsSummary.approximate` must be `true`; use astropy `WCS(header)` for exact conversions. Pixel-to-sky at the cutout center gave (133.79520, -7.24654), about 5 arcsec from the target.
- IMAGE header has `DETECTOR`, `OBSID` (e.g. `2025W19_1B_0116_1`), `EXPIDN`, `DATE` (a processing date, not the observation time). **Observation time keywords (`DATE-OBS`, `MJD-OBS`, `MJD-AVG`, `XPOSURE`) are in the VARIANCE and ZODI headers**, not IMAGE.
- IMAGE and VARIANCE were 100% finite in this cutout.
- FLAGS values in this cutout: 0 (501 px), 524288 = bit 19 (14 px), 2097152 = bit 21 (324 px), 2097221 = bit 21 + bits 0, 2, 6 (2 px). **About 40% of pixels were flagged**, so flag meaning matters. Bit meanings still need the Explanatory Supplement (checklist P0-19, assumption A5).

## Discovery: use SIA2, not TAP (observed 2026-11-14)
- For WISE 0855 the **SIA2** query (`https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_dr1&POS=circle+ra+dec+0.01&RESPONSEFORMAT=votable`) returned the same 258 files in **about 1.3 s**. The equivalent **TAP join took about 310 s**, and for Barnard's Star the TAP request failed with HTTP 502 (proxy error). TAP is not used for discovery. Even a `GROUP BY` over the artifact table returned 502.
- SIA2 returns standard ObsCore columns: `access_url`, `obs_collection`, `obs_id`, `energy_bandpassname`, `em_min`/`em_max` (metres), `em_res_power`, `t_min`/`t_max` (MJD), `t_exptime`, `s_pixel_scale` (6.15 arcsec/pixel), `s_ra`/`s_dec` (centre of the full 2040x2040 detector image, about 3.5 degrees across), `s_region` (footprint polygon), `obs_release_date`, `target_name` (survey field name), `access_estsize`.
- The VOTable fields carry generic IDs (`col_0`, ...); read by name (`to_table(use_names_over_ids=True)`).
- `qr3` returned 0 rows for WISE 0855; `dr1` returned all 258.

## Cutout downloads
- Each cutout MEF is about **4 MB** (the EPSF PSF table dominates), and took **3 to 8 s** to download. Four concurrent downloads gave about 1.5 s per frame effective. Cold frames are slow, so curated targets must be pre-cached; cached reads are instant.
- A real end-to-end test (resolve, list 258 observations, 2 images on one grid) runs in about 19 s cold.

## Flag bits (official IRSA help, read through a summarizing fetch; cross-check with the Explanatory Supplement)
- Bits seen in the real cutout: 19 = OUTLIER, 21 = SOURCE ("known source"), 0 = TRANSIENT, 2 = SUR_ERROR, 6 = NONFUNC. The 40% "flagged" pixels were almost all **SOURCE** (known sources), not bad data. Decoder: `backend/processing/quality.py`. Severity labels are our own classification.

## Coverage of the five challenge targets (SIA2, all releases searched)
| Target | Files | Distinct days | Max same-band days | Releases |
|---|---|---|---|---|
| Barnard's Star (269.4521, +4.6934) | 363 | 41 | 20 | dr1, qr3 |
| Epsilon Indi B (331.0441, -56.7828) | 467 | 60 | 25 | dr1, qr3 |
| WISE 0855 (133.7951, -7.2451) | 258 | 27 | 14 | dr1 |
| RW Aurigae (76.9565, +30.4014) | 336 | 35 | 14 | dr1, qr3 |
| R Draconis (248.1676, +66.7550) | 2514 | 223 | 113 | dr1, dr1_deep, qr3, qr3_deep |

R Draconis sits in a deep-survey field. "Distinct days" do not show how clustered the dates are; spacing was not examined.

## Integration check (teammate's real client, live backend)
The frontend's `client.js` with `useMock=false` ran against the live backend: search, 363 observations for Barnard's Star, three D3 images with one shared `gridId`, PNG fetch, pixel-to-sky, error mapping. Candidates returned 404 (not built yet) and object extras were empty (no proper-motion catalogue yet).

## Consequences for the plan
- Multi-epoch same-band data **exists for all five targets** (A3 holds). Same-band epoch counts range from 8 to 113.
- Only one file was downloaded; assumptions A4, A6, A7, A8, A9, A13 are still unverified.
