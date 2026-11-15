# Validation of the Epsilon Indi B motion candidate (SX-6655)

Done 2026-11-15 with `backend/scripts/validate_candidate.py`, on the 59 stored D3 frames (50 of them tracked, spanning 1.01 years, 2025-04-29 to 2026-05-04). It uses only stored frames.

## What the candidate is, and is not

Epsilon Indi B is a nearby brown dwarf with a **known** large proper motion (4.68 arcsec/yr, from SIMBAD). The app is seeded from that catalogue value, finds the source at the expected position in each frame, measures its position relative to the surrounding stars, and fits a straight line. So the candidate **recovers an already known motion**; it is a test that the pipeline measures real motion correctly, not a new discovery, and the app never calls it one.

## Results

| Check | Result |
|---|---|
| **Headline motion** | west -3.78 +- 0.23, north -2.57 +- 0.26, **total 4.57 +- 0.24 arcsec/yr (18.9 sigma)**; rms of the positions about the line 0.65 arcsec; 25 reference stars per frame |
| **Against the catalogue** (west -3.98, north -2.47, total 4.68) | differences +0.9 and -0.4 formal sigma; total -0.11 arcsec/yr (-0.5 sigma). With the first 40 frames over 0.52 years the components differed by 2.2 and 1.7 sigma; with the longer baseline that tension is gone. |
| **Is the formal uncertainty honest?** Frame bootstrap, 500 resamples | total 4.56 +- 0.22 (formal 0.24); west -3.75 +- 0.23 (formal 0.23); north -2.58 +- 0.24 (formal 0.26). The formal errors match the empirical ones. |
| **Leave-one-out** | total stays between 4.47 and 4.63; no single frame moves it by more than 0.10. |
| **Odd versus even frames** (same time span) | odd: west -3.84 +- 0.35, north -2.32 +- 0.28; even: west -3.72 +- 0.31, north -2.82 +- 0.46. Consistent. |
| **First versus second half in time** | first half (25 frames, 0.42 yr): west -2.26 +- 0.82, north -2.60 +- 1.13; second half (25, 0.59 yr): west -4.47 +- 0.44, north -1.69 +- 0.42. The west rates differ by about 2.4 sigma; the first half is short and noisy. Worth knowing, not a failure. |
| **Negative controls**: the same tracker on 14 ordinary field stars | apparent motion median **0.35 arcsec/yr**, largest 0.99; **none above 3 sigma**. The target's 4.57 is 4.6 times the largest control. The scatter of the controls' rates across stars is 0.32 (west) and 0.36 (north) arcsec/yr, about 1.3 to 1.5 times the formal error of 0.24, so a realistic per-source uncertainty is closer to 0.3 arcsec/yr, which still leaves the motion at about 14 sigma. |
| **Could the wavelength fake a drift?** | The wavelength at the object ranges 1.638 to 2.376 micrometres and correlates with time at +0.30 (mildly). Using only the 12 frames within 0.08 micrometres of 1.91 micrometres: west -3.19 +- 0.56, north -1.95 +- 0.49, total 3.74 +- 0.54, consistent with the full fit within about 1.5 sigma and in the same direction. |

## What this supports

- The positional motion is real and measured, in the direction and at about the speed of the catalogue value, with uncertainties checked three independent ways and a false-motion rate of 0 in 14 on ordinary stars.
- The unresolved pair (Epsilon Indi Ba and Bb) is tracked as one source, since SPHEREx pixels (6.15 arcsec) cannot separate the two.

## What it does not show

- **Not a discovery.** The motion was already known; the target and its expected motion were supplied by the catalogue.
- **Brightness variability** is not claimed: matched-wavelength pairs agree within their uncertainties (`INVESTIGATION_EPSILON_INDI_B.md`).
- **The stored candidate in the live bundle** was built from the first 40 frames (4.55 +- 0.49 arcsec/yr). With the full stored set it is 4.57 +- 0.24. `scripts/ship_finalize.py` recomputes it before a bundle is shipped.
- **Only one target** has a validated candidate. WISE 0855 does not (the source found at its expected position does not move as WISE 0855 should, probably a neighbouring star); Barnard's Star and R Draconis saturate.
- **Systematics not tested:** a correlated pointing error shared by all reference stars would not show up in the controls (they share the same reference stars); the field is small (about 10 arcminutes) and the pointing model is the archive's own.
