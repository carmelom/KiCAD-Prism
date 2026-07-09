from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import archive_utils  # noqa: E402


class SanitizeNameTests(unittest.TestCase):
    def test_strips_zip_extension_and_path(self):
        self.assertEqual(archive_utils.sanitize_project_name("/tmp/My Board.zip"), "My Board")

    def test_replaces_unsafe_characters(self):
        self.assertEqual(archive_utils.sanitize_project_name("a/b:c*?.zip"), "b-c")

    def test_fallback_when_empty(self):
        self.assertEqual(archive_utils.sanitize_project_name(""), "uploaded-project")
        self.assertEqual(archive_utils.sanitize_project_name("...zip"), "uploaded-project")


class SafeExtractTests(unittest.TestCase):
    def _zip_bytes(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for name, data in entries:
                zf.writestr(name, data)
        buffer.seek(0)
        return buffer

    def test_extracts_normal_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            dest.mkdir()
            with zipfile.ZipFile(self._zip_bytes([
                ("proj/board.kicad_pro", "x"),
                ("proj/board.kicad_sch", "y"),
            ])) as zf:
                archive_utils.safe_extract_zip(zf, dest)
            self.assertTrue((dest / "proj" / "board.kicad_pro").exists())

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            dest.mkdir()
            # Craft a member with a traversal path (zip-slip).
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zf:
                info = zipfile.ZipInfo("../evil.txt")
                zf.writestr(info, "pwned")
            buffer.seek(0)
            with zipfile.ZipFile(buffer) as zf:
                with self.assertRaises(ValueError):
                    archive_utils.safe_extract_zip(zf, dest)
            self.assertFalse((Path(tmp) / "evil.txt").exists())

    def test_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            dest.mkdir()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zf:
                zf.writestr(zipfile.ZipInfo("/etc/evil.txt"), "pwned")
            buffer.seek(0)
            with zipfile.ZipFile(buffer) as zf:
                with self.assertRaises(ValueError):
                    archive_utils.safe_extract_zip(zf, dest)


class FindContentRootTests(unittest.TestCase):
    def test_descends_single_top_level_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wrapper").mkdir()
            (root / "wrapper" / "board.kicad_pro").write_text("x")
            self.assertEqual(archive_utils.find_content_root(root), root / "wrapper")

    def test_ignores_macosx_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wrapper").mkdir()
            (root / "__MACOSX").mkdir()
            self.assertEqual(archive_utils.find_content_root(root), root / "wrapper")

    def test_returns_self_when_multiple_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "board.kicad_pro").write_text("x")
            (root / "sub").mkdir()
            self.assertEqual(archive_utils.find_content_root(root), root)


if __name__ == "__main__":
    unittest.main()
