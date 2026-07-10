# ecad-viewer Sync Notes

This document tracks the vendored visualizer: where its source lives, how we
carry local patches, how upstream fixes are ingested, and how the shipped blob
is rebuilt.

## Source layout & upstream sync (git subtree)

The viewer's **source** lives in this repo at `vendor/ecad-viewer/`, imported via
`git subtree` (squashed). This is a deliberate monorepo choice: we are not the
owner of `Huaqiu-Electronics/ecad-viewer`, we will not upstream our changes, and
Prism is internal-only — so the fork's source-of-truth and our patches belong in
our own history, not in a loose external clone.

- **Our patches are ordinary commits here.** Edit under `vendor/ecad-viewer/`,
  commit, and the change is versioned like any other file.
- **The shipped artifact is the prebuilt blob**, `frontend/public/ecad-viewer.js`
  (see "Rebuild" below). The deploy loads it as a static asset
  (`frontend/index.html` → `<script src="/ecad-viewer.js">`); it is **not** built
  in the frontend Docker image. Rebuilding is a deliberate, documented step.

Remotes (local to each clone; not committed):
- `ecad-viewer-upstream` → `https://github.com/Huaqiu-Electronics/ecad-viewer.git`
- `ecad-viewer-fork` → the original local patched clone `../ecad-viewer` (legacy;
  optional — the subtree is now canonical).

**Ingest an upstream fix** (into `vendor/ecad-viewer/`, squashed, conflicts
resolved in-repo):
```
git remote add ecad-viewer-upstream https://github.com/Huaqiu-Electronics/ecad-viewer.git  # once per clone
git subtree pull --prefix vendor/ecad-viewer ecad-viewer-upstream <ref> --squash
```
Note: upstream `main` moved to a Comlink worker-pool parser (commit `b8d8019`)
that destabilised Firefox 114+ (see RESUME.md). We stay on the `a85abd4`
baseline; pull **specific refs/cherry-picks**, not `main`, until that migration
is resolved.

## Current Reference

- sync date: 2026-02-27
- upstream ref used for the Prism vendor refresh: `origin/main`
- upstream commit used for the current refactor baseline: `a85abd4`

### Local render-fix patches (2026-07-09)

`frontend/public/ecad-viewer.js` is built from `a85abd4` **plus** two local
patches, not a plain upstream artifact. Base + patches now live in-repo at
`vendor/ecad-viewer/` (subtree-imported from the fork branch `prism/render-fixes`
@ `6c4bf54`), forked from `a85abd4fb31a493fc1bec9fb1908741043fd7ff5` (recovered
via the GitHub API — the short SHA was rewritten off upstream `main` by the later
worker-pool migration).

Patches (schematic renderer only):
- **Root-sheet landing** — `Project.get_first_page` prefers a blob literally named
  `root.kicad_sch`. PRISM's flattened feed always names the top-level sheet that, so
  the viewer opens on the true root instead of a name-sorted subsheet.
- **Dashed/dotted strokes** — `determine_stroke` maps KiCad stroke types to canvas
  dash patterns, threaded through `Polyline`/painters/`DrawCommand`.
- **Table cell rendering** — new `Table`/`TableCell` parser classes + `TablePainter`
  (external border, separators, per-cell text with word-wrap to the cell width via
  the font's `break_lines`, matching KiCad's SCH_TABLECELL). Backported from the
  b8d8019 lineage but rewritten in a85abd4's parser-combinator idiom.

We deliberately stayed on the `a85abd4` base (not upstream `b8d8019`) because the
worker-pool migration in b8d8019 destabilised Firefox 114+ (see RESUME.md).

Rebuild (docker, single-package a85abd4 layout — note: NOT the b8d8019 `packages/*`
command in older notes). Run from the in-repo source tree:
```
cd vendor/ecad-viewer
docker run --rm -v "$PWD":/app -w /app node:20 bash -lc \
  'rm -rf node_modules build && npm install && npm run build:no-check \
   && npm run build:glyph && npm run build:3d'
cp build/ecad-viewer.js ../../frontend/public/   # only ecad-viewer.js changed;
                                                  # glyph-full.js/3d-viewer.js unaffected
```
Then commit both the `vendor/ecad-viewer/` source change and the regenerated
`frontend/public/ecad-viewer.js` together.
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
