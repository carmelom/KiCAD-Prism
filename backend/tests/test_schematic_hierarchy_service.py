from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import schematic_hierarchy_service as svc  # noqa: E402


# Synthetic root UUID shared by the end-to-end and unit tests below. The suite
# builds its own schematics on disk -- it does not depend on any external
# example project.
ROOT_UUID = "00000000-0000-4000-8000-000000000001"


class SexprParserTests(unittest.TestCase):
    def test_parses_nested_and_strings(self):
        node = svc.parse_sexpr('(kicad_sch (version 20250114) (uuid "abc") (title "a \\"b\\""))')
        self.assertEqual(svc._head(node), "kicad_sch")
        self.assertEqual(svc._value(node, "version"), "20250114")
        self.assertEqual(svc._value(node, "uuid"), "abc")
        self.assertEqual(svc._value(node, "title"), 'a "b"')

    def test_empty_returns_none(self):
        self.assertIsNone(svc.parse_sexpr("   \n  "))


class HierarchySyntheticTests(unittest.TestCase):
    """End-to-end resolution over a synthetic multi-subsheet project built on
    disk. Exercises root metadata, multi-subsheet resolution, per-instance page
    numbers, sheet-path composition, and per-instance reference designators --
    without depending on any external example project."""

    AMP_UUID = "00000000-0000-4000-8000-0000000000a1"

    ROOT = (
        f'(kicad_sch (version 20250114) (uuid "{ROOT_UUID}")'
        f'  (symbol (uuid "symJ") (lib_id "Connector:Conn_01x02")'
        f'    (instances (project "p" (path "/{ROOT_UUID}"'
        f'      (reference "J1") (unit 1)))))'
        f'  (sheet (uuid "{AMP_UUID}")'
        f'    (property "Sheetname" "Amplifier")'
        f'    (property "Sheetfile" "amp.kicad_sch")'
        f'    (instances (project "p" (path "/{ROOT_UUID}" (page "2")))))'
        f'  (sheet (uuid "pwr")'
        f'    (property "Sheetname" "Power")'
        f'    (property "Sheetfile" "power.kicad_sch")'
        f'    (instances (project "p" (path "/{ROOT_UUID}" (page "3")))))'
        f'  (sheet (uuid "mech")'
        f'    (property "Sheetname" "Mechanical")'
        f'    (property "Sheetfile" "mech.kicad_sch")'
        f'    (instances (project "p" (path "/{ROOT_UUID}" (page "4")))))'
        f'  (sheet_instances (path "/" (page "1"))))'
    )
    AMP = (
        f'(kicad_sch (version 20250114) (uuid "AMPSCR")'
        f'  (symbol (uuid "symR1") (lib_id "Device:R")'
        f'    (instances (project "p" (path "/{ROOT_UUID}/{AMP_UUID}"'
        f'      (reference "R201") (unit 1)))))'
        f'  (symbol (uuid "symR2") (lib_id "Device:R")'
        f'    (instances (project "p" (path "/{ROOT_UUID}/{AMP_UUID}"'
        f'      (reference "R211") (unit 1))))))'
    )
    POWER = '(kicad_sch (version 20250114) (uuid "PWRSCR"))'
    MECH = '(kicad_sch (version 20250114) (uuid "MECHSCR"))'

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root_dir = Path(cls._tmp.name)
        (root_dir / "root.kicad_sch").write_text(cls.ROOT)
        (root_dir / "amp.kicad_sch").write_text(cls.AMP)
        (root_dir / "power.kicad_sch").write_text(cls.POWER)
        (root_dir / "mech.kicad_sch").write_text(cls.MECH)
        cls.result = svc.resolve_from_directory(str(root_dir / "root.kicad_sch"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_root_metadata(self):
        self.assertEqual(self.result["rootUuid"], ROOT_UUID)
        self.assertEqual(self.result["version"], "20250114")

    def test_root_node(self):
        root = self.result["root"]
        self.assertEqual(root["name"], "root")
        self.assertEqual(root["sheetPath"], f"/{ROOT_UUID}")
        self.assertEqual(root["page"], "1")

    def test_three_subsheets_resolved(self):
        children = self.result["root"]["children"]
        names = sorted(c["name"] for c in children)
        self.assertEqual(names, ["Amplifier", "Mechanical", "Power"])
        # All children resolved to real files (no unresolved flag).
        self.assertFalse(any(c.get("unresolved") for c in children))

    def test_subsheet_page_numbers(self):
        pages = {c["name"]: c["page"] for c in self.result["root"]["children"]}
        self.assertEqual(pages["Amplifier"], "2")
        self.assertEqual(pages["Power"], "3")
        self.assertEqual(pages["Mechanical"], "4")

    def test_subsheet_instance_path_includes_root_and_sheet_uuid(self):
        amp = next(c for c in self.result["root"]["children"]
                   if c["name"] == "Amplifier")
        self.assertEqual(amp["sheetPath"], f"/{ROOT_UUID}/{self.AMP_UUID}")

    def test_root_references_present(self):
        refs = self.result["references"][f"/{ROOT_UUID}"]
        values = {v["reference"] for v in refs.values()}
        # Root-level designator lives on the root sheet path.
        self.assertIn("J1", values)

    def test_subsheet_references_resolved_per_instance(self):
        amp_path = f"/{ROOT_UUID}/{self.AMP_UUID}"
        refs = self.result["references"][amp_path]
        values = {v["reference"] for v in refs.values()}
        # R201/R211 live in the Amplifier instance, not the root.
        self.assertIn("R201", values)
        self.assertIn("R211", values)


class ResolverUnitTests(unittest.TestCase):
    def test_missing_child_file_marked_unresolved(self):
        root_content = (
            f'(kicad_sch (version 20250114) (uuid "{ROOT_UUID}")'
            f'  (sheet_instances (path "/" (page "1")))'
            f'  (sheet (uuid "s1")'
            f'    (property "Sheetname" "Missing")'
            f'    (property "Sheetfile" "missing.kicad_sch")'
            f'    (instances (project "p" (path "/{ROOT_UUID}" (page "2"))))))'
        )
        result = svc.resolve_from_content(
            "root.kicad_sch", root_content, lambda name: None
        )
        child = result["root"]["children"][0]
        self.assertTrue(child.get("unresolved"))
        self.assertEqual(child["page"], "2")

    def test_symbol_uuid_appended_path_form_matches(self):
        # Some format versions append the symbol UUID to the instance path.
        root_content = (
            f'(kicad_sch (version 20250114) (uuid "{ROOT_UUID}")'
            f'  (symbol (uuid "symA") (lib_id "R")'
            f'    (instances (project "p" (path "/{ROOT_UUID}/symA"'
            f'      (reference "R99") (unit 1))))))'
        )
        result = svc.resolve_from_content(
            "root.kicad_sch", root_content, lambda name: None
        )
        refs = result["references"][f"/{ROOT_UUID}"]
        self.assertEqual(refs["symA"]["reference"], "R99")


class NestedSubdirHierarchyTests(unittest.TestCase):
    """A subsheet living in a subdirectory references its own children relative to
    *its* location, not the root's. Regression test for deep hierarchies."""

    ROOT = (
        '(kicad_sch (version 20250114) (uuid "R")'
        '  (sheet (uuid "M")'
        '    (property "Sheetname" "Mid")'
        '    (property "Sheetfile" "Subsheets/mid.kicad_sch")'
        '    (instances (project "p" (path "/R" (page "2")))))'
        '  (sheet_instances (path "/" (page "1"))))'
    )
    MID = (
        '(kicad_sch (version 20250114) (uuid "MID")'
        '  (sheet (uuid "L")'
        '    (property "Sheetname" "Leaf")'
        '    (property "Sheetfile" "leaf.kicad_sch")'  # relative to Subsheets/
        '    (instances (project "p" (path "/R/M" (page "3"))))))'
    )
    LEAF = (
        '(kicad_sch (version 20250114) (uuid "LEAF")'
        '  (symbol (uuid "symL") (lib_id "Device:R")'
        '    (instances (project "p" (path "/R/M/L" (reference "R500") (unit 1))))))'
    )

    def _build(self, root_dir: Path) -> Path:
        (root_dir / "Subsheets").mkdir()
        (root_dir / "root.kicad_sch").write_text(self.ROOT)
        (root_dir / "Subsheets" / "mid.kicad_sch").write_text(self.MID)
        (root_dir / "Subsheets" / "leaf.kicad_sch").write_text(self.LEAF)
        return root_dir / "root.kicad_sch"

    def test_resolves_to_full_depth_across_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_sch = self._build(Path(tmp))
            result = svc.resolve_from_directory(str(root_sch))

        mid = result["root"]["children"][0]
        self.assertEqual(mid["name"], "Mid")
        self.assertFalse(mid.get("unresolved"))
        self.assertEqual(mid["page"], "2")
        self.assertEqual(mid["sheetPath"], "/R/M")

        # Grandchild must resolve (previously stopped here).
        self.assertEqual(len(mid["children"]), 1)
        leaf = mid["children"][0]
        self.assertEqual(leaf["name"], "Leaf")
        self.assertFalse(leaf.get("unresolved"))
        self.assertEqual(leaf["page"], "3")
        self.assertEqual(leaf["sheetPath"], "/R/M/L")

        # Per-instance reference on the deepest sheet.
        self.assertEqual(result["references"]["/R/M/L"]["symL"]["reference"], "R500")


if __name__ == "__main__":
    unittest.main()
