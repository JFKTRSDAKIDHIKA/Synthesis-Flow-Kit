"""Bank extraction must retain all table registers and move each logic cell once."""
import copy
import unittest
from genus_lsu_state import split_cones,HELPERS

class StateBankTests(unittest.TestCase):
    def test_register_table_and_shared_control_stay_in_parent(self):
        cells={'shared':dict(type='$not',parameters={},attributes={},
            port_directions={'A':'input','Y':'output'},connections={'A':[4],'Y':[5]})}
        nets={};state=[]
        for i in range(128):
            q=300+i;d=1000+i;state.append(q)
            cells['reg'+str(i)]=dict(type='$dff',parameters={},attributes={},
                port_directions={'D':'input','CLK':'input','Q':'output'},
                connections={'D':[d],'CLK':[2],'Q':[q]})
            cells['update'+str(i)]=dict(type='$mux',parameters={},attributes={},
                port_directions={'A':'input','B':'input','S':'input','Y':'output'},
                connections={'A':[q],'B':[4],'S':[5],'Y':[d]})
            nets['slot.state_cells_'+str(i)+'_mask']=dict(bits=[q])
        original=copy.deepcopy(cells)
        modules={'lsu':dict(cells=cells,netnames=nets,ports={
            'clock':dict(direction='input',bits=[2]),'data':dict(direction='input',bits=[4]),
            'state':dict(direction='output',bits=state)})}
        stats=split_cones(modules,'lsu');parent=modules['lsu']['cells']
        self.assertEqual(len(stats),8)
        self.assertTrue(all(s['cells']==16 for s in stats))
        self.assertIn('shared',parent)
        self.assertEqual(sum(c['type']=='$dff' for c in parent.values()),128)
        primitive={n:c for n,c in parent.items() if c['type'] not in HELPERS}
        for helper in HELPERS:
            for n,c in modules[helper]['cells'].items():
                self.assertNotIn(n,primitive);self.assertEqual(c['type'],'$mux');primitive[n]=c
        self.assertEqual(primitive,original)
        self.assertEqual(modules['lsu']['ports']['state']['bits'],state)

if __name__=='__main__':unittest.main()
