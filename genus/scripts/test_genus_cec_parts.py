"""Partition proofs retain named inputs and detect mismatches beyond group 0."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from genus_cec_parts import aig_interface, positive, prove_partitioned_helper


class PartitionInterfaceTests(unittest.TestCase):
    def test_symbols_must_cover_all_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.aig'
            path.write_bytes(b'aig 1 1 0 1 0\n2\ni0 a\no0 y\nc\ncomment\n')
            self.assertEqual(aig_interface(path), (['a'], ['y']))
            path.write_bytes(b'aig 1 1 0 1 0\n2\ni0 a\nc\n')
            with self.assertRaises(ValueError):
                aig_interface(path)

    def test_only_explicit_positive_claims_are_accepted(self):
        self.assertTrue(positive('Networks are equivalent.'))
        self.assertTrue(positive('Networks are equivalent after structural hashing.'))
        for message in ('Networks are NOT EQUIVALENT', 'Networks are undecided', 'Timed out', ''):
            self.assertFalse(positive(message))


@unittest.skipUnless(shutil.which(os.environ.get('YOSYS', 'yosys')), 'Yosys required')
class PartitionEngineTests(unittest.TestCase):
    def test_later_output_group_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'empty.lib'; library.write_text('library(empty) {}\n')
            gold = root / 'gold.v'
            gold.write_text('module test(input [129:0] a, output [129:0] y);assign y=a;endmodule\n')
            for label, expression, equivalent in [('same', 'a', True), ('last_bit', "a ^ (130'b1 << 129)", False)]:
                folder = root / label; folder.mkdir()
                gate = folder / 'gate.v'
                gate.write_text(f'module test(input [129:0] a, output [129:0] y);assign y={expression};endmodule\n')
                args = ('test', gold, gate, library, [os.environ.get('YOSYS', 'yosys')], folder)
                if equivalent:
                    prove_partitioned_helper(*args)
                else:
                    with self.assertRaises(RuntimeError):
                        prove_partitioned_helper(*args)


if __name__ == '__main__':
    unittest.main()
