"""Execute the real Tcl drivers with command stubs; no Cadence license needed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
proc unknown {name args} {
    if {$name eq "syn_map" && [info exists ::env(TEST_FAIL_MAP)]} {
        error "injected mapping failure"
    }
    if {$name in {read_hdl elaborate syn_generic syn_map syn_opt read_netlist read_sdc write_hdl}} {
        puts "NATIVE $name"
    }
    if {$name in {create_clock set_input_delay set_output_delay set_clock_uncertainty set_false_path}} {
        puts "CONSTRAINT $name $args"
    }
    if {$name eq "get_db"} {
        if {[lindex $args end] eq ".period"} {return 2500}
        if {[lindex $args 0] eq "hinsts"} {return {}}
        return core_clock
    }
    if {$name eq "get_ports"} {return clock}
    return {}
}
source [lindex $argv 0]
'''

@unittest.skipUnless(shutil.which('tclsh'), 'requires Tcl interpreter')
class TclStageTests(unittest.TestCase):
    def run_driver(self, driver, **overrides):
        with tempfile.TemporaryDirectory(prefix='genus stages ') as directory:
            root = Path(directory)
            # Run a copy from an unrelated cwd, as campaign snapshots do.
            shutil.copytree(ROOT/'tcl', root/'snapshot')
            harness = root/'harness.tcl'; harness.write_text(HARNESS)
            values = dict(TOP='Top', OUT=str(root), HIERARCHY='preserve', THREADS='2',
                          EFFORT='medium', STD_LIB='standard.lib', SRAM_LIB_DIR=str(root),
                          DEFINES='SYNTHESIS', BLACKBOXES='bb.v', RTL='rtl.v',
                          CHILD_NETLISTS='', PRESERVE_BLOCKS='', CLK_PORT='clock',
                          PERIOD_PS='2500', CLK_TRANSITION_PS='20', UNCERTAINTY_PS='100',
                          HOLD_UNCERTAINTY_PS='50', STA_MODE='io', IO_FRACTION='0.2',
                          IO_MIN_PS='100', INPUT_TRANSITION_PS='50', OUTPUT_LOAD_FF='4',
                          SDC='original.sdc', EXPECTED_MACROS='')
            env = dict(os.environ, **{'FLOW_'+k:v for k,v in values.items()})
            env.update(overrides)
            return subprocess.run(['tclsh', str(harness), str(root/'snapshot'/driver)],
                                  cwd='/', env=env, capture_output=True, text=True)

    def test_flat_and_block_execute_all_stages_in_order(self):
        for driver in ['genus.tcl', 'genus-block.tcl']:
            with self.subTest(driver=driver):
                result = self.run_driver(driver)
                self.assertEqual(result.returncode, 0, result.stderr)
                native = [line for line in result.stdout.splitlines() if line.startswith('NATIVE ')]
                self.assertEqual(native, ['NATIVE '+name for name in
                    ['read_hdl', 'elaborate', 'syn_generic', 'syn_map', 'syn_opt', 'write_hdl']])
                self.assertEqual(result.stdout.count('CORAL_GENUS_STAGE_END'), 10)
                self.assertIn('CORAL_GENUS_SYNTHESIS_OK', result.stdout)

    def test_constraints_execute_before_synthesis(self):
        for driver in ['genus.tcl', 'genus-block.tcl']:
            with self.subTest(driver=driver):
                result = self.run_driver(driver)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('CONSTRAINT create_clock -name core_clock -period 2500 clock', result.stdout)
                self.assertIn('CONSTRAINT set_output_delay -max 500.0', result.stdout)
                self.assertIn('CONSTRAINT set_input_delay -max 500.0', result.stdout)
                self.assertIn('CONSTRAINT set_clock_uncertainty -setup 100', result.stdout)
                self.assertLess(result.stdout.index('CONSTRAINT set_output_delay'),
                                result.stdout.index('NATIVE syn_generic'))
                self.assertNotIn('CONSTRAINT set_false_path', result.stdout)
        result = self.run_driver('genus.tcl', FLOW_STA_MODE='reg2reg')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('CONSTRAINT set_false_path'), 2)
        self.assertNotIn('CONSTRAINT set_output_delay', result.stdout)

    def test_block_reads_child_netlist(self):
        result = self.run_driver('genus-block.tcl', FLOW_CHILD_NETLISTS='child.v',
                                 FLOW_PRESERVE_BLOCKS='Child', FLOW_CLK_PORT='__virtual_clock__')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('NATIVE read_hdl'), 2)

    def test_stage_failure_stops_before_optimization_or_success(self):
        result = self.run_driver('genus.tcl', TEST_FAIL_MAP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('injected mapping failure', result.stderr)
        self.assertNotIn('NATIVE syn_opt', result.stdout)
        self.assertNotIn('CORAL_GENUS_SYNTHESIS_OK', result.stdout)
        self.assertNotIn('CORAL_GENUS_STAGE_END 60-map', result.stdout)

    def test_final_restore_never_synthesizes(self):
        result = self.run_driver('final-check.tcl')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CORAL_GENUS_FINAL_CHECK_OK', result.stdout)
        self.assertEqual(result.stdout.count('CORAL_GENUS_FINAL_STAGE_END'), 4)
        self.assertNotIn('NATIVE syn_', result.stdout)
        self.assertIn('NATIVE read_sdc', result.stdout)

if __name__ == '__main__':
    unittest.main()
