"""Dependency-free helpers for importing uploaded project archives.

Deliberately kept free of heavy imports (git / pydantic / fastapi) so the
security-critical extraction logic can be unit-tested in isolation.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

# Safety cap for uploaded archives, applied to both the raw upload size and the
# declared uncompressed size (zip-bomb guard).
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def sanitize_project_name(raw: str) -> str:
    """Derive a safe on-disk/display name from a filename or user-supplied name."""
    stem = Path(raw or "").name  # strip any directory component
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", stem).strip(" -.")
    return cleaned or "uploaded-project"


def safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip while rejecting absolute paths and path traversal (zip-slip),
    and bounding the total uncompressed size."""
    dest_resolved = dest.resolve()
    total = 0
    for member in zf.infolist():
        total += member.file_size
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("Archive uncompressed size exceeds the allowed limit")
        target = (dest / member.filename).resolve()
        if target != dest_resolved and dest_resolved not in target.parents:
            raise ValueError(f"Unsafe path in archive: {member.filename}")
    zf.extractall(dest)


def find_content_root(extracted: Path) -> Path:
    """If the archive wraps everything in a single top-level folder, descend into it
    (matches the GitHub 'Download ZIP' layout)."""
    entries = [e for e in extracted.iterdir() if e.name != "__MACOSX"]
    dirs = [e for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]
    if len(dirs) == 1 and not files:
        return dirs[0]
    return extracted
