# Frontend Specification

> Owner: frontend developer. Visual source of truth: `frontend/design-refs/investigate-mockup.png` (workspace),
> `landing-mockup.png` (cover), `navi-reference.png` (companion). Data boundary: `FRONTEND_BACKEND_CONTRACT.md` v0.1.0.
> Stack: plain HTML/CSS/ES modules, no build step, no dependencies.

## 1. Product feel

An immersive astronomical instrument, not an admin dashboard or a chatbot. Dark teal/charcoal sky, pixel-style
headings, calm spacing, a pixel-art observatory landscape and Navi resting in unused space near the work. The image is the
visual priority. Everything illustrative is labelled **"Sample / illustrative — not real SPHEREx data"**, always visible.

## 2. Navigation

- **Top bar:** `SPHERE/X` · Mission · Investigate · Learn.
- **Investigation tools** (tabs under the top bar, one shared state): Investigate (with all comparison modes), Spectrum, Notes.
  Old `#/compare` links redirect to Investigate in Blink mode.
- **Shared state** (survives every tab switch and reload, mirrored into the URL for sharing): object, observation,
  comparison pair, candidate, spectral band, guided progress, notes, bookmarks, history, chat.
- Landing page `index.html` → "Begin exploring" opens the workspace; "Skip to observations" skips the intro.

## 3. Investigate (centrepiece)

**Image-first layout.** No permanent side panels: the viewer spans the workspace and secondary information lives
in drawers that push the viewer aside only while open (wide screens; state persisted in `store.panels`) or slide
over the page (below 1000px; Esc / outside click closes). Default on wide screens: Candidates open, Details closed
(viewer ≈75% of the width at 1366px; ≈96% with both closed). Title row: page title, sample-data badge, tool tabs.

| Area | Behaviour |
|---|---|
| **Context bar** (top) | Candidates drawer toggle (with count), sample target picker, name / RA-Dec search, one-line object summary (name · RA · Dec · proper motion), Details drawer toggle. Sample targets: Barnard's Star, WISE 0855, RW Aurigae, R Draconis, Epsilon Indi B. |
| **Viewer toolbar** | Mode bar (View · Blink · Side by side · Overlay · Difference), compatibility badge while comparing (opens the checks), Fullscreen (whole viewer frame, both panes when comparing), Focus (distraction-free: only toolbar, viewer, timeline; Esc exits). |
| **Viewer** (centre, ≈ window height − 290px) | Image, zoom (wheel / buttons / +−), pan (drag / arrows), reset, field of view (Fit · 1:1 · 2:1), brightness and contrast (cosmetic), candidate markers (toggle), RA/Dec readout from the API, date badge, loading / processing / unavailable / failed states. Zoom/pan is remembered per object (`store.viewState`) when leaving for Spectrum or Notes. |
| **Candidates drawer** (left) | Candidates with id, score, description; selection highlights the marker and shows *why it is listed*, `scoreBasis`, warnings ("ranking score, not a probability"); Spot-the-shift card below. |
| **Details drawer** (right) | Compatibility checks while comparing, then **Catalogue**, **Derived by this app** (value ± uncertainty, method, flags) and **Observation** blocks. Independent of Navi's chat: opening or closing one never opens, closes or moves the other. |
| **Timeline** (bottom) | Chronological dots with dates. Play/pause, stop, step, speed (0.5×–4×). Replaced by A ⇄ B date pickers while comparing. |

Playback = blinking through the sequential observations. Frames are preloaded so blinking is smooth.
The frontend never aligns, subtracts or measures images.

## 4. Comparison modes (inside Investigate)

Comparison is not a separate page. A compact mode bar above the viewer switches **View | Blink | Side by side |
Overlay | Difference** (arrow keys move between modes) without navigating or reloading. `store.compareMode` holds the
mode (`"view"` = normal playback). Switching keeps the object, candidate, selected dates and zoom/pan (zoom is copied
into and back out of side-by-side/overlay). In comparison modes the timeline row is replaced by **A ⇄ B date pickers**
(default: the current date and the next one) plus the mode's tools (blink play/flip/speed, overlay fade slider), and the
backend's **compatibility verdict and every check** appear at the top of the right column; `notEvaluated` is shown as
"not evaluated", never as a pass. `not_comparable` removes the images (not just dims them) and explains why. Blink and
difference reuse the main viewer; side-by-side and overlay use a layer of the same size. **Side by side** collapses both drawers (restored on exit), gives each pane half the width with its own full controls, has a draggable / keyboard divider (20–80%) and a *Views linked* toggle (synchronised or independent zoom/pan); Fullscreen covers both panes. The difference image is
labelled "illustrative difference image" with mock data or "calculated by the backend" with real data, plus the caveat
that differences may come from noise, alignment or calibration. Implementation: `js/components/comparison.js`.

## 5. Spectrum

Large viewer showing the observation that produced the selected band, then the **band selector**
(`js/components/bandSelector.js`): every band is drawn at its real wavelength position and width from the API's
`wavelengthMinUm`/`wavelengthMaxUm` (spacing is never assumed uniform; mock layout in `mock/bands.json`), with a µm
axis, hatched no-data bands, a selected-band outline with caret, a dashed outline for a second band, hover tooltip,
previous/next buttons, and a live readout (band n of N, centre and range in µm, data / no data). One `role="slider"`
carries the keyboard model (←/→, PageUp/PageDown ±10, Home/End). Colours are a display mapping of wavelength and the UI
says so. The flux-vs-wavelength chart is collapsible (closed by default so the image stays primary), draws error bars
**only** where `fluxUncertainty` exists, and its marker follows the selection. Units always shown; illustrative flux labelled.

## 6. Navi companion (every page, including the landing page)

**Body** (`js/components/navi.js`). Original sprite, never altered, shown at one size everywhere: 60 CSS px tall
on desktop, 52 px below 600 px wide, aspect ratio preserved. She is anchored to the viewport and flown by a steering
model in a `requestAnimationFrame` loop (position + velocity, capped speed and acceleration, easing on arrival), with a
soft layered-sine hover and a slight lean into turns; changing destination mid-flight bends her path, never jumps.
Destinations come from the free space actually on screen: a 24 px grid of candidate spots, kept 18 px from every
edge and away from corners, rejecting any spot where she would cover imagery/art, controls, inputs, navigation or
text, and any path that would cross imagery or controls. She prefers comfortable hops (120-480 px) to places she has
not just visited, rests 2-5.5 s, then explores again. Scroll, resize, tool switches and content appearing under her
trigger a re-check (with timer fallbacks if the browser does not report them); if her spot is no longer free she flies
to the nearest free one; if nothing is free she takes the least-crowded spot (never imagery, never a corner). While
the user inspects an image she retreats away from it and stays quiet for 20 s. Clicking **Begin exploring** makes her
lead the way and the workspace starts her where she left. Reduced motion: no flight, hover, lean or particles; she is
placed directly in free space.

**Behaviour states** (`js/components/naviStates.js`, separate from the flight code). One state at a time, priority
TOUCHED > MISC > IDLE_DIALOGUE > MOVING > IDLE; a lower-priority request is refused while a higher state is active.
IDLE: hover in place; after 10–15 s → MOVING; on entering IDLE a 25% chance of IDLE_DIALOGUE (cooldown 45 s since her
last remark, 150 s with Comments: Fewer; never while the user is acting, studying an image, in Focus mode or with
Comments: Off; only where the bubble fits beside her, she never moves just to chat; recent lines are not repeated).
MOVING: the existing flight to a safe destination; arrival → IDLE. TOUCHED: clicking her pauses autonomy, drops any
bubble, opens the assistant; nothing can speak over it; closing → IDLE. IDLE_DIALOGUE: one short spontaneous line while
hovering; bubble closes → IDLE. MISC: tutorials, explanations, reactions and warnings (contextual `say()`, tour steps,
`goNear`); overrides idle chatter and wandering; finishes → IDLE. The state is exposed as `data-state` on `.navi`.
Navi is created once per page and survives tool switches (no duplicated timers).

**Voice** (whispers). A soft, translucent whisper rather than a chat box: organic rounded shape, faint glacier/aurora
tint over a light dark wash, backdrop blur, barely-there glow, no border, two trailing motes toward Navi instead of a
tail. Before a contextual line she settles where the bubble fits, hovers in place while it shows (the bubble follows
her resting point, so it never shakes), then returns to IDLE. Bubble placement is tiered: clear of everything; else
clear of imagery and controls; else least overlap. If content appears under her mid-sentence she moves and repeats
the line. Text: IBM Plex Sans (the app's clean body face; no extra font download), 16 px (15 px on phones), line height
1.5, wraps within 300 px, never shrunk; whole-pixel positioning and opacity-only fades keep it crisp. Click or Esc
dismisses. Bubble ranks: guided > tour > reactions > ambient > idle remarks, so only one bubble ever shows.

**Personality** (`js/components/naviBrain.js`, all lines predefined, no LLM). Curious, playful, a little sarcastic,
occasionally dramatic, encouraging, scientifically careful: jokes are about situations, never people; questions are
never mocked; candidates are "possible", differences "could be noise", gaps are not invented. Reacts only to things
the user just did: changing target or date, play / pause / stop, viewer modes, selecting a candidate, selecting a
band or a gap band, saving, notes, missing/processing images, not-comparable pairs, guided guesses, the same button
clicked three times, and a long idle spell. **Comments: On / Fewer / Off** in the assistant (`store.naviHints`);
Off mutes all unsolicited lines, questions still work.

**Tour.** Starts on arrival from Begin exploring (or a first visit): welcome → observation window → timeline →
candidates → viewer modes; Blink is explained when first used and the Spectral Explorer on first visit. Each step is
a whisper with *Next* and *Skip tour*; doing the thing (e.g. pressing play) also advances it; it pauses while the user
studies an image. Progress in `store.naviTour`; *Replay tour* in the assistant.

**Assistant.** Clicking Navi opens a floating chat window (default 400 × 450, conversation text 16px) with its own
open state (`store.naviOpen`), fully independent of the Details drawer (`store.panels.right`). Drag it by its header
(or the grip with arrow keys), resize it from the corner (300 × 280 up to 720px × screen height); it always stays
inside the viewport, and its position and size are remembered (`spherex.navi.chat`) across mode switches, tools and
reloads. By default it opens beside Navi or in the corner that hides the least imagery. Minimise (–) collapses it to
its title bar; ✕ or Escape inside it closes it (Escape elsewhere on the page does not). Long conversations scroll
inside. Below 600px it is a bottom sheet.
Messages, suggested questions per page/mode, beginner/advanced, thinking and error/retry states; answers show
explanation, **Based on** references, **Caveats**, **Next steps**. Mock replies are labelled illustrative.

## 6a. Landing and Mission

Landing keeps the approved cover with its sketch details (calm, but maximalist): orbit logo, "!" marks by the
headline, an orbit diagram with two slowly circling worlds, orange / ivory / blue sparkles that breathe out of phase,
a dusty Milky Way band, a shooting star, the engine flame, pixel cumulus clouds on the horizon
(`assets/landing/cloud-*.svg`, generated as pixel art), an ivory "Begin exploring" button, and a warm-paper sketch
strip along the bottom (telescope, ridgelines, an asteroid's dashed path, a wireframe globe, "▼ The Mission ▼").
All of it is decorative (aria-hidden), slow (5 s and up), frozen for reduced motion, and cropped rather than shrunk on
phones. Navi rests beside (never on) the spacecraft, planet, Earth and sketch strip. The Mission section uses the same dark
page gradient as every page: a short intro, three small points with pixel icons, a diagram explaining blink comparison,
the four steps, the honesty note, a call to action, and the shared pixel landscape.

## 6b. Pixel landscape

`js/components/scene.js` draws one landscape for every page: seamless ridges on a 4 px grid at 1:1 CSS pixels
(never stretched), Slate Blue far peaks with ivory snow, darker mid ridge and ground, a lit observatory with a
telescope in the dome slit, two tripod telescopes, faint aurora glow. Approved palette only; decorative and below content.

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
