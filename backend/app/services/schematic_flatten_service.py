"""Flatten a hierarchical schematic into per-instance blobs for the viewer.

The vendored ecad-viewer renders each symbol's baked-in ``Reference`` property and
navigates purely by filename -- it does not resolve per-instance references from the
``instances`` block. So two instances of the same subsheet file render identical
designators.

To work around this without modifying the viewer, we materialize one distinct
``.kicad_sch`` blob per sheet *instance*: each symbol's ``Reference`` property is
rewritten to the value the backend resolver computed for that instance, and each
child ``(sheet)``'s ``Sheetfile`` is repointed at the child instance's synthetic
filename. The viewer then loads the correctly-annotated file per instance.

Rewriting is done as scoped, offset-preserving string edits on the raw file text
(only the specific property *values* change) so the on-disk format the viewer parses
stays byte-for-byte identical everywhere else.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Callable, Dict, List, Optional

from app.services.schematic_hierarchy_service import resolve_from_content

SheetLoader = Callable[[str], Optional[str]]

_UUID_RE = re.compile(r'\(uuid\s+"([^"]+)"')


def _synthetic_name(sheet_path: str) -> str:
    digest = hashlib.md5(sheet_path.encode("utf-8")).hexdigest()[:16]
    return f"sheet_{digest}.kicad_sch"


def _head_at(text: str, open_paren: int) -> str:
    """Return the head symbol of the list starting at ``open_paren`` (a '(')."""
    j = open_paren + 1
    n = len(text)
    while j < n and text[j] in " \t\r\n":
        j += 1
    k = j
    while k < n and text[k] not in " \t\r\n()\"":
        k += 1
    return text[j:k]


def _top_level_blocks(text: str) -> List[tuple]:
    """Return (head, start, end) spans for the direct children of the root s-expr.

    Only direct children are returned, so nested ``(symbol ...)`` inside
    ``(lib_symbols ...)`` and pin/instance sub-nodes are never mistaken for
    top-level placements. String contents (and escapes) are skipped.
    """
    n = len(text)
    i = 0
    while i < n and text[i] != "(":
        i += 1
    if i >= n:
        return []
    i += 1  # step inside the root list
    depth = 1
    in_str = False
    block_start: Optional[int] = None
    blocks: List[tuple] = []
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            if depth == 1:
                block_start = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 1 and block_start is not None:
                blocks.append((_head_at(text, block_start), block_start, i + 1))
                block_start = None
            elif depth == 0:
                break
        i += 1
    return blocks


def _first_uuid(block: str) -> Optional[str]:
    match = _UUID_RE.search(block)
    return match.group(1) if match else None


def _replace_property_value(block: str, key: str, new_value: str) -> str:
    """Replace the value of the first ``(property "key" "value" ...)`` in ``block``."""
    pattern = re.compile(r'(\(property\s+"' + re.escape(key) + r'"\s+")((?:[^"\\]|\\.)*)(")')
    escaped = new_value.replace("\\", "\\\\").replace('"', '\\"')
    return pattern.sub(lambda m: m.group(1) + escaped + m.group(3), block, count=1)


def _rewrite(
    text: str,
    reference_by_symbol: Dict[str, str],
    sheetfile_by_sheet: Dict[str, str],
) -> str:
    if not reference_by_symbol and not sheetfile_by_sheet:
        return text
    # Apply edits from the end so earlier (lower-offset) spans stay valid even when
    # a replacement changes length.
    for head, start, end in sorted(_top_level_blocks(text), key=lambda b: b[1], reverse=True):
        if head not in ("symbol", "sheet"):
            continue
        block = text[start:end]
        uuid = _first_uuid(block)
        if head == "symbol" and uuid in reference_by_symbol:
            block = _replace_property_value(block, "Reference", reference_by_symbol[uuid])
        elif head == "sheet" and uuid in sheetfile_by_sheet:
            block = _replace_property_value(block, "Sheetfile", sheetfile_by_sheet[uuid])
        else:
            continue
        text = text[:start] + block + text[end:]
    return text


def build_flattened_blobs(
    root_filename: str,
    root_content: str,
    load_sheet: SheetLoader,
) -> List[dict]:
    """Produce one annotated blob per sheet instance.

    Returns a list of ``{filename, sheetPath, content, isRoot}`` with the root first.
    The root keeps the ``root.kicad_sch`` filename the viewer expects; every other
    instance gets a unique synthetic filename derived from its sheet path.
    """
    resolved = resolve_from_content(root_filename, root_content, load_sheet)
    references = resolved["references"]
    root_node = resolved["root"]

    text_cache: Dict[str, Optional[str]] = {}

    def get_text(rel_path: str) -> Optional[str]:
        if rel_path not in text_cache:
            text_cache[rel_path] = load_sheet(rel_path)
        return text_cache[rel_path]

    blobs: List[dict] = []

    def emit(node: dict, text: Optional[str], base_dir: str, filename: str, is_root: bool) -> None:
        if text is None:
            return
        sheet_path = node["sheetPath"]
        reference_by_symbol = {
            symbol_uuid: entry["reference"]
            for symbol_uuid, entry in references.get(sheet_path, {}).items()
            if entry.get("reference")
        }

        sheetfile_by_sheet: Dict[str, str] = {}
        child_plan = []
        for child in node.get("children", []):
            if child.get("unresolved"):
                continue
            child_sheet_uuid = child["sheetPath"].rsplit("/", 1)[-1]
            child_name = _synthetic_name(child["sheetPath"])
            sheetfile_by_sheet[child_sheet_uuid] = child_name
            child_rel = os.path.normpath(
                os.path.join(base_dir, (child["file"] or "").replace("\\", "/"))
            )
            child_plan.append((child, child_rel, child_name))

        blobs.append({
            "filename": filename,
            "sheetPath": sheet_path,
            "content": _rewrite(text, reference_by_symbol, sheetfile_by_sheet),
            "isRoot": is_root,
        })

        for child, child_rel, child_name in child_plan:
            emit(child, get_text(child_rel), os.path.dirname(child_rel), child_name, False)

    root_rel = os.path.normpath(root_filename.replace("\\", "/"))
    emit(root_node, root_content, os.path.dirname(root_rel), "root.kicad_sch", True)
    return blobs


def build_flattened_blobs_from_directory(root_schematic_path: str) -> List[dict]:
    """Filesystem convenience wrapper (working-tree mode)."""
    root_dir = os.path.dirname(os.path.abspath(root_schematic_path))
    root_filename = os.path.basename(root_schematic_path)

    def load_sheet(rel_path: str) -> Optional[str]:
        candidate = os.path.normpath(os.path.join(root_dir, rel_path))
        if not candidate.startswith(root_dir):
            return None
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                return handle.read()
        except (FileNotFoundError, IsADirectoryError, OSError):
            return None

    with open(root_schematic_path, "r", encoding="utf-8") as handle:
        root_content = handle.read()

    return build_flattened_blobs(root_filename, root_content, load_sheet)
