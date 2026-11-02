"""Check that cone extraction moves logic once and preserves shared state."""
import copy
import unittest
from genus_lsu_factor import split_cones,HELPERS

class FactorTests(unittest.TestCase):
    def test_exclusive_logic_moves_once_shared_state_stays(self):
        def cell(kind,a,y):
            return dict(type=kind,parameters={},attributes={},
                port_directions={'A':'input','Y':'output'},connections={'A':[a],'Y':[y]})
        cells={'state':cell('$dff',2,3),'shared':cell('$not',3,4)}
        nets={}
        for i in range(65):
            cells['address_internal'+str(i)]=cell('$not',4,300+i)
            cells['address'+str(i)]=cell('$not',300+i,100+i)
            nets['stateFromAction_nextRowAddrCandidates_'+str(i)]={'bits':[100+i]}
        for i in range(1,64):
            cells['data_internal'+str(i)]=cell('$not',4,400+i)
            cells['data'+str(i)]=cell('$not',400+i,200+i)
            nets['windowNormal_'+str(i)+'_data']={'bits':[200+i]}
        original=copy.deepcopy(cells)
        modules={'lsu':dict(cells=cells,netnames=nets,
            ports={'clock':dict(direction='input',bits=[2]),
                   'result':dict(direction='output',bits=list(range(100,165))+list(range(201,264)))})}
        stats=split_cones(modules,'lsu')
        self.assertEqual(len(stats),8)
        parent=modules['lsu']['cells']
        self.assertIn('state',parent);self.assertIn('shared',parent)
        primitive={n:c for n,c in parent.items() if c['type'] not in HELPERS}
        for helper in HELPERS:
            for n,c in modules[helper]['cells'].items():
                self.assertNotIn(n,primitive);primitive[n]=c
        self.assertEqual(primitive,original)
        self.assertEqual(sum(g['cells'] for g in stats),256)
        for helper in HELPERS:
            internal=[v for n,v in modules[helper]['netnames'].items() if n not in modules[helper]['ports']]
            self.assertTrue(internal)
            self.assertTrue(all(len(v['bits'])==1 for v in internal))

if __name__=='__main__':unittest.main()
