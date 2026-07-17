from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import schematic_flatten_service as flat  # noqa: E402


# Root instantiates the SAME subsheet file twice (sheet uuids A and B). The subsheet's
# single symbol resolves to R1 under /R/A and R2 under /R/B.
ROOT = (
    '(kicad_sch (version 20250114) (uuid "R")'
    '  (sheet (uuid "A")'
    '    (property "Sheetname" "First")'
    '    (property "Sheetfile" "sub.kicad_sch")'
    '    (instances (project "p" (path "/R" (page "2")))))'
    '  (sheet (uuid "B")'
    '    (property "Sheetname" "Second")'
    '    (property "Sheetfile" "sub.kicad_sch")'
    '    (instances (project "p" (path "/R" (page "3")))))'
    '  (sheet_instances (path "/" (page "1"))))'
)
SUB = (
    '(kicad_sch (version 20250114) (uuid "SUB")'
    '  (symbol (lib_id "Device:R") (uuid "symX")'
    '    (property "Reference" "R?" (at 0 0 0))'
    '    (instances (project "p"'
    '      (path "/R/A" (reference "R1") (unit 1))'
    '      (path "/R/B" (reference "R2") (unit 1)))))'
    ')'
)


class FlattenMultiInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = {"sub.kicad_sch": SUB}.get
        cls.blobs = flat.build_flattened_blobs("root.kicad_sch", ROOT, loader)
        cls.by_path = {b["sheetPath"]: b for b in cls.blobs}

    def test_one_blob_per_instance(self):
        # root + two subsheet instances
        self.assertEqual(len(self.blobs), 3)
        self.assertEqual(self.blobs[0]["filename"], "root.kicad_sch")
        self.assertTrue(self.blobs[0]["isRoot"])
        self.assertIn("/R/A", self.by_path)
        self.assertIn("/R/B", self.by_path)

    def test_distinct_synthetic_filenames(self):
        name_a = self.by_path["/R/A"]["filename"]
        name_b = self.by_path["/R/B"]["filename"]
        self.assertNotEqual(name_a, name_b)
        self.assertTrue(name_a.endswith(".kicad_sch"))

    def test_reference_rewritten_per_instance(self):
        # The shared symbol's *displayed* Reference property (what the viewer paints)
        # must be R1 in one instance and R2 in the other. (The raw "R1"/"R2" strings
        # also appear in the untouched (instances) metadata block, so assert on the
        # property specifically.)
        self.assertIn('(property "Reference" "R1"', self.by_path["/R/A"]["content"])
        self.assertNotIn('(property "Reference" "R2"', self.by_path["/R/A"]["content"])
        self.assertIn('(property "Reference" "R2"', self.by_path["/R/B"]["content"])
        self.assertNotIn('(property "Reference" "R1"', self.by_path["/R/B"]["content"])

    def test_root_sheetfiles_repointed_to_synthetic_names(self):
        root_content = self.blobs[0]["content"]
        name_a = self.by_path["/R/A"]["filename"]
        name_b = self.by_path["/R/B"]["filename"]
        self.assertIn(f'(property "Sheetfile" "{name_a}"', root_content)
        self.assertIn(f'(property "Sheetfile" "{name_b}"', root_content)
        # The original shared filename should no longer be referenced.
        self.assertNotIn('"sub.kicad_sch"', root_content)

    def test_lib_symbols_not_rewritten(self):
        # A lib_symbols reference prefix must survive untouched.
        root_with_lib = (
            '(kicad_sch (version 20250114) (uuid "R")'
            '  (lib_symbols (symbol "Device:R" (property "Reference" "R" (at 0 0 0))))'
            '  (symbol (lib_id "Device:R") (uuid "symZ")'
            '    (property "Reference" "R?" (at 0 0 0))'
            '    (instances (project "p" (path "/R" (reference "R9") (unit 1)))))'
            '  (sheet_instances (path "/" (page "1"))))'
        )
        blobs = flat.build_flattened_blobs("root.kicad_sch", root_with_lib, {}.get)
        content = blobs[0]["content"]
        # lib_symbols prefix untouched; the placed symbol annotated to R9.
        self.assertIn('(symbol "Device:R" (property "Reference" "R" ', content)
        self.assertIn('(property "Reference" "R9"', content)


if __name__ == "__main__":
    unittest.main()
