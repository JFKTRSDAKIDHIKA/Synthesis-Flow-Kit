"""Exercise the real mapped-netlist validator with Genus-style partial helpers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import run_genus

@unittest.skipUnless(os.environ.get('YOSYS'), 'requires workspace Yosys')
class NormalizationTests(unittest.TestCase):
    def normalize(self, raw, preserve=False):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            for name in ('logs','netlist','reports','lib'): (out/name).mkdir()
            (out/'netlist/genus-raw.v').write_text(raw)
            (out/'dummy.lib').write_text('library(dummy) {}\n')
            (out/'spec.json').write_text('{"macros":[]}')
            argv=['run_genus','--genus','fake-genus','--yosys',os.environ['YOSYS'],
                  '--out-dir',str(out),'--rtl',str(out/'input.v'),'--blackboxes',str(out/'blackboxes.v'),
                  '--std-lib',str(out/'dummy.lib'),'--sram-lib-dir',str(out/'lib'),
                  '--spec',str(out/'spec.json'),'--netlist',str(out/'mapped.v'),
                  '--script',str(out/'unused.tcl'),'--top','Top','--unit-mode']
            if preserve:
                child=out/'child.v';child.write_text('module Child(input A,output Z);assign Z=A;endmodule\n')
                argv+=['--child-netlist',str(child),'--preserve-block','Child']
            real_run=subprocess.run
            def execute(cmd, **kwargs):
                if cmd[0]=='fake-genus':
                    kwargs['stdout'].write('CORAL_GENUS_SYNTHESIS_OK\n')
                    return SimpleNamespace(returncode=0)
                return real_run(cmd, **kwargs)
            with patch.object(sys,'argv',argv),patch.object(run_genus.subprocess,'run',side_effect=execute):
                run_genus.main()
            return (out/'mapped.v').read_text()

    def test_exported_child_implementation_is_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);child=root/'child.v';raw=root/'raw.v';out=root/'unit.v'
            child.write_text('module Child(input A, output Z);\nassign Z=A;\nendmodule\n')
            raw.write_text(child.read_text()+'module Top(input A,output Z);\nChild u(A,Z);\nendmodule\n')
            run_genus.unit_source(raw,[child],out)
            self.assertNotIn('module Child',out.read_text())
            self.assertIn('Child u(A,Z)',out.read_text())
            self.assertIn('module Top',out.read_text())

    def test_unused_helper_output_is_removed(self):
        raw='module Helper(input A,output [1:0] Z);assign Z[1]=A;endmodule\nmodule Top(input A,output O);wire [1:0] z;Helper u(A,z);assign O=z[1];endmodule\n'
        result=self.normalize(raw)
        self.assertIn('assign O = A;',result)
        self.assertNotIn('module Helper',result)

    def test_live_undriven_output_still_fails(self):
        raw='module Helper(input A,output [1:0] Z);assign Z[1]=A;endmodule\nmodule Top(input A,output O);wire [1:0] z;Helper u(A,z);assign O=z[0];endmodule\n'
        with self.assertRaises(subprocess.CalledProcessError):self.normalize(raw)

    def test_separately_mapped_child_boundary_remains(self):
        result=self.normalize('module Child(input A,output Z);assign Z=A;endmodule\nmodule Top(input A,output O);Child u(A,O);endmodule\n',True)
        self.assertIn('Child u',result)
        self.assertNotIn('module Child',result)

if __name__=='__main__':unittest.main()
