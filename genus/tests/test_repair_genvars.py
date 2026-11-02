"""Relocated repair must require explicit proof rather than campaign discovery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from repair_genvars import repair

class RepairTests(unittest.TestCase):
    def test_repair_inverse_then_explicit_proof_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);rtl=root/'source.sv';out=root/'proof.json'
            names=['rvv_backend_falu','rvv_backend_mulmac','rvv_backend_vrf']
            rtl.write_text('\n'.join('module '+n+'(input x); genvar i,j,k; endmodule' for n in names))
            data={'modules':{n:{'cells':{}} for n in names}}
            cells={}
            for name,target,params in [('u_falu',names[0],{'i':1}),('u_mulmac',names[1],{'i':1}),('u_vrf',names[2],{'j':5,'k':15})]:
                cells[name]={'type':target,'parameters':{k:format(v,'032b') for k,v in params.items()},'connections':{'x':[2]}}
            data['modules']['RvvCoreWrapper__fixture']={'cells':cells}
            first=repair(data,rtl,out)
            self.assertTrue(first['exact_inverse_passed'])
            self.assertTrue(all(not c['parameters'] for c in cells.values()))
            with self.assertRaisesRegex(ValueError,'explicit'):repair(data,rtl,root/'bad.json')
            second=repair(data,rtl,root/'reused.json',out)
            self.assertTrue(second['already_repaired'])
            cells['u_falu']['connections']['x']=[3]
            with self.assertRaisesRegex(AssertionError,'drift'):repair(data,rtl,root/'drift.json',out)

if __name__=='__main__':unittest.main()
