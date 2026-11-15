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

## Photometry on real cutouts (2026-11-14)
- Measured one bright field star at the same sky position in 50 cached D3 frames (12 arcsec aperture, 24-36 arcsec ring): 43 measured, 7 refused (ring or aperture mostly masked/off-image), none returned as zero.
- **Bug found only on real data:** `WCS.all_world2pix` raises `NoConvergence` for positions far from a TAN-SIP image. Now handled as "unmeasurable" (regression test added).
- **The archive VARIANCE plane is smaller than the real scatter.** On one frame: archive sigma 0.019, pixel-to-pixel scatter 0.024 (1.3x), ring scatter 0.036 (1.9x) in MJy/sr. In crowded/bright fields the ring scatter is far larger (structure, not noise). Our uncertainty therefore rescales the archive variance up to the ring scatter and warns; it is conservative, not exact.
- **Resolved: the flux variation is almost entirely wavelength.** The WCS-WAVE extension (one row; X and Y node pixels; VALUES of shape (Y, X, 2) = wavelength and bandwidth in um; cutout pixel mapped to node coordinates by the IMAGE header keys CRPIX/CDELT/CRVAL 1W and 2W) gives the wavelength at each pixel. Across the 43 measured frames the star fell at 1.64 to 2.40 um (median bandwidth 0.046 um); flux correlated with wavelength at -0.95 and with date at only -0.16. Removing a smooth wavelength trend cut the scatter from 6.1 to 1.7 mJy, below our median reported uncertainty of 4.1 mJy (so our uncertainties are conservative). Consequence: **same bandpass does not mean same wavelength.** Brightness differences between pointings must be compared at matched wavelength, or reported as spectral. We call wavelengths matched when they differ by no more than one bandwidth (our heuristic).
- **Flux-change check on real data.** Same star, different-epoch pairs (visits at least 0.5 day apart). Matched wavelength (within one bandwidth): n=96, change/uncertainty has median |z| 0.31, max 1.69, std 0.48, consistent with no change and confirming our uncertainties are conservative. Unmatched wavelength: n=715, median |z| 1.16, max 4.51, which would look like significant changes. This is why `flux_change` refuses unmatched pairs.
- **Frame-to-frame alignment on real data.** 38 random frame pairs (D3, 6 arcmin field, 2 arcsec grid): median star-position offset 0.41 arcsec, 90th percentile 1.05, max 1.41, from a median of 10 matched stars (2 pairs had too few stars). That is under a quarter of a native pixel, so the WCS alignment is good and misregistration does NOT explain the bright/dark pairs seen at stars in difference images. More likely causes (not yet separated): different flux at different wavelengths, and a PSF shape that changes with wavelength and position, plus resampling of an undersampled PSF. Re-registering the images is therefore not needed for the demo.

## Curated targets and proper motion (2026-11-15)
- CDS Sesame (SIMBAD data) gives J2000 positions and proper motion (mas/yr, pmRA includes cos(dec)): Barnard's Star (-801.6, +10362.4), WISE J085510.83-071442.5 (-8123.7, +673.2), eps Ind B (+3982.0, -2466.8), RW Aur (+7.2, -20.3), R Dra (-20.6, +6.1). Sesame does NOT know the names "WISE 0855" or "WISE 0855-0714"; it knows the J-designation. SIMBAD's TAP service was returning 404 for its capabilities that day.
- **Proper motion moves three targets minutes of arc away from their catalogue position.** Over 26 years Barnard's Star moves about 270 arcsec north. Verified on real frames: at the predicted position the image is about 1000x the sky median with 78 to 100 percent of those pixels flagged unusable (saturated), and flat sky at the catalogue position.
- WISE 0855: the challenge text's coordinate (133.795129, -7.245147) matches the Sesame position plus proper motion to about 0.4 arcsec if the challenge coordinate is at epoch 2010.4 (RA and Dec components give the same epoch independently). This supports the J2000 epoch assumption.
- After centring each field where the object is expected at epoch 2026.0, real D3 frames show a source at the field centre: Barnard's Star 6000x to 10000x the sky median (84 to 100 percent saturated), Epsilon Indi B 33x to 92x, RW Aur about 1000x, R Dra about 15000x (saturated). WISE 0855 shows only a faint, consistent excess (3x to 4x the median, nothing flagged) in three epochs; that is NOT a confirmed detection.
- Consequence for the demo: Barnard's Star and R Draconis are saturated in SPHEREx, so aperture photometry there is refused (the aperture is masked). Their motion or variability shows only as a moving or changing masked blob, not as a flux.

## IRSA reliability (2026-11-15)
- During the build of the demo bundle two archive collections timed out after 60 s and two returned 502 for several minutes, while the rest of IRSA answered. Earlier the same day the archive also returned 502 for a few seconds. The app now retries, never reports "no coverage" when part of the archive is silent, and falls back to a saved listing; the demo bundle exists so the five targets do not depend on the archive at all.

## Demo bundle and archive behaviour (2026-11-15)
- Building the bundle: the archive returned 502 or timed out for roughly the first five minutes, then recovered; the builder waited and retried by itself. 245 frames, 1069 MB of cutouts slimmed to 100 MB (the PSF table is nearly all of a cutout's size and is never read).
- With outside network blocked, asking the archive first made the observation list take 13 s before falling back; serving a recent saved listing immediately (refresh in the background) made it 0.5 s. SIA queries now use 2 attempts of at most 20 s each.

## Spectra (2026-11-15)
- SPHEREx sees one wavelength per object per exposure, so a spectrum is built from many frames: flux at the object against the wavelength at its pixel. Band numbers are not in the archive; each detector's range is split into 17 channels equally spaced in log wavelength (102 in total). Check: the measured per-pixel bandwidth in D3 is 2.4 to 2.5 percent of the wavelength, and 17 log channels give 2.39 percent.
- **Epsilon Indi B** (D3 only, from the demo bundle): 31.4 mJy at 1.64 um, a minimum of 9.9 mJy at 1.86 um, a second peak of 29.0 mJy at 2.09 um, then falling to about 12 mJy beyond 2.3 um. Two peaks with a dip between them is the shape expected of a T dwarf, so the wavelength calibration, photometry and band mapping agree with the physics. With the live archive added (D2, D4, D5) it rises again to 26 to 56 mJy at 3.9 to 4.4 um, as a cool brown dwarf should.
- **WISE 0855**: 0.5 to 0.95 mJy at 1.6 to 2.4 um, smoothly falling with wavelength. A Y dwarf this cold should be far fainter than that in the near infrared, so those points are very probably a neighbouring star inside the 12 arcsec aperture, not WISE 0855. At 4 to 5 um (D5, D6) the values are 0.2 to 1.3 mJy, which is the scale expected from its published WISE 4.5 um brightness (about 14 mag, about 0.5 mJy; from memory, not checked here). A real detection would need a smaller aperture or a neighbour-subtracted measurement.
- **Barnard's Star and R Draconis** are refused: every frame has the aperture masked (saturation).
- **RW Aurigae**: only 4 of 102 bands measurable (the aperture is 22 to 65 percent usable in most frames), and its brightness varies, so points from different dates are not comparable.
- Failed archive downloads cost about 15 s each (3 retries, long timeout), which made a spectrum take 35 s even after the archive was known to be down; the spectrum now uses 1 retry and a 15 s timeout, and a single probe download first.
- Listing a target with 2,514 files wrote one database row at a time and took about 12 s; bulk reads and writes fixed it.
- The UI's Spectrum page only lists D3 observations, so the image behind a band from another detector says "not listed". The page should ask for all bands (`listObservations(objectId, {bandpass: undefined, limit: 200})`).

## Candidates and the brightness floor (2026-11-15)
- **Frame-to-frame repeatability is a few percent, not the 1 percent the formal errors suggest.** Epsilon Indi B, 44 real frames: frames at the SAME wavelength agree to 0 to 3 percent where the spectrum is flat, but three frames at 1.724 um (formal error about 1 percent) spread by 8 percent (24.8, 25.8, 27.0 mJy) where it is steep (slope about -140 mJy/um). With only the formal errors, 8 same-wavelength pairs gave brightness differences up to 14 sigma and the candidate wrongly read 'both motion and brightness'. Fix: wavelength matching tightened from one bandwidth to a quarter of one (a mismatch of 0.01 um is already 2 mJy in a steep region) and a 3 percent systematic floor per frame added in quadrature to brightness comparisons (`photometry.SYSTEMATIC_FRACTION`). The earlier flux change of -0.74 mJy is now -0.74 +- 1.39 mJy (0.5 sigma).
- **Real candidates** (demo bundle, stored frames only): Epsilon Indi B has one (positional change, 4.55 +- 0.49 arcsec/yr from 40 frames over 0.52 years, 9 sigma, validated; the catalogue gives 4.68; components differ by 2.2 and 1.7 sigma; brightness steady between 8 same-wavelength pairs; score 0.74). WISE 0855: none (the source at the expected position does not move: 1.07 +- 0.86 arcsec/yr, consistent with a neighbouring star). Barnard's Star and R Draconis: none (saturated, cannot be centroided). RW Aurigae: none (only 7 frames track and 1 same-wavelength pair exists, too few to judge brightness).
- On screen the shift is tiny: Epsilon Indi B moves about 2 arcseconds between the earliest and latest bundled dates, one or two pixels of the 300 pixel image. The guided exercise therefore says so and asks the user to zoom in.
