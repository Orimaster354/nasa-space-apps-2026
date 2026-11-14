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

## Consequences for the plan
- Multi-epoch same-band data **does exist** at this target, so the "video" is feasible (assumption A3 holds here; still check the other four targets, P0-06).
- Only one file was downloaded; assumptions A4, A6, A7, A8, A9, A13 are still unverified.
