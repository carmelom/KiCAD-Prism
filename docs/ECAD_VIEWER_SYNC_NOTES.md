# ecad-viewer Sync Notes

This document tracks the current upstream sync reference for the vendored visualizer assets.

## Current Reference

- sync date: 2026-02-27
- upstream ref used for the Prism vendor refresh: `origin/main`
- upstream commit used for the current refactor baseline: `a85abd4`

### Local render-fix patches (2026-07-09)

`frontend/public/ecad-viewer.js` is now built from `a85abd4` **plus** two local
patches, not a plain upstream artifact. Base + patches live in the sibling clone
`../ecad-viewer` on branch `prism/render-fixes` (commit `43e0e8a`), forked from
`a85abd4fb31a493fc1bec9fb1908741043fd7ff5` (recovered via the GitHub API — the
short SHA was rewritten off upstream `main` by the later worker-pool migration).

Patches (schematic renderer only):
- **Dashed/dotted strokes** — `determine_stroke` maps KiCad stroke types to canvas
  dash patterns, threaded through `Polyline`/painters/`DrawCommand`.
- **Table cell rendering** — new `Table`/`TableCell` parser classes + `TablePainter`
  (external border, separators, per-cell text). Backported from the b8d8019 lineage
  but rewritten in a85abd4's parser-combinator idiom.

We deliberately stayed on the `a85abd4` base (not upstream `b8d8019`) because the
worker-pool migration in b8d8019 destabilised Firefox 114+ (see RESUME.md).

Rebuild (docker, single-package a85abd4 layout — note: NOT the b8d8019 `packages/*`
command in older notes):
```
cd ../ecad-viewer   # on branch prism/render-fixes
docker run --rm -v "$PWD":/app -w /app node:20 bash -lc \
  'rm -rf node_modules build && npm install && npm run build:no-check \
   && npm run build:glyph && npm run build:3d'
cp build/ecad-viewer.js frontend/public/   # only ecad-viewer.js changed;
                                            # glyph-full.js/3d-viewer.js unaffected
```
`glyph-full.js` rebuilds byte-identical; `3d-viewer.js` differs only by toolchain
drift (not our changes), so we keep the committed copies of both.

## Vendored Artifacts

The current sync updated:
- `frontend/public/ecad-viewer.js`
- `frontend/public/glyph-full.js`
- `frontend/public/3d-viewer.js`

## Scope Notes

Visualizer code is intentionally treated as a higher-risk surface than the rest of the app.

That means:
- general frontend cleanup should avoid changing vendored visualizer assets unless the task is explicitly a visualizer/vendor sync
- performance or bundle work outside the visualizer should isolate viewer-specific chunks rather than rewriting the viewer surface itself

If you need to update visualizer behavior, treat it as a dedicated task with explicit validation.
