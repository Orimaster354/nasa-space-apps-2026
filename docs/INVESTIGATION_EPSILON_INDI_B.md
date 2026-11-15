# Investigation: did Epsilon Indi B change or move in SPHEREx data?

One complete investigation, run end to end with this project's own backend on real SPHEREx data. It shows what the tool can establish, and just as important, what it cannot.

- **Target:** Epsilon Indi B, a nearby brown dwarf (listed as a close pair that SPHEREx's 6 arcsecond pixels cannot separate, so every number below describes the pair together).
- **Data:** 50 SPHEREx observations in the D3 band (1.63 to 2.43 micrometres), 29 April 2025 to 4 November 2025, from NASA's public IRSA archive.
- **Reproduce:** `PYTHONPATH=. SPX_DATA_DIR=<copy of demo_bundle> python scripts/investigate_eps_indi_b.py` from `backend/` (works offline except the AI question). Every number below comes from that run.

## The question

A brown dwarf this close drifts across the sky by several arcseconds a year, and brown dwarfs can also vary in brightness. Using only SPHEREx, can we say whether it **changed in brightness** between two dates, and whether it **moved**?

## What was done, and what came out

### 1. Find the target
The name resolves from the project's curated table, with its catalogue proper motion: **4.68 arcseconds per year** (pmRA +3982.0, pmDec -2466.8 milliarcseconds per year, SIMBAD data fetched through the CDS Sesame service). The catalogue position is for the year 2000, so the image field is centred where the object should be during the survey. Real frames confirm this: a source sits at the centre of the field, 33 to 92 times brighter than the sky median.

### 2. Choose two dates that can be compared fairly
Each SPHEREx band covers a range of wavelengths, and an object lands at a different wavelength on each pointing. Brightness changes with wavelength even when the object does not change, so two pictures only compare cleanly when they sample the object at about the same wavelength. The tool measures the wavelength at the object's own pixel. Among the frames where brightness could be measured (44 of 50), the best pair at least 100 days apart:

| | Date | Wavelength at the object |
|---|---|---|
| Earlier | 29 April 2025 | 1.647 micrometres |
| Later | 28 September 2025 | 1.638 micrometres |

They are 153 days apart and differ by 0.009 micrometres, well inside the band's local width of 0.039 micrometres.

### 3. Measure brightness, with uncertainty
Aperture photometry, 12 arcsecond radius, local background ring, uncertainty from the archive's variance plane (scaled up where the sky scatter is larger than the archive predicts).

| Date | Brightness | Signal compared with its uncertainty |
|---|---|---|
| 29 April 2025 | **32.16 ± 0.27 mJy** | 117 times |
| 28 September 2025 | **31.42 ± 0.19 mJy** | 163 times |

### 4. Measure the change
Later minus earlier: **-0.74 ± 0.34 mJy**, which is **2.2 times its uncertainty**. The tool's own verdict: *within 3 sigma, so it is consistent with no change.*

**The safeguard works.** The tool refuses to report a change between frames at different wavelengths. Example: 28 September (1.638 micrometres) against 1 October (2.396 micrometres), 0.76 micrometres apart, was refused with the explanation that a brightness difference there would be a colour difference, not a change over time.

### 5. Compare the two frames
Verdict: **comparable with caveats.** Footprint, wavelength, data release and background checks passed. Stars in the two frames agree on position to **0.36 arcseconds** (median over matched field stars, under a tenth of a native pixel), so the alignment check passed. One check warned: the 28 September frame has degraded quality (many pixels flagged unusable). One was not evaluated: whether the flux calibration of the two frames is equivalent.

### 6. Check the motion against the catalogue
The target's position was tracked relative to the surrounding field stars in 40 of the 50 frames (the others lacked enough matched stars), then a straight line was fitted against time over 0.52 years.

| Direction | Measured | Catalogue predicts | Difference |
|---|---|---|---|
| West | -3.02 ± 0.43 arcsec/yr (so 3.02 toward the east) | -3.98 | 2.2 sigma |
| North | -3.40 ± 0.53 arcsec/yr (so 3.40 toward the south) | -2.47 | 1.7 sigma |
| **Total** | **4.55 arcsec/yr** | **4.68 arcsec/yr** | within 3 percent |

The target clearly moves relative to the field stars (about 7 sigma in each direction), toward the south-east, at about the speed the catalogue gives.

### 7. Ask the companion
Question: *"Did this brown dwarf get brighter or fainter between those two dates?"* Navi's answer (written by the AI from the results above, every number checked against them):

> The measurements show a change of -0.7434 with an uncertainty of 0.336 mJy. Because this change is only 2.2 times its uncertainty, it is consistent with no change. The two pictures were matched in wavelength, so colour does not explain a difference. You could try selecting another pair of observations to see if they show a different result.

Cited results: the flux-change measurement and the comparison. The AI is told to keep evidence and hypotheses apart, and it did not claim a finding.

## What is established

- Real SPHEREx observations of the target were retrieved, and their metadata (band, date, quality flags, wavelength at the object) was interpreted.
- The target is detected strongly in every usable frame and its brightness is **32 mJy, measured to about 1 percent** in each of the two chosen frames (statistical uncertainty only).
- Between those two dates (153 days, matched wavelength) there is **no significant change in brightness**: -0.74 ± 0.34 mJy.
- The target **moves** relative to the field stars in the direction and at about the speed of its catalogue proper motion.
- The tool refuses comparisons that would be misleading (different wavelengths), and says so in plain words.

## What is NOT established

- **That the brightness is constant.** One pair of dates, with a limit of about 1 percent (2.2 sigma on -0.74 mJy). A longer or different pattern of variability is not ruled out.
- **That the flux calibration of the two frames is equivalent.** This check was not evaluated.
- **Absolute brightness.** There is no aperture correction (light outside the 12 arcsecond circle is not added back) and no colour correction. The values are only comparable with each other, in the same band, with the same aperture.
- **The motion as a validated measurement.** The two components differ from the catalogue by 1.7 and 2.2 sigma, and the fit assumes the frames' errors are independent over a baseline of only about half a year. This is a good consistency check, not an independent proper-motion measurement. The component differences hint that our uncertainties are somewhat optimistic.
- **The catalogue epoch.** The catalogue positions are assumed to be at epoch J2000. This was confirmed on real data for Barnard's Star and cross-checked against the challenge text for WISE 0855, but not separately for this object (the source at the field centre is consistent with it).
- **Anything about the two components of the binary.** SPHEREx's pixels cannot separate them.
- **Anything about the brown dwarf's nature.** The tool measures brightness and position, nothing more.

## Limits of the data we hit along the way
- 6 of the 50 frames could not be measured (aperture masked, or outside the stored image).
- The listing shows only the first 50 of the 99 D3 observations; later epochs (into 2026) exist but were not used.
- Two of the five challenge targets, Barnard's Star and R Draconis, saturate SPHEREx, so brightness cannot be measured on them at all (the tool refuses, rather than guess).
