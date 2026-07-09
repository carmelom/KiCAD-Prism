from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import schematic_pin_fix_service as pf  # noqa: E402


# A lib symbol whose two placed pins both carry an empty number ("") -- the exact
# collision that collapses in the viewer's pin_by_number map. A matching placed
# instance references it and also carries two empty-numbered pin refs.
COLLIDE = (
    '(kicad_sch (version 20250114) (uuid "R")'
    '  (lib_symbols'
    '    (symbol "Blk:Model336" (pin_names (offset 0))'
    '      (symbol "Model336_1_1"'
    '        (pin input line (at 0 0 0) (length 2.54)'
    '          (name "A" (effects (font (size 1 1))))'
    '          (number "" (effects (font (size 1 1)))))'
    '        (pin input line (at 0 5 0) (length 2.54)'
    '          (name "B" (effects (font (size 1 1))))'
    '          (number "" (effects (font (size 1 1))))))))'
    '  (symbol (lib_id "Blk:Model336") (uuid "u1")'
    '    (property "Reference" "U1" (at 0 0 0))'
    '    (pin "" (uuid "p1"))'
    '    (pin "" (uuid "p2")))'
    '  (sheet_instances (path "/" (page "1"))))'
)

# Same shape but pins carry genuine, distinct numbers -- must be left untouched.
DISTINCT = (
    '(kicad_sch (version 20250114) (uuid "R")'
    '  (lib_symbols'
    '    (symbol "Device:R"'
    '      (symbol "R_1_1"'
    '        (pin passive line (at 0 0 0) (length 2.54)'
    '          (name "~" (effects (font (size 1 1))))'
    '          (number "1" (effects (font (size 1 1)))))'
    '        (pin passive line (at 0 5 0) (length 2.54)'
    '          (name "~" (effects (font (size 1 1))))'
    '          (number "2" (effects (font (size 1 1))))))))'
    '  (symbol (lib_id "Device:R") (uuid "u1")'
    '    (property "Reference" "R1" (at 0 0 0))'
    '    (pin "1" (uuid "p1"))'
    '    (pin "2" (uuid "p2")))'
    '  (sheet_instances (path "/" (page "1"))))'
)


class NoOpTests(unittest.TestCase):
    def test_no_lib_symbols_is_noop(self):
        text = '(kicad_sch (version 20250114) (uuid "R") (sheet_instances (path "/" (page "1"))))'
        self.assertEqual(pf.fix_colliding_pin_numbers(text), text)

    def test_distinct_numbers_untouched(self):
        out = pf.fix_colliding_pin_numbers(DISTINCT)
        self.assertEqual(out, DISTINCT)
        # No visibility node injected on a symbol that did not collide.
        self.assertNotIn("(pin_numbers (hide yes))", out)


class CollisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = pf.fix_colliding_pin_numbers(COLLIDE)

    def test_input_actually_collides(self):
        # Guard: the fixture must exercise the collision path.
        self.assertEqual(COLLIDE.count('(number ""'), 2)

    def test_lib_pins_renumbered_uniquely(self):
        self.assertNotIn('(number ""', self.out)
        self.assertIn('(number "1"', self.out)
        self.assertIn('(number "2"', self.out)

    def test_synthetic_numbers_hidden(self):
        # Exactly one visibility node, injected on the colliding symbol.
        self.assertEqual(self.out.count("(pin_numbers (hide yes))"), 1)

    def test_placed_instance_pins_renumbered(self):
        self.assertNotIn('(pin "" (uuid', self.out)
        self.assertIn('(pin "1" (uuid "p1")', self.out)
        self.assertIn('(pin "2" (uuid "p2")', self.out)

    def test_unrelated_content_preserved(self):
        # Names, references and the sheet_instances page survive untouched.
        self.assertIn('(name "A"', self.out)
        self.assertIn('(name "B"', self.out)
        self.assertIn('(property "Reference" "U1"', self.out)
        self.assertIn('(sheet_instances (path "/" (page "1")))', self.out)


class ExistingPinNumbersNodeTests(unittest.TestCase):
    def test_existing_visible_node_forced_hidden(self):
        # A colliding symbol that already declares (pin_numbers ...) must have that
        # node rewritten to hide yes, not get a second one injected.
        text = (
            '(kicad_sch (version 20250114) (uuid "R")'
            '  (lib_symbols'
            '    (symbol "Blk:Sym" (pin_numbers (hide no))'
            '      (symbol "Sym_1_1"'
            '        (pin input line (name "A") (number ""))'
            '        (pin input line (name "B") (number "")))))'
            '  (symbol (lib_id "Blk:Sym") (uuid "u1")'
            '    (pin "" (uuid "p1")) (pin "" (uuid "p2")))'
            '  (sheet_instances (path "/" (page "1"))))'
        )
        out = pf.fix_colliding_pin_numbers(text)
        self.assertEqual(out.count("(pin_numbers"), 1)
        self.assertIn("(pin_numbers (hide yes))", out)
        self.assertNotIn("(pin_numbers (hide no))", out)


if __name__ == "__main__":
    unittest.main()
