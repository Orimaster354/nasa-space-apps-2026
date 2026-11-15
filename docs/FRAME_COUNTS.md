# Where the frame counts come from

The Library and the timeline show several different numbers for one target. They count different things. Measured on the deployed bundle (revision 11, 2026-11-15) by `backend/scripts/frame_counts.py`.

## Barnard's Star, step by step

| Step | Count | What it is |
|---|---|---|
| Observations the archive lists at this position | **363** | Every SPHEREx spectral image whose footprint contains the position, **all six detectors** (D1 60, D2 62, D3 60, D4 60, D5 62, D6 59), from the DR1 and QR3 collections. One pointing produces six pictures, one per detector, at six different wavelength ranges. |
| In the default band, D3 (the timeline) | **60** | The app's timeline and comparisons use one band at a time; D3 (1.63 to 2.43 micrometres) is the default. |
| Stored on the server as a ready picture | **58** | The D3 pictures that were downloaded and rendered ahead of time. The 2 D3 observations that are not stored are both **poor quality** (more than a quarter of their pixels flagged unusable) and were dropped on purpose. |

So "58 of 363" compares two different things. The like-for-like pair is **58 of 60** (the same band). The Library now shows that pair and states the 363 separately.

## All five stored targets

| Target | Listed (all detectors) | Listed in D3 | Stored D3 | Stored in other detectors | Why the rest of D3 is not stored |
|---|---|---|---|---|---|
| Barnard's Star | 363 | 60 | 58 | 0 | 2 poor quality |
| WISE 0855-0714 | 268 | 45 | 45 | 33 (D1 1, D2 10, D5 11, D6 11) | none left out |
| RW Aurigae | 336 | 56 | 54 | 16 (D1 11, D2 5) | 2 poor quality |
| R Draconis | 2514 | 576 | 61 | 0 | 515 are later dates beyond the first stored batch (the target was observed far more often); 2 poor quality |
| Epsilon Indi B | 467 | 99 | 59 | 36 (D1 5, D2 9, D4 10, D5 12) | 36 later dates not stored; 4 poor quality |

## The rules that produced them

1. **Which frames are downloaded ahead of time.** The first frames in date order in the default band, about 50, plus a 25 percent spare so that dropping poor frames still leaves about 50 (`bundle.warm_target`, `skip_poor`). That is why R Draconis (576 listed in D3) has only its earliest 61 stored: the stored dates are 2025-04-24 to 2025-06-12 of a range reaching 2026-06-12. Dates beyond the stored batch need the live archive.
2. **Poor frames are dropped.** A frame is "poor" when more than a quarter of its pixels carry a critical quality flag (`processing/quality.quality_summary`: good under 5 percent, degraded under 25 percent, poor above). `bundle.prune_poor` deletes such a frame's picture and cutout. The frame stays in the observation list and loads live if opened.
3. **Pictures in other detectors** exist only where a spectrum was built: building a spectrum needs frames from several detectors, and each band's picture is stored with it (`bundle.warm_spectrum`). Barnard's Star and R Draconis have none because they saturate and no spectrum can be made.

## Known imperfection

The first shipped bundle still contains a few poor-quality frames that rule 2 should have removed (17 across the five targets and all detectors; in the default band, Barnard's Star 5 and WISE 0855 1): a snapshot was taken while the build was re-fetching them, before its clean-up step. They carry their own quality label in the app. `backend/scripts/ship_finalize.py` now removes them, so the next shipped bundle will not.
