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

## Updates 2026-11-14 (second interview)
| Topic | Decision |
|---|---|
| Judge access | Public web link |
| Demo data | Ship pre-rendered images for the five challenge targets; other targets fetch live |
| LLM | Google Gemini (AI Studio key) first; check real free limits in the console before relying on it |
| AI quota protection | Per-visitor message limit, then automated summaries |
| Still open | Hosting platform, Gemini key and model, submission freeze clock time |

## Update 2026-11-14: AI companion design rule
Scientific computation first, AI interpretation second. No vision or multimodal model judges SPHEREx images. Deterministic tools (NumPy, SciPy, Astropy, Photutils) do alignment, difference imaging, source detection, brightness measurement, variability significance, flag/variance quality filtering and spectral analysis. They return structured, cited results. The LLM only interprets, explains, contextualizes and guides. It never invents measurements, claims unsupported discoveries, or presents hypotheses as findings. ML models are added only for a specific task where they clearly beat deterministic methods. Full wording: plan section 9.
