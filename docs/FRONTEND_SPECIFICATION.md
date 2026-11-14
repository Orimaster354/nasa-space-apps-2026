# Frontend Specification

> Owner: frontend developer. Visual source of truth: `frontend/design-refs/investigate-mockup.png` (workspace),
> `landing-mockup.png` (cover), `navi-reference.png` (companion). Data boundary: `FRONTEND_BACKEND_CONTRACT.md` v0.1.0.
> Stack: plain HTML/CSS/ES modules, no build step, no dependencies.

## 1. Product feel

An immersive astronomical instrument, not an admin dashboard or a chatbot. Dark teal/charcoal sky, pixel-style
headings, calm spacing, a small observatory-on-the-hill scene and Navi hovering near the viewer. The image is the
visual priority. Everything illustrative is labelled **"Sample / illustrative — not real SPHEREx data"**, always visible.

## 2. Navigation

- **Top bar:** `SPHERE/X` · Mission · Investigate · Learn.
- **Investigation tools** (tabs under the top bar, one shared state): Investigate, Compare, Spectrum, Notes.
- **Shared state** (survives every tab switch and reload, mirrored into the URL for sharing): object, observation,
  comparison pair, candidate, spectral band, guided progress, notes, bookmarks, history, chat.
- Landing page `index.html` → "Begin exploring" opens the workspace; "Skip to observations" skips the intro.

## 3. Investigate (centrepiece)

Three columns on desktop (as the concept), stacked on narrow screens with the viewer first.

| Area | Behaviour |
|---|---|
| **Target picker** (header) | Search by name or RA/Dec; sample targets: Barnard's Star, WISE 0855, RW Aurigae, R Draconis, Epsilon Indi B. |
| **Candidates panel** (left) | List of candidates with id, score, short description. Selecting one highlights it in the viewer and fills the evidence block: *why it is listed* (evidence bullets), `scoreBasis`, warnings. Scores are labelled "ranking score, not a probability". |
| **Viewer** (centre) | Image, zoom (wheel / buttons / +−), pan (drag / arrows), reset, fullscreen, brightness and contrast (cosmetic only), candidate markers (toggle), RA/Dec readout from the API, observation date badge, loading / processing / unavailable / failed states. |
| **Object details** (right) | Three separated blocks: **Catalogue** (name, type, coordinates, proper motion, source), **Derived by this app** (measurement with value, unit, ± uncertainty, method, quality flags), **Observation** (date, release, detector, wavelength range, quality). |
| **Timeline** (bottom) | Chronological dots with dates. Play/pause, stop, step, speed (0.5×–4×). Active observation is the same state the viewer shows. Degraded or processing observations are marked. |
| **Guided prompt** | "Spot the shift." appears in Navi's bubble and as a compact progress card (see §8). |

Playback = blinking through the sequential observations. Frames are preloaded so blinking is smooth.
The frontend never aligns, subtracts or measures images.

## 4. Compare

Mode switch: **Blink**, **Side by side** (linked zoom/pan), **Overlay** (opacity slider), **Difference**.
Two observation pickers. Always shows the backend's **compatibility verdict and every check**; `notEvaluated` is shown
as "not evaluated", never as a pass. `not_comparable` disables the images and explains why. The difference image is shown with the
caveat that differences may come from noise, alignment or calibration as well as real change.

## 5. Spectrum

Large viewer (≈70%) showing the observation that produced the selected band; a band strip (count from
`expectedBandCount`, never hard-coded) with the muted cosmic gradient; gap bands drawn as gaps; collapsible
flux-vs-wavelength chart with error bars **only** where `fluxUncertainty` exists; selected-band readout; second band
for comparison. Units always shown; illustrative flux labelled.

## 6. Navi companion (all workspace pages)

Original sprite, unchanged. Floats bottom-right with a contextual speech bubble; **Chat** opens an in-place panel:
messages, suggested questions (change per page and selection), beginner/advanced switch, "thinking" state, error
state with retry. Answers render as: explanation, **Based on** reference chips (click to open), **Caveats** block,
and **Next steps** buttons (`suggestedActions`). Mock replies are labelled illustrative. No LLM is called from the browser.

## 7. Notes and history

Notes (add, delete), bookmarks (current observation/object), history of events, saved investigations
(save/load/delete through a service abstraction, backed by `localStorage` in the mock), and a "Summarise" action
that asks the companion.

## 8. Guided investigations

"Spot the shift" (Barnard's Star sample): three steps integrated with the workspace — (1) step through the dates,
(2) click where you think something moved (candidate markers hidden until revealed; 3 escalating hints),
(3) confirm with a blink comparison. Progress dots, feedback and a plain-language explanation that states candidates
are not discoveries. Progress is derived from real workspace events.

## 9. States

| State | UI |
|---|---|
| Loading | Spinner with text; skeleton panels. |
| Empty | Explanatory message using `coverageNote` where provided. |
| `processing` | "Image being prepared", polled per contract (`retryAfterSeconds`). |
| `unavailable` / `failed` | Message with reason; retry where `failed`. |
| Network / API error | `ApiError.message` shown; retry if `retryable`. |
| AI unavailable | In-panel notice; app keeps working. |

## 10. Responsive and accessibility

Breakpoints: ≥1000px three columns; below, single column (viewer, timeline, candidates, details). Keyboard operable
viewer and timeline, visible focus, ARIA for lists/tabs/status, skip link, `prefers-reduced-motion` honoured
(no autoplay motion, no float animations). Slate Blue `#405A6B` is never used for text.

## 11. Data access

Views call only `js/api/client.js`. All mock data lives in `frontend/mock/` and `frontend/js/api/mock/`.
Capabilities missing from the contract are in `docs/proposals/CONTRACT_PROPOSAL_0.2.0.md`
(candidates, guided investigations, field of view, proper motion, chat mode). Until agreed they are served by
`mock/proposed.json` via `js/api/mock/mockApi.js`; against the real client they report "unavailable".
