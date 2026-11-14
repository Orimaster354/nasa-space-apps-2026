# Development Ownership

> Goal: two developers work independently on separate branches without building incompatible things.
> Contract version **0.2.0 (draft)**. See [FRONTEND_BACKEND_CONTRACT.md](FRONTEND_BACKEND_CONTRACT.md).

Each developer designs and structures their own side. The **contract and canonical data models are the only boundary** between the two implementations.

## Roles

| Role | Developer | Branch |
|---|---|---|
| Backend / AI | Developer 1 | `back-end` |
| Frontend | Developer 2 | `front-end` (exists) |

`main` holds the shared agreed state (these docs and anything both sides have merged).

## Ownership

### Frontend developer owns
- All frontend components and implementation.
- Styling and visual identity.
- Client-side interactions.
- Mock data and mock services.
- Frontend routing and state management.
- Frontend-specific architecture and documentation.

### Backend developer owns
- NASA/IRSA integration and data retrieval.
- FITS parsing and scientific processing.
- Backend APIs.
- AI orchestration, tools, and context retrieval.
- Backend-specific architecture and documentation.
- Backend tests and cached data (`backend/data/`, git-ignored).

### Shared responsibilities
- API contract ([FRONTEND_BACKEND_CONTRACT.md](FRONTEND_BACKEND_CONTRACT.md)).
- Canonical data models ([DATA_MODELS.md](DATA_MODELS.md)).
- Integration testing.
- Decisions that affect both systems (the open decisions in [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md#open-decisions)).

### Shared files

| File / area | Rule |
|---|---|
| `docs/FRONTEND_BACKEND_CONTRACT.md`, `docs/DATA_MODELS.md` | Change only via the contract process below. These two always change together. |
| `docs/ARCHITECTURE.md`, `docs/SCIENTIFIC_DATA_PIPELINE.md`, `docs/INTEGRATION_PLAN.md`, this file | Either may propose edits; the other approves before they land on `main`. |
| Shared example JSON / types, if a shared location is agreed (OD-3) | Part of the contract. Same process. |
| `README.md`, `.gitignore`, `.github/workflows/` | Announce before editing; keep changes small and additive. |
| Configuration that crosses the boundary (e.g. API base URL, allowed origins) | Agree first, document in README. |

## Rules

1. **Communicate before touching the other person's implementation.** Request the change instead of making it.
2. **Contract before code.** Shared interfaces are agreed first, then implemented on both sides. Never add or change a shared field on only one side.
3. **No duplicated business logic.** Scientific logic lives in the backend. Do not recompute backend-derived results on the other side; request a contract field instead.
4. **Mock data stays out of the backend.** Mocks are a frontend concern and must follow the contract exactly.
5. **No backend-specific logic in the frontend.** That includes IRSA access, FITS handling, scientific calculations, LLM calls, and API keys.
6. **No dependence on internals.** The frontend depends only on the contract, never on how backend modules are implemented. The backend does not depend on how the frontend is built.
7. **Contract changes are communicated and mirrored in both branches** (process below).
8. **Freedom inside the boundary.** Each developer may structure their own implementation however they like, provided they honor the contract.
9. **Unknown is explicit.** Undecided things are recorded as open decisions, not silently assumed.
10. **Keep it small.** This is a 48-hour project; avoid abstractions it does not need.

## Changing the contract

1. State what changes, why, and which operations/models it affects (issue, PR, or message).
2. The other developer agrees or counters.
3. Update **both** `FRONTEND_BACKEND_CONTRACT.md` and `DATA_MODELS.md` (and shared examples/types if they exist) together on `main`. Bump the contract version and add a changelog row.
4. Both developers bring `main` into their branches promptly.
5. Each side updates its own implementation and mocks/tests.

**Versioning.** `MAJOR.MINOR.PATCH`. Adding an **optional** field is non-breaking. Removing, renaming, or making a field required is breaking. Before connecting the two sides (Phase 5 of the integration plan), breaking changes still follow steps 1-4. After that, they also need a same-day update on both sides.

## Git workflow

- Work on your own branch; do not push to the other developer's branch.
- Merge to `main` only what the other person can safely depend on (docs, contract, finished work).
- Bring `main` into your branch regularly and after any contract change.
- Commit messages follow `type: summary` (`docs:`, `feat:`, `fix:`, `chore:`).
- Never commit data files, credentials, or `.env` files.

## When to message the other developer

- You want to change anything in a shared file.
- The contract cannot express something you need.
- You are renaming or removing an endpoint or field.
- You change configuration that crosses the boundary.
- You find a problem in the other side's output: describe request, expected, and actual. Do not patch it yourself.

## Quick examples

| Situation | Whose |
|---|---|
| Whether two observations are comparable | Backend decides; verdict and checks are in the contract |
| How a comparison verdict is presented | Frontend |
| Computing flux values in a spectrum | Backend |
| How a spectrum is drawn | Frontend |
| Which tools the AI companion can call, and what it says | Backend |
| How the companion looks and behaves in the UI | Frontend |
| Caveat text accompanying an AI answer | Backend supplies it in `caveats`; frontend decides presentation |
| Adding a field to `Measurement` | Both (contract change) |
