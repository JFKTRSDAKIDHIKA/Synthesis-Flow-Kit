"""CEC must reject counterexamples, unknown results and interface mismatch."""
import tempfile
from pathlib import Path
import unittest
from genus_cec import require_equivalent,validate_ports

class CecTests(unittest.TestCase):
    def test_requires_explicit_proof_even_when_process_returns_zero(self):
        require_equivalent('Networks are equivalent.  Time = 1 sec')
        for text in ('Networks are NOT EQUIVALENT','Timeout','Error: cannot read file',''):
            with self.assertRaises(RuntimeError):require_equivalent(text)
    def test_all_port_bits_must_match(self):
        with tempfile.TemporaryDirectory() as folder:
            gold=Path(folder)/'gold.map';gate=Path(folder)/'gate.map'
            gold.write_text('input 0 0 a\noutput 0 0 y\n');gate.write_text(gold.read_text())
            validate_ports(gold,gate,1,1)
            with self.assertRaises(ValueError):validate_ports(gold,gate,2,1)
            gate.write_text('input 0 0 b\noutput 0 0 y\n')
            with self.assertRaises(ValueError):validate_ports(gold,gate,1,1)

if __name__=='__main__':unittest.main()
