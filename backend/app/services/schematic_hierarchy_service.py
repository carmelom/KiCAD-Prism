"""Resolve the hierarchical structure of a KiCad schematic.

KiCad schematics are hierarchical: a root ``.kicad_sch`` file (a *screen*) can
place child *sheets*, each of which references another ``.kicad_sch`` file. The
same file can be placed more than once, producing distinct *instances*. Because
a file/screen is shared across its instances, per-instance data (references,
page numbers) cannot live on the screen -- it is keyed by the **sheet path**,
the ordered chain of sheet UUIDs from the root down to a given instance.

This module reconstructs that model from the file contents alone (no KiCad
runtime), mirroring the algorithm in the KiCad source
(``eeschema/sch_sheet_path.cpp`` / ``eeschema/sch_symbol.cpp``). The core
resolver is pure: it takes the root file content plus a ``load_sheet`` callback,
so it works identically for a working-tree checkout and for a git commit.

Join keys (validated against real KiCad projects, file format v20250114):

* A sheet-instance path is ``/<rootUuid>/<sheetUuid>/...`` (root-relative,
  including the root UUID as the first element).
* A symbol's ``(instances ... (path <p> (reference ..) (unit ..)))`` is keyed by
  the sheet path of the sheet that contains it. Some format versions append the
  symbol's own UUID to ``<p>``; both forms are matched.
* A child sheet's page number lives in its own ``(instances ... (path <parent>
  (page ..)))`` keyed by the *parent* sheet path. The root's page lives in the
  root screen's top-level ``(sheet_instances (path "/" (page ..)))``.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

# A callback that returns the text content of a sheet file (referenced by the
# ``Sheetfile`` property, relative to the root schematic) or ``None`` if missing.
SheetLoader = Callable[[str], Optional[str]]

# Guard against pathological / malformed hierarchies.
_MAX_DEPTH = 64


# --------------------------------------------------------------------------- #
# S-expression parser
# --------------------------------------------------------------------------- #
# A small recursive-descent parser is used instead of regex: the per-instance
# data lives in deeply nested nodes -- (instances (project (path (reference))))
# -- which regex/brace-counting handles poorly. Parsed nodes are plain lists;
# the head (index 0) is the node's bare symbol, e.g. "sheet", "symbol", "path".
_WS = " \t\r\n"
_ATOM_END = " \t\r\n()\""


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)

    def parse(self) -> Optional[list]:
        # Seek the first top-level '(' (skips any leading BOM/comments/whitespace).
        while self.i < self.n and self.s[self.i] != "(":
            self.i += 1
        if self.i >= self.n:
            return None
        return self._node()

    def _node(self) -> list:
        self.i += 1  # consume '('
        items: list = []
        while self.i < self.n:
            c = self.s[self.i]
            if c in _WS:
                self.i += 1
            elif c == ")":
                self.i += 1
                return items
            elif c == "(":
                items.append(self._node())
            elif c == '"':
                items.append(self._string())
            else:
                items.append(self._atom())
        return items  # unterminated; return what we have

    def _string(self) -> str:
        self.i += 1  # consume opening quote
        out: List[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < self.n:
                out.append(self.s[self.i + 1])
                self.i += 2
            elif c == '"':
                self.i += 1
                break
            else:
                out.append(c)
                self.i += 1
        return "".join(out)

    def _atom(self) -> str:
        start = self.i
        while self.i < self.n and self.s[self.i] not in _ATOM_END:
            self.i += 1
        return self.s[start:self.i]


def parse_sexpr(text: str) -> Optional[list]:
    """Parse KiCad s-expression text into a nested list, or ``None`` if empty."""
    return _Parser(text).parse()


# --------------------------------------------------------------------------- #
# Node navigation helpers
# --------------------------------------------------------------------------- #
def _head(node) -> Optional[str]:
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def _children(node, head: str) -> List[list]:
    if not isinstance(node, list):
        return []
    return [c for c in node if isinstance(c, list) and _head(c) == head]


def _child(node, head: str) -> Optional[list]:
    for c in _children(node, head):
        return c
    return None


def _value(node, head: str, index: int = 1) -> Optional[str]:
    """Return the ``index``-th element of the first ``(head ...)`` child."""
    c = _child(node, head)
    if c is not None and len(c) > index and isinstance(c[index], str):
        return c[index]
    return None


def _property(node, key: str) -> Optional[str]:
    """Return the value of a ``(property "key" "value" ...)`` child."""
    for c in _children(node, "property"):
        if len(c) >= 3 and c[1] == key and isinstance(c[2], str):
            return c[2]
    return None


def _instance_path_node(node, path_str: str, symbol_uuid: Optional[str] = None) -> Optional[list]:
    """Find the ``(path ...)`` node under ``(instances (project ...))`` whose path
    string matches ``path_str`` (or ``path_str/symbol_uuid`` for versions that
    append the symbol UUID)."""
    inst = _child(node, "instances")
    if inst is None:
        return None
    candidates = {path_str}
    if symbol_uuid:
        candidates.add(f"{path_str}/{symbol_uuid}")
    for project in _children(inst, "project"):
        for path in _children(project, "path"):
            if len(path) > 1 and isinstance(path[1], str) and path[1] in candidates:
                return path
    return None


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def resolve_from_content(
    root_filename: str,
    root_content: str,
    load_sheet: SheetLoader,
) -> dict:
    """Resolve the full hierarchy from the root file content + a sheet loader.

    Returns::

        {
          "rootUuid": str | None,
          "version": str | None,
          "root": <node>,            # tree of instance nodes (see below)
          "references": {            # per-instance symbol references
            "<sheetPath>": { "<symbolUuid>": {"reference": str, "unit": str|None} }
          },
        }

    where each tree node is::

        {"sheetPath", "displayPath", "name", "file", "page", "children"[, "unresolved"]}
    """
    root = parse_sexpr(root_content)
    if root is None:
        raise ValueError("Root schematic is empty or unparseable")

    root_uuid = _value(root, "uuid")
    version = _value(root, "version")
    root_name = os.path.splitext(os.path.basename(root_filename))[0]

    references: Dict[str, Dict[str, dict]] = {}
    # Cache parsed screens by their root-relative path: a shared screen is parsed and
    # loaded once even when instantiated multiple times.
    screen_cache: Dict[str, Optional[list]] = {}

    def get_screen(rel_path: str) -> Optional[list]:
        if rel_path not in screen_cache:
            content = load_sheet(rel_path)
            screen_cache[rel_path] = parse_sexpr(content) if content else None
        return screen_cache[rel_path]

    def collect_references(screen: list, sheet_path: str) -> None:
        refmap: Dict[str, dict] = {}
        for symbol in _children(screen, "symbol"):
            symbol_uuid = _value(symbol, "uuid")
            if not symbol_uuid:
                continue
            path_node = _instance_path_node(symbol, sheet_path, symbol_uuid)
            if path_node is None:
                continue
            reference = _value(path_node, "reference")
            if reference is None:
                continue
            refmap[symbol_uuid] = {"reference": reference, "unit": _value(path_node, "unit")}
        references[sheet_path] = refmap

    def root_page() -> Optional[str]:
        sheet_instances = _child(root, "sheet_instances")
        if sheet_instances is None:
            return None
        for path in _children(sheet_instances, "path"):
            if len(path) > 1 and path[1] == "/":
                return _value(path, "page")
        return None

    def walk(
        screen: list,
        uuid_chain: List[str],
        name: Optional[str],
        filename: str,
        page: Optional[str],
        display_chain: List[str],
        base_dir: str,
        visited: frozenset,
        depth: int,
    ) -> dict:
        sheet_path = "/" + "/".join(uuid_chain)
        display_path = "/" + "/".join(display_chain)
        collect_references(screen, sheet_path)

        node = {
            "sheetPath": sheet_path,
            "displayPath": display_path,
            "name": name,
            "file": filename,
            "page": page,
            "children": [],
        }

        if depth >= _MAX_DEPTH:
            return node

        for sheet in _children(screen, "sheet"):
            child_uuid = _value(sheet, "uuid")
            child_name = _property(sheet, "Sheetname")
            child_file = _property(sheet, "Sheetfile")
            # The child's page number is keyed by the parent (current) sheet path.
            page_node = _instance_path_node(sheet, sheet_path)
            child_page = _value(page_node, "page") if page_node is not None else None

            child_chain = uuid_chain + [child_uuid] if child_uuid else uuid_chain
            child_display = display_chain + [child_name or child_file or "?"]

            # KiCad stores Sheetfile relative to the directory of the *parent* sheet,
            # so resolve each child against this screen's directory (root-relative).
            child_rel = (
                os.path.normpath(os.path.join(base_dir, child_file.replace("\\", "/")))
                if child_file else None
            )
            child_screen = get_screen(child_rel) if child_rel else None

            if child_screen is None or child_rel in visited:
                # Missing file, or a cyclic reference (a sheet including itself).
                node["children"].append({
                    "sheetPath": "/" + "/".join(child_chain),
                    "displayPath": "/" + "/".join(child_display),
                    "name": child_name,
                    "file": child_file,
                    "page": child_page,
                    "children": [],
                    "unresolved": True,
                })
                continue

            node["children"].append(walk(
                child_screen,
                child_chain,
                child_name,
                child_file,
                child_page,
                child_display,
                os.path.dirname(child_rel),
                visited | {child_rel},
                depth + 1,
            ))
        return node

    root_rel = os.path.normpath(root_filename.replace("\\", "/"))
    tree = walk(
        root,
        [root_uuid] if root_uuid else [],
        root_name,
        root_filename,
        root_page(),
        [root_name],
        os.path.dirname(root_rel),
        frozenset({root_rel}),
        0,
    )

    return {
        "rootUuid": root_uuid,
        "version": version,
        "root": tree,
        "references": references,
    }


def resolve_from_directory(root_schematic_path: str) -> dict:
    """Resolve a hierarchy from a root ``.kicad_sch`` file on disk.

    Child sheet files (``Sheetfile`` properties) are resolved relative to the
    root schematic's directory.
    """
    root_dir = os.path.dirname(os.path.abspath(root_schematic_path))
    root_filename = os.path.basename(root_schematic_path)

    def load_sheet(filename: str) -> Optional[str]:
        # Sheetfile is a repo-relative path; normalize and keep it inside root_dir.
        candidate = os.path.normpath(os.path.join(root_dir, filename))
        if not candidate.startswith(root_dir):
            return None
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                return handle.read()
        except (FileNotFoundError, IsADirectoryError, OSError):
            return None

    with open(root_schematic_path, "r", encoding="utf-8") as handle:
        root_content = handle.read()

    return resolve_from_content(root_filename, root_content, load_sheet)
