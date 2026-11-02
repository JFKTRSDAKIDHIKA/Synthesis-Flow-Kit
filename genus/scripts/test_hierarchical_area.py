import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from hierarchical_area import report,totals
from summary import read_area


class HierarchicalAreaTests(unittest.TestCase):
    def test_multiplicity_and_unresolved_types(self):
        cells={'INV':{'area':2,'sequential':False}}
        self.assertEqual(totals({'Top':{'Child':3},'Child':{'INV':2}},cells,'Top'),{'INV':6})
        with self.assertRaises(ValueError):totals({'Top':{'missing':1}},cells,'Top')
        with self.assertRaises(ValueError):totals({'Top':{'Top':1}},cells,'Top')

    @unittest.skipUnless(os.environ.get('YOSYS'),'workspace Yosys required')
    def test_matches_actual_yosys_stat_with_repeated_children_and_sram(self):
        root=Path(__file__).resolve().parents[2]
        std=root/'build/pdk/usc3n/lib/FSPR-PVT_0P7V_25C.lib'
        macro=root/'build/VmeCoreMini512Axi/itcm8-dtcm128/sram/usc3n/lib/fakeram_128x128.lib'
        if not std.exists() or not macro.exists():self.skipTest('PDK/macro libraries required')
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);net=d/'test.v';streamed=d/'streamed.txt';native=d/'yosys.txt'
            net.write_text('module Child(input A, output Z);\n  INVx1 inv (\n    .A(A),\n    .Y(Z)\n  );\n  DFFHQNx1 ff (\n  );\nendmodule\nmodule Top(input A, output Z);\n  Child a (\n    .A(A),\n    .Z(Z)\n  );\n  Child b (\n    .A(A)\n  );\n  fakeram_128x128 ram (\n  );\nendmodule\n')
            streamed.write_text(report(net,[std,macro],'Top'))
            script=f'read_liberty -lib "{std}"; read_liberty -lib "{macro}"; read_verilog "{net}"; hierarchy -check -top Top; tee -o "{native}" stat -liberty "{std}" -liberty "{macro}"'
            subprocess.run([os.environ['YOSYS'],'-Q','-p',script],stdout=subprocess.DEVNULL,check=True)
            a,b=read_area(streamed),read_area(native)
            self.assertAlmostEqual(a['chip_area_um2'],b['chip_area_um2'],places=5)
            self.assertAlmostEqual(a['sequential_um2'],b['sequential_um2'],places=5)
            self.assertEqual({c:v['count'] for c,v in a['cells'].items()},{c:v['count'] for c,v in b['cells'].items()})
