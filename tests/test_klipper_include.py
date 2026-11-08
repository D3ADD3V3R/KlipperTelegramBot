import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_klipper_include import update, MARKER, INCLUDE


class IncludeTests(unittest.TestCase):
    def check(self, content):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'printer.cfg'
            cfg.write_bytes(content)
            update(cfg, 'ensure')
            result = cfg.read_bytes()
            self.assertEqual(result.count(INCLUDE), 1)
            self.assertLess(result.index(INCLUDE), result.index(MARKER))
            self.assertIn(MARKER + b'\n#*# z_offset = 1.45\n', result)
            update(cfg, 'ensure')
            self.assertEqual(cfg.read_bytes(), result)
            update(cfg, 'remove')
            self.assertNotIn(INCLUDE, cfg.read_bytes())
            self.assertIn(MARKER + b'\n#*# z_offset = 1.45\n', cfg.read_bytes())

    def test_misplaced_include(self):
        self.check(b'[printer]\nkinematics: cartesian\n\n' + MARKER + b'\n#*# z_offset = 1.45\n\n' + INCLUDE + b'\n')

    def test_existing_include(self):
        self.check(b'[printer]\n' + INCLUDE + b'\n' + MARKER + b'\n#*# z_offset = 1.45\n')

    def test_duplicate_include(self):
        self.check(INCLUDE + b'\n' + INCLUDE + b'\n' + MARKER + b'\n#*# z_offset = 1.45\n')

    def test_without_save_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'printer.cfg'
            cfg.write_bytes(b'[printer]\nkinematics: cartesian\n')
            update(cfg,'ensure')
            self.assertEqual(cfg.read_bytes().count(INCLUDE),1)
            update(cfg,'ensure')
            self.assertEqual(cfg.read_bytes().count(INCLUDE),1)

if __name__ == '__main__':
    unittest.main()
