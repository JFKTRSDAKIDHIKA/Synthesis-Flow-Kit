"""Reject unsupported matrix factoring and state hidden outside storage."""
import unittest
from pathlib import Path
from genus_mt import PORTS, factor_matrix, interface

class MatrixFactorTests(unittest.TestCase):
    def test_geometry_changes_fail_before_running_yosys(self):
        ports = {p: dict(direction=d, bits=list(range(n))) for p, (d, n) in PORTS.items()}
        ports['readData']['bits'].pop()
        data = {'modules': {'zvt_mt': {'ports': ports, 'cells': {}}}}
        with self.assertRaisesRegex(ValueError, 'unsupported matrix geometry'):
            factor_matrix(data, ['must-not-run'], Path('/tmp/matrix-test'))

    def test_duplicate_or_absent_wrappers_are_rejected(self):
        for modules in ({}, {'zvt_mt': {}, 'zvt_mt$other': {}}):
            with self.assertRaisesRegex(ValueError, 'one VME wrapper'):
                factor_matrix({'modules': modules}, ['must-not-run'], Path('/tmp/matrix-test'))

    def test_storage_cut_rejects_extra_state(self):
        module = {'cells': {'memory': {'type': 'MTStorage', 'connections': {}},
                            'hidden': {'type': '$sdffe'}}, 'ports': {}}
        with self.assertRaisesRegex(ValueError, 'not combinational'):
            interface(module, 'gold')

if __name__ == '__main__':
    unittest.main()
