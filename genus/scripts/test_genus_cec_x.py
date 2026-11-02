"""Exercise actual x propagation and ABC against legal and broken mappings."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from genus_cec_x import prove_defined_helper


@unittest.skipUnless(shutil.which(os.environ.get('YOSYS', 'yosys')), 'Yosys required')
class DefinedCecTests(unittest.TestCase):
    def test_defined_outputs_and_undefined_gate_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gold = root / 'gold.v'
            gold.write_text("module test(input a,b, output y);assign y=a?b:1'bx;endmodule\n")
            library = root / 'empty.lib'
            library.write_text('library(empty) {}\n')
            for label, expression, equivalent in [('legal', 'b', True),
                                                  ('mismatch', '~b', False),
                                                  ('undefined', "1'bx", False)]:
                with self.subTest(label=label):
                    folder = root / label
                    folder.mkdir()
                    gate = folder / 'gate.v'
                    gate.write_text(f'module test(input a,b, output y);assign y={expression};endmodule\n')
                    args = ('test', gold, gate, library, [os.environ.get('YOSYS', 'yosys')], folder)
                    if equivalent:
                        prove_defined_helper(*args)
                    else:
                        with self.assertRaises(RuntimeError):
                            prove_defined_helper(*args)


if __name__ == '__main__':
    unittest.main()
