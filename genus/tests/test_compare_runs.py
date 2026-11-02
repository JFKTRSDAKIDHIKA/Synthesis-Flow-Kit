"""Metrics parsing must retain missing data and avoid hierarchical double counts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('compare_runs',ROOT/'scripts/compare_runs.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class CompareTests(unittest.TestCase):
    def test_native_metrics_and_missing_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);native=out/'native-final';native.mkdir()
            (native/'setup.rpt').write_text('Slack:= 309\nSlack:= -12.5\n')
            (native/'area.rpt').write_text('Top NA 20 100.250 0.000 100.250 ZeroWireload\n  child Child 15 80 0 80\n')
            metrics=module.collect(out,'Top')
            self.assertEqual(metrics['worst_reported_setup_slack_ps'],-12.5)
            self.assertIsNone(metrics['worst_reported_hold_slack_ps'])
            self.assertEqual(metrics['cell_area'],100.25)
            self.assertEqual(metrics['cell_count'],20)

    def test_side_by_side_deltas_and_identity_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,slack in [('base',10),('variant',30)]:
                run=root/name;native=run/'native-final';native.mkdir(parents=True)
                config=dict(platform_id='fixture',top='Top',flow='flat',rtl='rtl.v',blackboxes='bb.v',std_lib='std.lib',spec='spec.json',sram_lib_dir='macros')
                launch=dict(configuration=str(root/'config.json'),inputs_sha256={str(root/p):p for p in ['rtl.v','bb.v','std.lib','spec.json']},tools_sha256={'genus':'fixture'})
                for filename,value in [('configuration.json',config),('launch.json',launch),('entry-status.json',dict(state='complete',stage='synth'))]:
                    (run/filename).write_text(json.dumps(value))
                (native/'setup.rpt').write_text(f'Slack:= {slack}\n')
            result=module.compare(root/'base',root/'variant')
            self.assertEqual(result['metrics']['worst_reported_setup_slack_ps']['delta'],20)
            self.assertIsNone(result['metrics']['cell_area']['delta'])
            config['platform_id']='other';(root/'variant/configuration.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'Different hardware'):module.compare(root/'base',root/'variant')
            self.assertFalse(module.compare(root/'base',root/'variant',True)['same_hardware_inputs'])

    def test_unknown_timing_format_is_unavailable(self):
        self.assertIsNone(module.timing('No timing paths.'))
        self.assertEqual(module.timing('Path 1: VIOLATED (-40 ps) Late Setup'),-40)
        self.assertEqual(module.timing('-12.750 slack (VIOLATED)'),-12.75)

if __name__=='__main__':unittest.main()
