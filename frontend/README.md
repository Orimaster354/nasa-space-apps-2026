# SPHERE X frontend

Plain HTML, CSS and ES modules. No build step, no dependencies.

## Run

```bash
cd frontend
python3 -m http.server 8000      # ES modules need http://, not file://
# open http://localhost:8000/app.html   (style guide: /styleguide.html)
```

## Check

```bash
node frontend/tools/check-fixtures.mjs   # mock fixtures vs contract v0.1.0
```

## Layout

| Path | Purpose |
|---|---|
| `css/tokens.css` | Approved palette, page gradient (same on every page), type, spacing |
| `css/base.css`, `css/components.css` | Reset, focus, shared components |
| `js/core/` | `store.js` (shared investigation state), `router.js`, `storage.js`, `dom.js` |
| `js/api/` | `client.js` (only data entry point), `types.js` (contract JSDoc), `mock/` |
| `mock/` | Contract-shaped, **illustrative** fixtures |
| `assets/navi/navi.png` | Navi, cropped unmodified from `design-refs/navi-reference.png`, background keyed transparent |
| `design-refs/` | Approved visual references (source of truth) |

## Rules

- Views get data only through `js/api/client.js`. To use the Python backend set `CONFIG.useMock = false` in `js/config.js`.
- Mock data must stay labelled "illustrative". Candidates are never described as discoveries.
- Contract changes go through `docs/proposals/` and the process in `docs/DEVELOPMENT_OWNERSHIP.md`.
