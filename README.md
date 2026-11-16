# NASA Space Apps 2026: SPHEREx "Planet X" explorer

A public web tool for exploring **real SPHEREx observations** from NASA/IPAC's IRSA archive. Search an object, blink and compare its images across dates, read its measured infrared spectrum, check motion candidates, and ask **Navi**, an AI companion that answers only from the measurements shown on screen.

**Live site:** https://navilink-786494247195.us-central1.run.app (Google Cloud Run, one container serving the API and the frontend)

> Candidates are leads to check, **not discoveries**. Nothing in this app claims a confirmed finding.

## What it does

- **Search** by name or by RA/Dec. Names are resolved with SIMBAD (via CDS Sesame), including the target's proper motion, so a moving star is looked for where it is *now*, not where it was in 2000.
- **Observation viewer.** Real SPHEREx image cutouts in date order, with playback and five modes: blink, side by side, overlay, difference and a single view. Frames with poor quality flags are dropped or marked, and the difference view states its caveats.
- **Spectrum.** Up to 102 bands (0.75 to 5 micrometres). Each point is measured from the picture behind it, with an uncertainty. Where a spectrum cannot be drawn honestly (too few usable bands, a saturated star), the app says so and gives the reason instead of drawing one. An earlier-versus-later comparison shows which bands changed beyond their errors.
- **Candidates.** Motion candidates from a known proper motion, and brightness changes that must be corroborated by more than one frame. Each comes with the evidence, a heuristic score (never a probability) and its limits.
- **Navi, the AI companion.** A language model sees only text facts computed by the backend, and every number in its answer is checked against those facts. Words like "discovered", "confirmed" or "proved" are not allowed. It also gives a guided tour, and **Spot the shift**, a guided activity built from a real candidate.
- **Library.** 13 targets stored on the server (838 frames) that open instantly and keep working when the archive is down. A target found by a search can be **added automatically** once its data comes back complete (it gets the same frames, spectrum and candidate search as the shipped ones).
- **Archive status badge**, investigations with notes, and an **Achievements** tab with 18 badges.
- **Data credits** with IRSA's required acknowledgments.

## Honesty and validation

The principle is *scientific computation first, AI interpretation second*: NumPy, SciPy and Astropy compute every measurement; the model explains them and never calculates.

- **No science result is typed into the code.** See [docs/DATA_AUDIT.md](docs/DATA_AUDIT.md) for what is hard-coded (target constants re-checked against live SIMBAD, mission facts, thresholds) and how each was verified.
- [docs/SPECTRUM_VALIDATION.md](docs/SPECTRUM_VALIDATION.md): an independent re-measurement of the spectra. It found and fixed two real errors (aperture area over-counted by up to 1.2 percent; masked bright cores read about three times too low) and lists what is *not* validated, including the absolute flux scale.
- [docs/CANDIDATE_VALIDATION.md](docs/CANDIDATE_VALIDATION.md): stress tests of the Epsilon Indi B motion candidate (about 4.57 +/- 0.24 arcsec/yr against a catalogue 4.68), with negative controls on ordinary stars.
- [docs/INVESTIGATION_EPSILON_INDI_B.md](docs/INVESTIGATION_EPSILON_INDI_B.md): one full investigation on real data.
- [docs/FRAME_COUNTS.md](docs/FRAME_COUNTS.md): where every frame count comes from (for example, how Barnard's Star's 363 observations become the pictures shown).
- [docs/BACKEND_CHECKLIST.md](docs/BACKEND_CHECKLIST.md): every planned item, its status and the evidence.

**Known gaps**
- The absolute flux scale was not compared with catalogue photometry, and no aperture correction is applied.
- Calibration offsets between detectors could not be tested.
- Saved investigations live on the server's temporary disk and are lost on a redeploy.
- Streaming chat replies were deliberately not built. Auto-differencing and a catalogue overlay exist but are off by default.

## Project structure

```text
backend/     FastAPI app
  api/         routes and response models (the contract with the frontend)
  services/    objects, observations, spectra, candidates, library, promotion, proper-motion lookup
  processing/  FITS, WCS, photometry, comparison, astrometry, archive status
  ai/          Navi: grounded facts, wording guard, number checking
  store/       local registry and file store (optional cloud backup, off by default)
  scripts/     validation, demo-bundle building and probing tools
  tests/       about 414 tests (pytest)
frontend/    plain JavaScript web app (hash router, no build step)
docs/        architecture, contract, validation write-ups, checklists
Dockerfile   one image serving API + frontend
```

## Run it locally

Requirements: Python 3.11.

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then add a Gemini API key; .env is git-ignored
SPX_FRONTEND_DIR=../frontend uvicorn app.main:app --port 8000
```

Open http://localhost:8000. Without a key everything works except Navi's free-text answers. The frontend ships with sample data switched on for development (`useMock: true` in `frontend/js/config.js`); set it to `false` to use the real backend, as the Dockerfile does.

Tests: `cd backend && pytest -m "not realdata"` (tests marked `realdata` call the live archive).

## Configuration

All settings are `SPX_*` environment variables; see [backend/.env.example](backend/.env.example). The Gemini key lives only in a git-ignored `.env` and, in production, in Google Secret Manager. Never commit it. Optional switches: `SPX_AUTO_PRELOAD` (add successful searches to the Library), `SPX_ARCHIVE_PROBE`, `SPX_AUTO_DIFFERENCING`, `SPX_CATALOG_OVERLAY`, `SPX_BACKUP_BUCKET`.

## Deploying

The Dockerfile builds one container. The pre-built demo data (`backend/demo_bundle`: stored pictures, spectra and candidates) is kept out of git and copied into the build folder before `gcloud run deploy`. Because Cloud Run's disk is temporary, searched targets added at run time last only until the next redeploy; each is logged as `PRELOAD_ADDED name=...` so the good ones can be added to the next bundle. Build the bundle with `backend/scripts/build_demo_bundle.py` and finish it with `ship_finalize.py`. Note that `gcloud` skips files named in `.gitignore`, so the deploy folder needs a `.gcloudignore` that does not exclude `demo_bundle`.

## Data credits

The app uses public SPHEREx data from NASA/IPAC IRSA and shows the required acknowledgments on its Library page. The exact wording, dataset DOIs, papers and the WiseView reference are in [docs/DATA_CREDITS.md](docs/DATA_CREDITS.md). WiseView (a WISE/NEOWISE viewer) was a UX reference only; no SPHEREx processing is taken from it.

## Team

- **Asish Nakka**: backend, scientific pipeline, AI companion
- **Sonika Ravichandran**: frontend, visual design, Navi's look and personality
