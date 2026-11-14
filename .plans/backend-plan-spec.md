# Backend Plan: Interview Spec (2026-11-14)

Decisions captured while preparing `docs/BACKEND_IMPLEMENTATION_PLAN.md` and `docs/BACKEND_CHECKLIST.md`.

| Topic | Decision |
|---|---|
| Output files | Two `docs/` files plus this spec |
| Demo hosting | Cloud host with persistent disk; curated pre-cached targets; arbitrary targets fetch live with a visible "fetching" state and a timeout (judges use the site themselves) |
| LLM access | No API key yet; free tier required; **chain of two free providers**, then a deterministic fallback. Providers/models are open (D-1) |
| Framework | FastAPI; Python 3.11; `pyproject.toml` + pip in a venv |
| Blink frame | Recommendation (not explicitly answered): same-band-across-epochs is primary, cross-band is a labeled spectral comparison (`comparisonKind`); finalize after the coverage probe (P0-06) |
| Candidates | Catalog-seeded, evidence-backed. Never a probability, never "confirmed". Automated differencing is P2 |
| Contract gap | Proposed v0.2 addendum inside the plan; contract not edited until the frontend developer approves |
| Investigations | Anonymous per-browser client ID (needs a small contract addition) |
| Demo target | Data-driven: probe all five targets, pick the best |
| Guided investigations | Deterministic templates built from measured evidence; LLM only phrases text |
| AI grounding | Structured output with cited result IDs plus numeric check; fallback to a templated summary on failure |
| WISEView | Reference only; no code copied (README says public domain, GitHub shows no license file) |
| Freeze time | Developer sets the clock time (D-9) |

## Not verified in this session
- The official challenge page body and resource list (page found, text not retrievable).
- Source code of WISEView (only README, metadata, and directory listing read).
- TAP column list beyond those in the IRSA tutorial; flag bit meanings; observation time/ID header keywords.
- Free-tier LLM provider limits.
