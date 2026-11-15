# Frontend Implementation Plan

Phases in dependency order. P0 first; everything after F is built on mocks behind the API client.

| # | Phase | Delivers | Depends on | Status |
|---|---|---|---|---|
| 1 | Foundation and design system | tokens, base/components CSS, store, router, storage, API client, style guide | none | done (commit `d664a09`) |
| 2 | Mock service layer | fixtures for 5 targets + observations + spectra, procedural imagery, candidates, chat/guided scripts, investigations store, contract conformance check | 1 | built, Node-verified |
| 3 | Shell and navigation | `app.html`, top bar, tool tabs, view mount/unmount, global Navi mount | 1 | built, not browser-verified |
| 4 | Astronomical viewer | `js/components/viewer.js` zoom/pan/markers/filters/fullscreen | 2 | built, not browser-verified |
| 5 | Investigation workspace | candidates, object details, timeline, playback, target picker | 2, 4 | built, not browser-verified |
| 6 | Comparison | blink, side-by-side, overlay, difference, compatibility display | 4, 5 | built, not browser-verified |
| 7 | Spectral explorer | band strip, chart, readouts | 2, 4 | built, not browser-verified |
| 8 | AI companion | floating Navi, chat panel, mock replies, actions | 2, 3 | built, not browser-verified |
| 9 | Guided investigations | "Spot the shift", hints, progress | 5, 6, 8 | built, not browser-verified |
| 10 | Notes, history, persistence | notes, bookmarks, saved investigations | 2, 5 | built, not browser-verified |
| 11 | Landing page | cover similar to concept, mission section | 1 | built, not browser-verified |
| 12 | Verification and polish | smoke tests, responsive and keyboard checks, docs aligned | all | built, not browser-verified |

## Architecture

```
frontend/
  index.html  app.html  styleguide.html
  css/        tokens base components landing workspace
  tools/                   check-fixtures.mjs, smoke.mjs (Node, no deps)
  js/
    app.js                 shell, routing, view lifecycle
    landing.js             landing intro (skippable)
    core/                  store, router, storage, dom, playback, frames
    api/                   client.js (only data entry), types.js
      mock/                mockApi, imagery, chat, investigations, fixtures
    components/            viewer, timeline, navi, objectDetails, guided
    views/                 investigate, compare, spectrum, notes, learn
  mock/                    JSON fixtures (illustrative): objects, observations, spectra, proposed
```

Rules: views never `fetch()` or hold data; mock data only in `mock/` and `js/api/mock/`; every view exports
`mount(container)` returning an unmount function; shared state only through the store; no scientific computation
outside the mock layer (and none in real mode).

## Backend swap

Set `CONFIG.useMock = false`. Operations #1-#18 map 1:1 to `js/api/client.js` functions. Features backed by
`mock/proposed.json` (candidates, guided investigation, proper motion, chat suggestions) show "unavailable" until the contract
proposal is agreed.

## Risks

- Real images may be large: viewer is raster + CSS transform only; tiling is OD-5.
- Pinch zoom and multi-touch are not implemented (wheel, buttons, keyboard, drag only).
- No browser was available in the build environment: logic is verified by `tools/smoke.mjs` and `tools/check-fixtures.mjs`; UI behaviour needs the manual pass in `FRONTEND_CHECKLIST.md`.
- Mock imagery uses a canvas, so generated pixels are only checkable in a browser.
