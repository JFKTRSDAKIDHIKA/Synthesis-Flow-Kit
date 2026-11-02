"""Check that LSU proof preserves state and rejects reset/clock changes."""
import unittest
from genus_lsu import state_comparison


def design(offset=0):
    def b(value): return value+offset
    return dict(attributes={},ports={
        'clock':dict(direction='input',bits=[b(2)]),
        'reset':dict(direction='input',bits=[b(3)]),
        'data':dict(direction='input',bits=[b(6),b(7)]),
        'result':dict(direction='output',bits=[b(4),b(5)])},
        netnames={'state':dict(bits=[b(4),b(5)],hide_name=0,attributes={})},
        cells={'state_reg':dict(type='$adff',parameters={
            'CLK_POLARITY':'1','ARST_POLARITY':'1','ARST_VALUE':'01','WIDTH':'10'},
            connections={'CLK':[b(2)],'ARST':[b(3)],'D':[b(6),b(7)],'Q':[b(4),b(5)]})})


class LsuProofTests(unittest.TestCase):
    def test_arbitrary_common_state_and_all_next_state_bits(self):
        comparison,count=state_comparison(design(),design(100))
        self.assertEqual(count,2)
        for m in comparison['modules'].values():
            self.assertFalse(m['cells'])
            self.assertEqual(len([p for p in m['ports'] if p.startswith('proof_next_')]),2)
            self.assertEqual(m['ports']['proof_next_0']['bits'],m['ports']['data']['bits'][:1])
            self.assertEqual(m['ports']['result']['bits'][0],m['ports']['proof_state_104']['bits'][0])
        self.assertEqual(set(comparison['modules']['gold']['ports']),set(comparison['modules']['gate']['ports']))

    def test_changed_asynchronous_reset_value_is_rejected(self):
        gate=design(100);gate['cells']['state_reg']['parameters']['ARST_VALUE']='00'
        with self.assertRaises(AssertionError):state_comparison(design(),gate)

    def test_changed_clock_edge_is_rejected(self):
        gate=design(100);gate['cells']['state_reg']['parameters']['CLK_POLARITY']='0'
        with self.assertRaises(AssertionError):state_comparison(design(),gate)

    def test_state_bijection_is_required(self):
        gate=design(100);gate['netnames']['state']['bits']=[104,104]
        with self.assertRaisesRegex(AssertionError,'bijective'):state_comparison(design(),gate)


if __name__=='__main__':unittest.main()
