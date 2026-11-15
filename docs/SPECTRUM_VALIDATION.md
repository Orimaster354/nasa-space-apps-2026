# Validation of the spectral calculations

Done 2026-11-15 with `backend/scripts/validate_spectrum.py`. It re-measures the stored spectra with separate code and tests the assumptions the spectrum makes. It reads only stored frames. It shares with the app only the archive's pixel data and flag definitions, not the app's photometry.

## What was found and fixed

**A real error: the aperture area was over-counted by up to 1.2 percent.** The app sums a circular aperture with fractional pixel overlap, sampled 7 x 7 per pixel. At the 12 arcsec aperture (1.95 native pixels) that sampling over-counts the area by +1.18 percent when the star sits at the centre of a pixel and by about -0.2 percent at other positions; sampling 20 x 20 gives +0.16 percent and 40 x 40 gives -0.03 percent. The independent measurement exposed it as a systematic difference:

| Target (bands re-measured) | Independent vs the stored spectrum, median | Independent vs the corrected code, median | 95th percentile after |
|---|---|---|---|
| Epsilon Indi B (49) | -1.20 % | +0.09 % | 0.35 % |
| RW Aurigae (20) | -1.11 % | +0.09 % | 0.22 % |
| WISE 0855-0714 (49) | -1.19 % | +0.13 % | 2.3 % (faint bands, see below) |

The sampling is now 32 x 32 (`processing/photometry._SUBSAMPLE`). Spectra stored before the fix carry the bias; `scripts/ship_finalize.py` rebuilds every stored spectrum before a bundle is shipped. Rebuilding Epsilon Indi B with the corrected code changed common bands by a median of -1.3 percent (the bias), and it now has 61 measured bands instead of 50 because more of its frames were reachable.

## The remaining accuracy of the method

The fractional-overlap sum is not exact for a source that is broad compared with the aperture, because the flux inside a partly covered pixel is assumed uniform. Measured on simulated pixel-integrated Gaussian stars at random positions inside the pixel (12 arcsec aperture):

| Star FWHM | Measured / true |
|---|---|
| 5.9 arcsec | -0.2 % |
| 7.2 arcsec | -0.9 % |
| 8.7 arcsec | -2.1 % |
| 11.6 arcsec | -4.5 % |

SPHEREx's blur is about 6 arcsec, so the method's own bias is expected to be a fraction of a percent to about 2 percent, inside the 3 percent repeatability floor. (The old synthetic test star was far broader, FWHM 17 arcsec, and reads 2.7 percent low; its tolerance was widened with this explanation.)

## Repeatability and the 3 percent floor

Frames at matched wavelength (within a quarter of a bandwidth) should give the same flux if the object does not change.

- **Epsilon Indi B, 37 pairs from 56 frames:** the robust scatter of the fractional difference is 4.2 percent. Divided by the formal statistical errors, the differences have a robust sigma of **2.64**, so the formal errors alone are about 2.6 times too small. A per-frame floor of **2.87 percent** makes them consistent, and with the app's 3 percent floor the robust sigma is **0.96**. But **4 of 37 pairs lie beyond 3 sigma where about 0.1 is expected**: the core of the distribution is right and the tails are heavier than Gaussian (an occasional frame is off by more than the floor).
- **WISE 0855-0714, 17 pairs:** the scatter is 18.6 percent because the source is faint; with the 3 percent floor the robust sigma is 1.11 and 1 of 17 pairs is beyond 3 sigma. The floor is not the limiting factor here, noise is.
- **RW Aurigae:** only one matched pair among the stored frames, so repeatability could not be tested.

## Continuity between neighbouring bands

The deviation of a band from the mean of its two neighbours, for Epsilon Indi B inside a detector: median 7.8 percent, 90th percentile 22.9 percent (21 bands). That is large, but this spectrum is steep (27 to 14 mJy over 0.056 micrometres in places) and each band comes from a different frame, date and detector position, so it measures how rough the spectrum is, not an error at a boundary. No band next to a detector boundary had both neighbours measured for Epsilon Indi B, so **a calibration offset between detectors could not be tested**; for WISE 0855 the one boundary band deviates by 12.7 percent and for RW Aurigae by 4.4 percent, which is within the roughness seen elsewhere.

## How much flux lies outside the aperture

Share of the flux inside 12 arcsec compared with 24 arcsec, on the best-measured Epsilon Indi B and RW Aurigae frames: **95 to 97 percent** across 1.6 to 2.4 micrometres. So at least 3 to 5 percent of the flux lies outside the 12 arcsec aperture (more outside 24 arcsec), and the spectrum, which applies no aperture correction, reads low in absolute terms by at least that much. This was already stated as "the flux scale is approximate"; the number is now known.

## What is NOT validated

- **The absolute flux scale** against independent photometry. Catalogues (2MASS, WISE, Gaia) could not be reached on 2026-11-15, so no literature magnitude was compared. The scale rests on the archive's calibration and our unit conversion (the pixel area comes from the WCS Jacobian, checked independently).
- **Colour corrections** (none applied) and any dependence of the blur on wavelength.
- **Calibration offsets between detectors** (see above).
- **Band numbers**, which are our estimate (17 log-spaced channels per detector); the wavelengths themselves are the archive's.
- **The wavelength of the faint WISE 0855 near-infrared points**, which probably belong to a neighbouring star (the source at the expected position does not move as WISE 0855 should).
- **Flagged pixels inside the aperture** are scaled up for, assuming the missing pixels look like the rest.
