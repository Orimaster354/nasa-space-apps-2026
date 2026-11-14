# Frontend Checklist

Done means: works in the browser against mock data, keyboard reachable, states handled, no console errors.

**Status legend**
- `[x]` implemented and **Node-verified** (`node frontend/tools/smoke.mjs` / `check-fixtures.mjs`).
- `[~]` implemented, **not yet run in a browser** (no browser was available in the build environment). Needs the manual pass below.
- `[ ]` not done.

## P0 Essential
- [~] **Shell**: top nav, tool tabs, skip link, view mount/unmount. DoD: every route renders and keeps shared state.
- [x] **Mock service layer**: all data via `js/api/client.js`. DoD: no `fetch` or mock ids outside `js/api/` (checked by grep).
- [~] **Viewer**: zoom (wheel/buttons/keys), pan (drag/arrows), reset, fullscreen, markers, brightness/contrast.
- [~] **Timeline + playback**: dots, dates, play/pause/stop/step, speed 0.5–4×; viewer and timeline driven by one store value.
- [~] **Candidates panel**: list, score labelled "not a probability", evidence, warnings, selection highlights marker.
- [~] **Object details**: Catalogue / Derived by this app / Observation blocks, ± uncertainty, quality flags.
- [x] **States in the service layer**: empty, `processing` (polled), `unavailable`, `failed`/network, AI unavailable, not_comparable.
- [~] **States in the UI**: each of the above rendered (viewer overlay, notices, retry buttons).
- [~] **Illustrative labelling**: page badge + viewer HUD badge whenever mocks are on.

## P1 Important
- [~] **Compare**: blink, side-by-side (linked zoom/pan), overlay (opacity), difference; verdict and every check always visible.
- [~] **Compare not_comparable**: images hidden, failing check explained (WISE 0855 dates 1 vs 2).
- [~] **Spectrum**: band strip from `expectedBandCount`, gaps hatched, chart with error bars only where provided, second-band comparison.
- [~] **Navi companion**: panel, suggestions, thinking, error + retry, references, caveats, next-step actions, beginner/advanced.
- [x] **Navi mock replies**: scripted answers, modes, references, caveats, actions, error hook (`simulate error`).
- [~] **Guided "Spot the shift"**: 3 steps driven by workspace events, hints, feedback, progress.
- [~] **Landing page**: cover close to the concept, skippable intro, mission section.

## P2 Optional
- [~] **Notes / bookmarks / history** persisted in the store (survive reload).
- [x] **Saved investigations** (create/list/get/update/delete through the service; mock = localStorage).
- [~] **AI summary** button on Notes.
- [~] **Learn** glossary page.
- [ ] Pinch-zoom on touch screens.
- [ ] Cross-browser pass (Safari, Firefox).

## Manual browser pass (do this before demoing)
1. `cd frontend && python3 -m http.server 8000`, open `/index.html`, then **Begin exploring**.
2. Investigate: candidates list shows 3; click one → marker turns orange, evidence appears, derived measurement loads.
3. Press play: viewer image and timeline dot change together; change speed; pause; click a date.
4. Barnard date 5 shows "image being prepared" then loads; R Draconis date 4 shows "unavailable".
5. Zoom with wheel, drag to pan, press `0` to reset, `F` for fullscreen; hover shows RA/Dec.
6. Compare: try all four views; pick WISE 0855 dates 1 and 2 → "Not comparable", no images.
7. Spectrum: click a hatched band (no data); click the chart; tick "Compare a second band".
8. Navi: open Chat, ask a suggested question, type `simulate error` and use Try again; click a next-step button.
9. Guided: Start → step through dates → click the moving source → blink in Compare → complete.
10. Notes: add note, bookmark, save, reload page, load saved investigation.
11. Resize to ~900 px and ~390 px; enable "reduce motion" in the OS and reload.

## Final verification
- [x] Fixtures check passes; Node smoke test passes (module linking, mock service layer, store, real-client error mapping).
- [x] No backend or shared-contract files modified (`git status`: only `frontend/`, `docs/FRONTEND_*`, `docs/proposals/`).
- [~] Responsive at 1280 / 900 / 390 px; reduced-motion honoured (CSS written, not browser-verified).
- [x] Docs match implementation.
