"""Extract a project's custom drawing-sheet (worksheet / ``.kicad_wks``) template.

KiCad projects can define a custom page-layout template (the frame + title block).
Modern KiCad *embeds* that ``.kicad_wks`` file inside the schematic's
``(embedded_files ...)`` block as a base64-encoded, Zstandard-compressed blob and
references it from ``.kicad_pro`` via
``"page_layout_descr_file": "kicad-embed://<name>.kicad_wks"``.

The vendored ecad-viewer never reads embedded files, so it always falls back to its
bundled default worksheet -- i.e. it *ignores the project's template*. This module
recovers the raw ``.kicad_wks`` text so the backend can hand it to the viewer.

Kept dependency-light (stdlib + ``zstandard``) and framework-free so it is
host-testable in isolation, mirroring ``archive_utils`` / ``schematic_flatten_service``.
"""

from __future__ import annotations

import base64
import re
from typing import Callable, Optional

# ``(type worksheet)`` always precedes ``(data |<base64>|)`` in a KiCad embedded
# file, so anchoring on the type lets us grab the matching data blob even when
# several files are embedded (fonts, datasheets, ...).
_WORKSHEET_DATA_RE = re.compile(
    r"\(\s*type\s+worksheet\s*\).*?\(\s*data\s+\|(?P<data>.*?)\|",
    re.DOTALL,
)

# Fallback: any embedded file whose name ends in .kicad_wks (order-independent).
_NAMED_WKS_RE = re.compile(
    r'\(\s*name\s+"(?P<name>[^"]*\.kicad_wks)"\s*\)(?P<rest>.*?\(\s*data\s+\|(?P<data>.*?)\|)',
    re.DOTALL,
)

FileLoader = Callable[[str], Optional[str]]


def _decode_kicad_embedded_blob(data_b64: str) -> Optional[bytes]:
    """base64-decode + Zstd-decompress a KiCad embedded-file data blob.

    Returns ``None`` on any decode/decompress failure rather than raising, so a
    malformed embed simply falls back to the viewer's default worksheet.
    """
    try:
        raw = base64.b64decode("".join(data_b64.split()))
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    try:
        import zstandard  # imported lazily so the module imports without the dep
    except ImportError:  # pragma: no cover - dependency is declared in requirements
        return None

    dctx = zstandard.ZstdDecompressor()
    try:
        return dctx.decompress(raw)
    except zstandard.ZstdError:
        # Frames written without an embedded content size need the streaming API.
        import io

        try:
            return dctx.stream_reader(io.BytesIO(raw)).read()
        except zstandard.ZstdError:
            return None


def extract_embedded_worksheet(schematic_content: str) -> Optional[str]:
    """Return the embedded worksheet's raw ``.kicad_wks`` text, or ``None``.

    Looks for an ``(embedded_files ... (file ... (type worksheet) (data |..|)))``
    entry, preferring the explicit ``worksheet`` type and falling back to any
    ``*.kicad_wks`` embedded file.
    """
    if "(embedded_files" not in schematic_content:
        return None

    match = _WORKSHEET_DATA_RE.search(schematic_content)
    if match is None:
        match = _NAMED_WKS_RE.search(schematic_content)
    if match is None:
        return None

    decoded = _decode_kicad_embedded_blob(match.group("data"))
    if not decoded:
        return None
    text = decoded.decode("utf-8", errors="replace")
    # Sanity check: it should look like a worksheet s-expression.
    return text if "(kicad_wks" in text else None


def _page_layout_ref(project_settings_content: Optional[str]) -> Optional[str]:
    """Read ``page_layout_descr_file`` from a ``.kicad_pro`` (JSON) text, if present."""
    if not project_settings_content:
        return None
    match = re.search(
        r'"page_layout_descr_file"\s*:\s*"((?:[^"\\]|\\.)*)"',
        project_settings_content,
    )
    if not match:
        return None
    return match.group(1).encode().decode("unicode_escape")


def resolve_worksheet(
    root_schematic_content: str,
    project_settings_content: Optional[str] = None,
    load_file: Optional[FileLoader] = None,
) -> Optional[str]:
    """Resolve the effective ``.kicad_wks`` text for a project.

    Resolution order:
      1. If ``.kicad_pro`` names an external file (a real path, not ``kicad-embed://``)
         and it can be read via ``load_file``, use it.
      2. Otherwise fall back to the schematic's embedded worksheet.

    Returns ``None`` when the project uses the default worksheet (empty ref and no
    embedded template) or the template cannot be recovered -- the viewer then keeps
    its bundled default.
    """
    ref = _page_layout_ref(project_settings_content)
    if ref and not ref.startswith("kicad-embed://") and load_file is not None:
        external = load_file(ref)
        if external and "(kicad_wks" in external:
            return external

    return extract_embedded_worksheet(root_schematic_content)
