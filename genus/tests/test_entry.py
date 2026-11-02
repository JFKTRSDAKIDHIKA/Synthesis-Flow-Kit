"""Exercise the public entry without a Cadence license or a production RTL run."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('canonical_genus_entry',ROOT/'run.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)

GENUS=r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
if os.environ.get('FAKE_MUTATE_CONFIG'):
    Path(os.environ['FAKE_MUTATE_CONFIG']).write_text('{}')
if os.environ.get('FAKE_NO_MARKER'):
    print('simulated license rejection');sys.exit(0)
if Path(sys.argv[-1]).name=='final-check.tcl':
    out=Path(os.environ['FLOW_OUT'])
    for n in ['native-structure.rpt','timing-intent.rpt','clocks.rpt','area.rpt','setup.rpt','hold.rpt','constraints.sdc']:
        (out/n).write_text('fixture only\n')
    print('CORAL_GENUS_FINAL_CHECK_OK')
else:
    out=Path(os.environ['FLOW_OUT'])
    (out/'netlist/genus-raw.v').write_text('module Top(input clock);\nendmodule\n')
    (out/'reports/constraints.sdc').write_text('create_clock -name core_clock -period 2500 [get_ports clock]\n')
    print('CORAL_GENUS_SYNTHESIS_OK')
'''
YOSYS=r'''#!/usr/bin/env python3
import shlex,sys
from pathlib import Path
out=shlex.split(sys.argv[-1])[-1]
Path(out).write_text('module Top(input clock);\nendmodule\n')
'''

class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for n,content in [('genus',GENUS),('yosys',YOSYS)]:
            p=self.root/n;p.write_text(content);p.chmod(0o755)
        (self.root/'rtl.v').write_text('module Top(input clock);endmodule\n')
        (self.root/'bb.v').write_text('// none\n')
        (self.root/'standard.lib').write_text('library(test) {}\n')
        (self.root/'macros').mkdir()
        (self.root/'spec.json').write_text('{"macros":[]}')
        self.config=dict(schema_version=1,platform_id='fixture-only-not-hardware',flow='flat',top='Top',
            rtl='rtl.v',blackboxes='bb.v',std_lib='standard.lib',sram_lib_dir='macros',spec='spec.json',
            out_dir='output',lock_file='shared.lock',admission_gib=0,
            tools={'genus':'./genus','yosys':'./yosys'},options={'period_ps':2500,'clk_port':'clock'})
        self.path=self.root/'configuration.json';self.save()
    def save(self):self.path.write_text(json.dumps(self.config))
    def invoke(self,command,env=None):
        return subprocess.run([sys.executable,str(ROOT/'run.py'),command,str(self.path)],
            cwd='/',env=dict(os.environ,**(env or {})),capture_output=True,text=True)
    def test_check_resolves_relative_to_config_and_launches_nothing(self):
        result=self.invoke('check');self.assertEqual(result.returncode,0,result.stderr)
        launch=json.loads(result.stdout)
        self.assertIn(str(self.root/'rtl.v'),launch['inputs_sha256'])
        self.assertFalse((self.root/'output').exists())
        self.assertFalse((self.root/'shared.lock').exists())
        self.assertEqual(launch['resource_policy']['threads'],2)
    def test_full_entry_snapshots_runs_validates_and_refuses_overwrite(self):
        result=self.invoke('synth');self.assertEqual(result.returncode,0,result.stderr)
        out=self.root/'output';launch=json.loads((out/'launch.json').read_text())
        self.assertEqual(json.loads((out/'entry-status.json').read_text())['state'],'complete')
        self.assertTrue((out/'native-final/hold.rpt').exists())
        self.assertTrue((out/'flow-source/scripts/repair_genvars.py').exists())
        self.assertIn(str(out/'flow-source/scripts/run_genus.py'),launch['command'])
        self.assertEqual(hashlib.sha256((ROOT/'tcl/genus.tcl').read_bytes()).hexdigest(),
                         hashlib.sha256((out/'flow-source/tcl/genus.tcl').read_bytes()).hexdigest())
        for stage in (ROOT/'tcl').rglob('*.tcl'):
            copied=out/'flow-source'/stage.relative_to(ROOT)
            self.assertEqual(stage.read_bytes(),copied.read_bytes())
        second=self.invoke('synth');self.assertEqual(second.returncode,2)
        self.assertIn('Preserve existing results',second.stderr)
    def test_zero_exit_without_native_marker_is_failure(self):
        result=self.invoke('synth',{'FAKE_NO_MARKER':'1'});self.assertEqual(result.returncode,2)
        status=json.loads((self.root/'output/entry-status.json').read_text())
        self.assertEqual(status['state'],'failed')
    def test_configuration_change_during_execution_is_rejected(self):
        result=self.invoke('synth',{'FAKE_MUTATE_CONFIG':str(self.path)})
        self.assertEqual(result.returncode,2)
        self.assertIn('Configuration changed during',result.stderr)
        self.assertEqual(json.loads((self.root/'output/entry-status.json').read_text())['state'],'failed')
    def test_lock_contention_never_launches_or_creates_outputs(self):
        with (self.root/'shared.lock').open('a+') as slot:
            fcntl.flock(slot,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.invoke('synth')
        self.assertEqual(result.returncode,75,result.stderr)
        self.assertFalse((self.root/'output').exists())
    def test_input_hash_mismatch_is_rejected(self):
        self.config['input_sha256']={'rtl.v':'0'*64};self.save()
        result=self.invoke('check');self.assertEqual(result.returncode,2)
        self.assertIn('Pinned input changed',result.stderr)
    def test_output_must_not_mix_with_source(self):
        self.config['out_dir']=str(ROOT/'scripts/results');self.save()
        result=self.invoke('check');self.assertEqual(result.returncode,2)
        self.assertIn('Outputs must be outside',result.stderr)
    def test_unknown_options_fail_closed(self):
        self.config['options']['timing_overide']=42;self.save()
        self.assertEqual(self.invoke('check').returncode,2)
    def test_internal_native_backend_rejects_direct_launch(self):
        env=dict(os.environ);env.pop('CORAL_GENUS_ENTRY',None)
        result=subprocess.run([sys.executable,str(ROOT/'scripts/run_genus.py'),'--help'],
                              env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('internal backend',result.stderr)

if __name__=='__main__':unittest.main()
