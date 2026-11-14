# SPHERE X frontend

Plain HTML, CSS and ES modules. No build step, no dependencies.

## Run

```bash
cd frontend
python3 -m http.server 8000      # ES modules need http://, not file://
# open http://localhost:8000/app.html   (style guide: /styleguide.html)
```

Open `/index.html` for the landing page, or `/app.html` for the workspace.

## Check

```bash
node frontend/tools/check-fixtures.mjs   # mock fixtures vs contract v0.1.0 + labelling rules
node frontend/tools/smoke.mjs            # module linking, mock service layer, store, real-client error mapping
```

UI behaviour still needs the manual pass in `docs/FRONTEND_CHECKLIST.md`.

## Layout

| Path | Purpose |
|---|---|
| `css/tokens.css` | Approved palette, page gradient (same on every page), type, spacing |
| `css/base.css`, `css/components.css` | Reset, focus, shared components, pixel landscape |
| `css/navi.css` | Navi, her whispers and the assistant (shared by landing and workspace) |
| `js/core/` | `store.js` (shared state), `router.js`, `storage.js`, `dom.js`, `playback.js`, `frames.js` (polling + cache), `placement.js` (collision-aware placement), `events.js` (app events Navi reacts to) |
| `js/components/` | `viewer`, `timeline`, `comparison` (blink/side/overlay/difference inside Investigate), `bandSelector`, `navi` (body: zones, flight, whispers, assistant), `naviBrain` (personality, reactions, tour), `scene` (pixel landscape), `guided`, `objectDetails` |
| `js/views/` | `investigate` (incl. comparison modes), `spectrum`, `notes`, `learn` (each `mount(el) -> unmount`) |
| `js/api/` | `client.js` (only data entry point), `types.js` (contract JSDoc), `mock/` |
| `js/api/mock/` | The only place mock logic lives: `mockApi`, `imagery`, `chat`, `investigations`, `fixtures` |
| `mock/` | Illustrative JSON: objects, observations, spectra, `bands.json` (explicit band metadata, uneven spacing), and `proposed.json` (not-yet-contract content) |
| `assets/navi/navi.png` | Navi, cropped unmodified from `design-refs/navi-reference.png`, background keyed transparent |
| `design-refs/` | Approved visual references (source of truth) |

## Rules

- Views get data only through `js/api/client.js`. To use the Python backend set `CONFIG.useMock = false` in `js/config.js`.
- Mock data must stay labelled "illustrative". Candidates are never described as discoveries.
- Contract changes go through `docs/proposals/` and the process in `docs/DEVELOPMENT_OWNERSHIP.md`.

## Try Navi

Click Navi to open the assistant. Type `simulate error` to see the AI-unavailable state. Comments (On / Fewer / Off) and Replay tour are in the assistant. Click a whisper or press Esc to dismiss it. Spot-the-shift: start it in the Investigate sidebar.

## Backend swap

`CONFIG.useMock = false`. Contract operations #1-#18 are wired in `js/api/client.js`. Operations marked PROPOSED
(candidates, guided investigation, catalogue extras, chat suggestions) report "unavailable" until the contract
proposal in `docs/proposals/` is agreed.
