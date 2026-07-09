# RESUME — Current Status

> Durable standards/architecture live in `CLAUDE.md`. This file tracks live status,
> decisions, open items, and the next task. Update it at the end of every iteration.

Last updated: 2026-07-09

## Goal (from CLAUDE.md)

Two features, done **phased**:

1. **Hierarchy navigation** (primary parity goal) — a "Schematic Hierarchy" side-panel
   tree + back / up-a-sheet / forward toolbar buttons, with correct resolution of
   sheet **instances** (one subsheet file instantiated multiple times) and per-instance
   references/page numbers. See `spec/UI_features_missing.jpg`.
2. **Project upload** via the web client (currently GitHub-clone only).

Order: **#1 first, then #2** (upload slots into a correct hierarchy model).

## Key decisions

- **Backend-authoritative hierarchy.** We compute the correct UUID-sheet-path hierarchy
  and per-instance references in the Python backend, expose them via a new endpoint,
  render the tree + navigation in React, and drive the existing viewer via its
  `switchPage()` API.
- **The viewer (`frontend/public/ecad-viewer.js`) is a vendored prebuilt blob** synced
  from an upstream repo (see `docs/ECAD_VIEWER_SYNC_NOTES.md`, commit `a85abd4`). It is a
  higher-risk surface and is **not modified** in this repo. We use only its existing
  public API/events (`switchPage(pageId)`, `kicanvas:sheet:loaded`, `kicanvas:sheet:change`).

## Architecture findings (scoped analysis, 2026-07-09)

- **Backend** (FastAPI, `backend/app`): treats projects as raw git file repos. Only
  regex metadata parsing exists (`services/project_properties_service.py`). **No sheet
  hierarchy resolution.** `/schematic/subsheets` returns a flat list of `.kicad_sch`
  filenames. Path resolution: `services/path_config_service.py`
  (`_select_root_schematic`, `_detect_subsheets_path`); `services/project_service.py`
  (`find_schematic_file`, `get_subsheets`).
- **Frontend** (React+TS+Vite, `frontend/src`): rendering is entirely the vendored
  `<ecad-viewer>` web component (`components/visualizer.tsx`). No hierarchy model —
  just a single `activePage` string. The `panel/` dir is an unrelated symbol finder.
  No tree, no nav buttons, no React-level dblclick wiring.
- **KiCad file model** (validated against `spec/amplified-photodiode/board`,
  v20250114): the **sheet-path UUID chain is the join key**. Symbol instance
  `(path "/rootUUID/sheetUUID/…")` = the sheet path (symbol UUID NOT appended in this
  version). Page number keyed by parent path; root page in top-level
  `(sheet_instances (path "/" (page …)))`. Multiple `(path …)` entries = multi-instance.
  Reference algorithm mirrors KiCad `SCH_SYMBOL::GetRef` (`spec/kicad-source-mirror/
  eeschema/sch_symbol.cpp:920`).

## Test fixture

`spec/amplified-photodiode/board/` — root `amplified_photodiode_board.kicad_sch` +
sibling subsheets `transimpedance_amplifier.kicad_sch`, `power.kicad_sch`,
`mechanical.kicad_sch`. (Subsheets are siblings, not in a `Subsheets/` dir — good
path-resolution edge case.)

## Open items / risks

- **Validation gate:** the vendored viewer may key pages by filename, not by
  instance-path. If so, entering instance A vs. B of the same subsheet renders identical
  designators on the canvas even though our panel is correct. Verify `switchPage()`
  instance targeting once the resolver + panel exist. If it can't, per-instance
  re-annotation becomes a scoped upstream-viewer task (out of current scope).
- This fixture instantiates each subsheet once; find/build a multi-instance fixture to
  fully exercise per-instance references before declaring parity.

## Done so far (Phase 1 backend)

- `backend/app/services/schematic_hierarchy_service.py` — recursive-descent s-expr
  parser + pure resolver (`resolve_from_content` / `resolve_from_directory`). Handles
  modern embedded instances; matches symbol paths with or without a trailing symbol
  UUID; guards cycles + depth; marks unresolved children.
- `GET /api/projects/{id}/schematic/hierarchy?commit=` in `backend/app/api/projects.py`
  (works for both working-tree and commit modes; flat `/subsheets` left intact).
  Response: `{rootUuid, version, root:<tree>, references:{sheetPath:{symbolUuid:{reference,unit}}}}`.
  Tree node: `{sheetPath, displayPath, name, file, page, children[, unresolved]}`.
- `backend/tests/test_schematic_hierarchy_service.py` — 11 tests, all green
  (validated against the fixture: pages 1/2/3/4, root `J102` vs. TIA `R201`/`R211`).
- Verified: `py_compile` clean; unit tests pass. (FastAPI isn't on the host Python, so
  the router itself is only import-checked inside the container.)

## Done so far (Phase 1 frontend)

- `frontend/src/types/schematic-hierarchy.ts` — `SchematicHierarchy` / `SchematicHierarchyNode` types.
- `frontend/src/hooks/use-schematic-hierarchy.ts` — fetches the new endpoint (gated by `enabled`).
- `frontend/src/components/schematic-hierarchy-tree.tsx` — collapsible "Schematic
  Hierarchy" tree; shows page numbers; highlights the active instance; unresolved
  sheets flagged. (The `panel/` dir is unrelated and untouched.)
- `frontend/src/components/visualizer.tsx` — integrated:
  - hierarchy panel docked left on the schematic tab (toggle via new "Hierarchy" button);
  - **back / up-a-sheet / forward** toolbar buttons backed by a nav-history stack
    (`{stack, index}`);
  - panel click / up / back / forward drive the viewer via `switchPage(pageId)`;
  - `kicanvas:sheet:loaded` (incl. canvas double-click-to-enter) syncs the panel +
    history without re-driving the viewer (`suppressDriveRef` / `lastDrivenRef` guards);
  - nav state resets on project/commit change.

### Confirmed viewer limitation (validation gate)
The vendored viewer loads the root as `filename: "root.kicad_sch"` and keys pages by
**filename** (`switchPage(pageId)` + `kicanvas:sheet:loaded` are filename-based). So on
the canvas, multiple instances of the same subsheet file render the **same page** and
references are not re-annotated per instance. The backend + hierarchy **panel** are
per-instance-correct; only the rendered canvas is viewer-limited. `pageIdForSheetPath`
in `visualizer.tsx` is the single mapping point to fix if/when the upstream viewer
gains instance-aware `switchPage` (option 3, a separate vendor-sync task).

## Verification status

- Backend: unit tests green; `py_compile` clean. Router import-checked only in container.
- Frontend: **could not type-check on host** (no `node_modules`; `tsc` runs in the
  container build). `tsconfig.app.json` has `noUnusedLocals`/`noUnusedParameters` on —
  container build is the real check. Code reviewed by hand: imports all used, hooks
  unconditional, JSX balanced.

## Done so far (Phase 2 backend — upload endpoint)

- `backend/app/services/archive_utils.py` — dependency-free (stdlib-only) helpers,
  isolated so the security-critical logic is host-testable: `sanitize_project_name`,
  `safe_extract_zip` (rejects zip-slip / absolute paths, bounds uncompressed size via
  `MAX_UPLOAD_BYTES`), `find_content_root` (descends a single wrapping folder).
- `backend/app/services/project_import_service.py::import_uploaded_archive(bytes,
  filename, display_name)` — validates zip, safe-extracts, `git init` + initial commit
  (so commit-scoped endpoints work like a clone), discovers project(s), moves into
  `PROJECTS_ROOT/type1|type2/<unique-name>`, registers repo (`url="upload://<name>"`)
  + project(s) via the same workspace calls as the clone import. Rolls back on failure.
- `POST /api/projects/upload` (`require_designer`, multipart `file` + optional `name`)
  in `backend/app/api/projects.py` — returns `{status, import_type, repo_id,
  project_ids, name, message}`. Synchronous (no job/poll; unzip is fast).
- `backend/tests/test_archive_utils.py` — 9 tests, all green (zip-slip, absolute-path,
  sanitize, content-root). `py_compile` clean. Full flow (git/workspace/move) verified
  only in-container (host lacks git/pydantic/fastapi).

## Frontend contract for the upload UI (task #6)

`POST /api/projects/upload`, `multipart/form-data`: field `file` (the .zip, required),
field `name` (optional display name). 200 → the result object above; 400 → `{detail}`
for bad/empty archive or no `.kicad_pro`. Extend `frontend/src/components/import-dialog.tsx`
(currently GitHub-clone only) with an "Upload .zip" mode.

## Done so far (Phase 2 frontend — upload UI)

- `frontend/src/components/import-dialog.tsx` — added an **"Upload .zip"** mode beside
  the existing "From GitHub" flow (mode toggle at the top of the input step):
  file picker (`accept=".zip"`) + optional name field → `POST /api/projects/upload`
  (multipart `file` + `name`), new `uploading` step (spinner), reuses the shared
  `complete` step, and calls `onImportComplete()` to refresh the project list.
  GitHub clone flow untouched.
- `frontend/nginx.conf` — added `client_max_body_size 512M` (default was 1 MB, which
  rejected multi-MB archives with a 413 before reaching the backend). Baked into the
  image at build time → **frontend container must be rebuilt** to apply. The optional
  Caddy proxy has no default body limit, so no change needed there.
- Verification: reviewed by hand (TS narrowing on the `uploading` step, all new state
  used so `noUnusedLocals` is satisfied). Host `tsc` unavailable — container build is
  the real check.

## Fixed (bughunt)

- **Nested subsheets stopped at depth 2.** The resolver resolved every child
  `Sheetfile` relative to the *root* dir, but KiCad stores it relative to the *parent*
  sheet's dir — so any subsheet in a subdirectory (e.g. `Subsheets/`) couldn't resolve
  its own children. Fixed in `schematic_hierarchy_service.py` by threading each screen's
  `base_dir` through `walk` and resolving `child_rel = normpath(join(base_dir, Sheetfile))`
  (root-relative); cache/visited now keyed by that path. Regression test:
  `NestedSubdirHierarchyTests` (12 resolver tests total, all green). **Backend rebuild
  required** to apply.

## Fixed (bughunt) — per-instance reference designators on canvas

Confirmed by reading the vendored bundle: `ecad-viewer` renders each symbol's baked-in
`Reference` property and navigates by filename; it never consumes the `instances` block.
So two instances of a shared subsheet rendered identical designators.

Chosen fix (user's call): **backend synthetic per-instance blobs** (no viewer change).
- `backend/app/services/schematic_flatten_service.py` — `build_flattened_blobs(...)`:
  resolves the hierarchy, then materializes ONE `.kicad_sch` blob per sheet instance.
  Each symbol's `Reference` property is rewritten to the resolver's per-instance value,
  and each child `(sheet)`'s `Sheetfile` is repointed at the child instance's synthetic
  filename. Rewrites are scoped, offset-preserving string edits on the raw text (only
  property *values* change) — `lib_symbols` and pin uuids are untouched. Root keeps the
  `root.kicad_sch` name; other instances get `sheet_<md5[:16]>.kicad_sch`.
- `GET /api/projects/{id}/schematic/flattened?commit=` → `{blobs:[{filename, sheetPath,
  content, isRoot}]}` (root first). Working-tree + commit modes.
- `backend/tests/test_schematic_flatten_service.py` — 5 tests incl. same-file-twice →
  R1 vs R2, root Sheetfile repointing, lib_symbols untouched. All green.
- Frontend `visualizer.tsx` now fetches `/schematic/flattened` and feeds those blobs to
  the viewer (replacing the old `/schematic` + `/schematic/subsheets` flat load).
  Panel nav maps `sheetPath → synthetic filename` via `blobFilenameByPath`.
- **Requires rebuilding BOTH backend and frontend.**

Caveats: payload multiplies for heavily-instantiated designs (one blob per instance);
schematic comment page-identity now uses synthetic filenames, so pre-existing subsheet
comments may not map (root comments unaffected). Old `/schematic` + `/subsheets`
endpoints remain but are no longer used by the viewer.

## Renderer fidelity (ecad-viewer) — REVERTED to stable, deferred

We bumped the vendored viewer `a85abd4` → latest `b8d8019` (+ local patches: dashed lines,
empty-number pin fix, render-crash guards) to chase renderer gaps. Upstream fixed beziers +
DNP markers, BUT the new bundle **destabilized rendering in Firefox 114+** with cascading
crashes: `clear_canvas` `ctx2d` undefined, `DisposableStack already disposed`
(`connectedCallback`/`setup_events`), and a `DOMException: Operation is not supported`. The
new bundle also switched the parser to a **Comlink module-worker pool** and **removed the
cross-probe API**.

**Decision: reverted the vendor bump** (2026-07-09). Restored the stable a85abd4 artifacts
from git (`frontend/public/{ecad-viewer,glyph-full,3d-viewer}.js`) and deleted the new
`parser.worker.js`. Our own code (hierarchy nav + flattened per-instance blobs in
`visualizer.tsx`, the `/schematic/hierarchy` + `/schematic/flattened` endpoints) is KEPT —
it was confirmed working with the stable bundle. Cross-probe guards in `visualizer.tsx` /
optional typing in `ecad-viewer.d.ts` are kept (harmless; stable bundle has cross-probe).

**Known renderer limitations (re-accepted, deferred to a dedicated visual-iteration effort):**
- Dashed/dotted lines render solid.
- Table cell content not rendered (borders only).
- Symbols with empty/duplicate pin numbers (e.g. `Lakeshore_TempCtrl` / "Model 336") drop
  pins — `pin_by_number("")` collision in the parser.
- Bezier + DNP-marker improvements from `b8d8019` are not present in the stable bundle.

**For a future renderer effort:** the patched upstream clone is at sibling dir
`../ecad-viewer` (Huaqiu-Electronics/ecad-viewer, canvas2d schematic renderer). Build via
docker: `docker run --rm -v "$PWD":/app -w /app node:20 bash -lc 'npm install && (cd
packages/kicad-parser && npm run build) && (cd packages/ecad-viewer-app && npm run
build:no-check && npm run build:glyph && npm run build:3d)'`. Patches applied there:
dashed-line stroke (`painters/base.ts` + `Polyline`/`DrawCommand`/canvas2d `setLineDash`),
empty-number pin resolution (`schematic.ts` `pins_in_order`/`pin_by_index`/`declaration_index`),
`clear_canvas`/render-layer null-`ctx2d` guards, and temporary `[prism-dash-debug]` /
`[prism-pin-debug]` console logs. Do it with live visual verification in the target browser
before re-vendoring. The `parser.worker.js` module-worker + lifecycle races (Firefox) must
be resolved before any future re-vendor.

## Deferred (bughunt later — user's call)

1. **Import dialog brightness/alpha looks off.** Overlay is `bg-black/80` (see
   `frontend/src/components/ui/dialog.tsx:43`); investigate whether it's the overlay
   opacity, a stacked/duplicate overlay, or theme interaction.
2. **Blank-page fragility (root cause of the earlier black screen).**
   `frontend/src/components/workspace.tsx:465+` wraps every lazy dialog in
   `<Suspense fallback={null}>` with **no error boundary**, so any chunk-load failure
   (e.g. stale cache after redeploy) unmounts the whole app to black. Add an error
   boundary. (The earlier black page was resolved by a hard refresh = stale chunk.)

## Next task

- Container build + functional verification of the full Phase 1 + Phase 2 flow.
- Then the deferred bughunt items above (when the user is ready).

After each iteration: confirm the container starts and backend+frontend stay alive
(functional testing is done by the user).
