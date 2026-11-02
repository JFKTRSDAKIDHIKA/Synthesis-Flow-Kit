"""Grouping must preserve all lanes and reject incompatible bank controls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_ameooo_wide_sram import bind


class WideSramBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.models = self.root / 'models'
        self.models.mkdir()
        configs = []
        for kind, depth, ports, instances in [('itcm',128,'1rw',2),('dtcm',2048,'1rw',2),('mrf',1024,'1r1w',16)]:
            name = f'asap7_est_{kind}_{depth}x256_{ports}'
            configs.append(dict(name=name,depth=depth,logical_depth=768 if kind=='mrf' else depth,width=256,ports=ports,instances=instances))
        for folder in ['models','lib','blackboxes']:
            (self.models/folder).mkdir()
            for cfg in configs:
                (self.models/folder/(cfg['name']+('.lib' if folder=='lib' else '.v'))).write_text('// Unit-test fixture\n')
        manifest = {'model_id':'ameooo-asap7-derived-sram-estimates-wide-v5',
                    'organization':{'mrf_width':256,'tcm_slice_width':256,'mrf_read_replicas':2,'mrf_bits_per_replica_word':2048},
                    'macros':configs,'logical_bytes':532480,'physical_model_bytes':663552,'unused_padding_bytes':131072,
                    'output_sha256':{str(p.relative_to(self.models)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.models.rglob('*') if p.is_file()}}
        (self.models/'model-manifest.json').write_text(json.dumps(manifest))
        parts = [f'module memory_{depth}x512(\n input clock\n);\nendmodule\n' for depth in [128,2048]]
        parts.append('module AmeRegisterFile(\n input clock\n);\n')
        for lane in range(32):
            for replica in range(2):
                parts.append(f'''  readDataFromEachBank_bankMemoryForReadPort_768x64 bank_r{replica}_{lane} (
    .R0_addr (read_address{replica}), .R0_en (read_enable{replica}), .R0_clk (clock),
    .R0_data (read_data{replica}_{lane}), .W0_addr (write_address), .W0_en (write_enable),
    .W0_clk (clock), .W0_data (io_writeRequestToMrf_bits_data[{lane*64+63}:{lane*64}])
  );\n''')
        parts.append('endmodule\n')
        self.rtl = self.root/'AMEOOO.sv'
        self.source = ''.join(parts)
        self.rtl.write_text(self.source)
        self.out = self.root/'binding'

    def test_groups_all_lanes_and_preserves_source(self):
        result = bind(self.rtl,self.models,self.out)
        self.assertEqual(self.rtl.read_text(),self.source)
        self.assertEqual(result['macro_instances'],20)
        mapped = (self.out/'AMEOOO.asap7-est.sv').read_text()
        self.assertEqual(mapped.count('  coral_mrf_768x256_1r1w coral_mrf_'),16)
        certificate = json.loads((self.out/'grouping-certificate.json').read_text())
        self.assertEqual(certificate['source_read_lanes'],64)
        self.assertEqual(len(certificate['groups']),16)
        self.assertEqual(certificate['full_source_inverse_reconstruction'],'passed')

    def reject_mutation(self,old,new):
        self.assertIn(old,self.source)
        self.rtl.write_text(self.source.replace(old,new,1))
        with self.assertRaises(AssertionError):
            bind(self.rtl,self.models,self.out)
        self.assertFalse(self.out.exists())

    def test_rejects_independent_bank_address(self):
        self.reject_mutation('.R0_addr (read_address0)','.R0_addr (third_address)')

    def test_rejects_independent_bank_enable(self):
        self.reject_mutation('.R0_en (read_enable0)','.R0_en (different_enable)')

    def test_rejects_misaligned_write_data(self):
        self.reject_mutation('bits_data[63:0]','bits_data[64:1]')

    def test_rejects_missing_write_lane(self):
        self.reject_mutation('bits_data[127:64]','bits_data[63:0]')

    def test_rejects_aliased_read_lane(self):
        self.reject_mutation('.R0_data (read_data0_1)','.R0_data (read_data0_0)')

    def test_rejects_tampered_model(self):
        next((self.models/'lib').glob('*.lib')).write_text('modified')
        with self.assertRaises(AssertionError):
            bind(self.rtl,self.models,self.out)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
