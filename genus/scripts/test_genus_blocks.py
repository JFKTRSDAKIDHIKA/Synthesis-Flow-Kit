"""Check selection of actual parameter-specialized modules and block dependencies."""
import unittest
import hashlib
import tempfile
from pathlib import Path
from genus_blocks import build_plan, check_prepared_rtl, mapping_identity, HERE, MATRIX_FACTOR_RTL

def module(clock='clock',children=()):
    return {'ports':{clock:{'direction':'input','bits':[2]}},'cells':{str(i):{'type':t} for i,t in enumerate(children)}}

class GenusBlockPlanTests(unittest.TestCase):
    def test_combinational_leaf_uses_virtual_clock_and_sequential_leaf_rejected(self):
        leaf = {'ports': {}, 'cells': {}}
        data = {'modules': {'Top': module('io_aclk', ['Leaf']), 'Leaf': leaf}}
        self.assertEqual(build_plan(data, 'Top', ['Leaf'])['units'][0]['clock'], '__virtual_clock__')
        leaf['cells']['register'] = {'type': '$dff'}
        with self.assertRaisesRegex(ValueError, 'sequential block'):
            build_plan(data, 'Top', ['Leaf'])
    def test_fpnew_clock_name_is_recognized(self):
        plan=build_plan({'modules':{'Top':module('io_aclk',['Fma']), 'Fma':module('clk_i')}},'Top',['Fma'])
        self.assertEqual(plan['units'][0]['clock'],'clk_i')

    def test_parameter_instances_remain_distinct_and_leaf_first(self):
        data={'modules':{'Top':module('io_aclk',['Mid$Top.u','Leaf$Top.v']),
                         'Mid$Top.u':module(children=['Leaf$Top.u.u']),
                         'Leaf$Top.u.u':module(),'Leaf$Top.v':module()}}
        plan=build_plan(data,'Top',['Mid','Leaf'])
        self.assertEqual(len(plan['units']),4)
        self.assertEqual(plan['units'][-1]['unit'],'Top')
        leaves=[u for u in plan['units'] if u['rtl_top'].startswith('Leaf$')]
        self.assertEqual(len({u['unit'] for u in leaves}),2)
        self.assertTrue(all(not u['children'] for u in leaves))
        self.assertEqual(len(plan['units'][-1]['children']),2)
    def test_missing_boundary_is_rejected(self):
        with self.assertRaises(ValueError):build_plan({'modules':{'Top':module('io_aclk')}},'Top',['Leaf'])
    def test_unknown_clock_is_rejected(self):
        with self.assertRaises(ValueError):build_plan({'modules':{'Top':module('mystery')}},'Top',[])
    def test_non_block_intermediate_hierarchy_is_traversed(self):
        data={'modules':{'Top':module('io_aclk',['Wrapper']), 'Wrapper':module(children=['Leaf$Top.u']), 'Leaf$Top.u':module()}}
        plan=build_plan(data,'Top',['Leaf'])
        self.assertEqual(len(plan['units']),2)
        self.assertEqual(plan['units'][-1]['children'],[plan['units'][0]['unit']])

    def test_only_known_postprocessing_change_reuses_native_mapping(self):
        driver=str((HERE/'run_genus.py').resolve())
        old={'hashes':{driver:'1a507ae585ac22b46c4ed73c67e201908d88195f92341569faf89de51b5501a9'},'threads':8}
        current={'hashes':{driver:hashlib.sha256((HERE/'run_genus.py').read_bytes()).hexdigest()},'threads':8}
        prefix=hashlib.sha256((HERE/'run_genus.py').read_text().split('    libraries = [',1)[0].encode()).hexdigest()
        # Only the documented, byte-identical native prefix may inherit the old key.
        if prefix == 'ab4e40b624acc64a39f19fedbba02d7e3128f71da9f3d8280276de37ed64cf6b':
            self.assertEqual(mapping_identity(old),mapping_identity(current))
        else:
            self.assertNotEqual(mapping_identity(old),mapping_identity(current))
        self.assertEqual(mapping_identity(current)['hashes'][driver],prefix)
        unknown={'hashes':{driver:'different-native-driver'},'threads':8}
        self.assertNotEqual(mapping_identity(unknown),mapping_identity(current))
        changed_threads=dict(current,threads=4)
        self.assertNotEqual(mapping_identity(current),mapping_identity(changed_threads))

    def test_unlowered_mux_is_rejected_before_genus(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'unit.v'
            path.write_text('module T();\n  \\$bwmux #(.WIDTH(8)) u();\nendmodule\n')
            with self.assertRaisesRegex(ValueError,'Unlowered Yosys cells'):
                check_prepared_rtl(path)
            path.write_text('module T(input A,B,S,output Y);assign Y=S?B:A;endmodule\n')
            check_prepared_rtl(path)

    def test_cache_identity_preserves_mapping_settings_and_inputs(self):
        driver=str((HERE/'genus_blocks.py').resolve())
        original=dict(hashes={driver:'old','genus-block.tcl':'tcl','run_genus.py':'normalizer'},period=2500,threads=8,blocks='Leaf',flatten_blocks='')
        changed=dict(original,hashes=dict(original['hashes'],**{driver:'new'}))
        self.assertEqual(mapping_identity(original),mapping_identity(changed))
        self.assertEqual(mapping_identity(original),mapping_identity(dict(changed,blocks='Leaf Parent',flatten_blocks='Parent')))
        self.assertEqual(mapping_identity(original),mapping_identity(dict(changed,clean_blocks='Parent')))
        factor=dict(changed,factor_mt=True,hashes=dict(changed['hashes'],**{
            str(HERE/'genus_mt.py'):'factor',str(MATRIX_FACTOR_RTL):'rtl'}))
        self.assertEqual(mapping_identity(original),mapping_identity(factor))
        lsu=dict(factor,factor_lsu=True,factor_lsu_state=True,hashes=dict(factor['hashes'],**{str(HERE/'genus_lsu_factor.py'):'lsu',str(HERE/'genus_lsu_state.py'):'state',str(HERE/'genus_cec.py'):'cec',str(HERE/'genus_cec_x.py'):'defined-cec',str(HERE/'genus_cec_parts.py'):'partitioned-cec'}))
        self.assertEqual(mapping_identity(original),mapping_identity(lsu))
        for key,value in [('period',5000),('threads',1)]:
            self.assertNotEqual(mapping_identity(original),mapping_identity(dict(original,**{key:value})))
        changed['hashes']['run_genus.py']='changed'
        self.assertNotEqual(mapping_identity(original),mapping_identity(changed))

if __name__=='__main__':unittest.main()
