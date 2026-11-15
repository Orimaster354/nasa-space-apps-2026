# NASA Space Apps 2026: SPHEREx Project

A web application built for the NASA Space Apps Challenge 2026 using data from NASA's SPHEREx mission.

## Purpose

Make SPHEREx all-sky spectral survey data easier to explore, process and understand. *(Refine once the challenge scope is finalized.)*

## Planned Core Features

- Data ingestion and processing pipeline for SPHEREx data (`backend/processing`)
- API serving processed data (`backend/api`)
- Interactive web frontend for exploring results (`frontend`)
- Tests and documentation (`backend/tests`, `docs`)

*(Placeholder list; to be updated as the team decides on scope.)*

## Project Structure

```text
frontend/   Web UI
backend/    data/, processing/, api/, tests/
docs/       Project documentation
.github/    CI workflows
```

## Setup

*Placeholder: setup instructions will be added once the stack is chosen.*

1. Clone the repository
2. Backend setup: TBD
3. Frontend setup: TBD

## Team

- Member 1: Asish Nakka
- Member 2: Sonika Ravichandran

## How we checked our work

- [docs/SPECTRUM_VALIDATION.md](docs/SPECTRUM_VALIDATION.md): an independent re-measurement of the spectra, the error it found and fixed, repeatability, and what is not validated.
- [docs/CANDIDATE_VALIDATION.md](docs/CANDIDATE_VALIDATION.md): stress tests of the Epsilon Indi B motion candidate, including negative controls on ordinary stars.
- [docs/INVESTIGATION_EPSILON_INDI_B.md](docs/INVESTIGATION_EPSILON_INDI_B.md): one full investigation on real data, with what it does and does not establish.
- [docs/FRAME_COUNTS.md](docs/FRAME_COUNTS.md): where each frame count in the app comes from.
- [docs/BACKEND_CHECKLIST.md](docs/BACKEND_CHECKLIST.md): every planned item, its status and the evidence.

## Data credits

The app uses public SPHEREx data from NASA/IPAC IRSA and shows the required acknowledgments on its Library page. The exact wording, dataset DOIs, papers and the WiseView reference are in [docs/DATA_CREDITS.md](docs/DATA_CREDITS.md).
