"""Metrics parsing must retain missing data and avoid hierarchical double counts."""
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from evidence import REQUIRED,seal,digest
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
            result=module.compare(root/'base',root/'variant',allow_unsealed=True)
            self.assertEqual(result['metrics']['worst_reported_setup_slack_ps']['delta'],20)
            self.assertIsNone(result['metrics']['cell_area']['delta'])
            config['platform_id']='other';(root/'variant/configuration.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'Different hardware'):module.compare(root/'base',root/'variant',allow_unsealed=True)
            self.assertFalse(module.compare(root/'base',root/'variant',True,allow_unsealed=True)['same_hardware_inputs'])

    def fixture_run(self,run,source):
        run.mkdir(parents=True)
        config=dict(platform_id='fixture',top='Top',flow='flat',rtl='rtl.v',blackboxes='bb.v',std_lib='std.lib',spec='spec.json',sram_lib_dir='macros')
        pins={str(source/name):'fixture-'+name for name in ('rtl.v','bb.v','std.lib','spec.json')}
        pins[str(source/'macros/memory.lib')]='macro-fixture'
        launch=dict(configuration=str(source/'config.json'),inputs_sha256=pins,tools_sha256={'genus':'fixture'})
        for relative in REQUIRED:
            p=run/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture only')
        (run/'configuration.json').write_text(json.dumps(config));(run/'launch.json').write_text(json.dumps(launch))
        (run/'native-final/setup.rpt').write_text('Slack:= 10\n')
        status=dict(state='complete',stage='synth',mapped_sha256=digest(run/'mapped.v'),evidence_schema_version=1,artifacts_sha256=seal(run))
        (run/'entry-status.json').write_text(json.dumps(status))

    def test_changed_reports_and_incomplete_seals_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=root/'base';b=root/'variant'
            self.fixture_run(a,root);self.fixture_run(b,root)
            self.assertEqual(module.compare(a,b)['evidence_integrity']['baseline'],'VERIFIED')
            (b/'native-final/setup.rpt').write_text('Slack:= 9999\n')
            with self.assertRaisesRegex(ValueError,'Retained artifact changed'):module.compare(a,b)
            status=json.loads((b/'entry-status.json').read_text());status['artifacts_sha256'].pop('native-final/setup.rpt')
            (b/'entry-status.json').write_text(json.dumps(status))
            with self.assertRaisesRegex(ValueError,'Incomplete retained'):module.compare(a,b,allow_unsealed=True)

    def test_legacy_unsealed_comparisons_require_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=root/'base';b=root/'variant'
            self.fixture_run(a,root);self.fixture_run(b,root)
            status=json.loads((b/'entry-status.json').read_text());status.pop('artifacts_sha256');status.pop('evidence_schema_version')
            (b/'entry-status.json').write_text(json.dumps(status))
            with self.assertRaisesRegex(ValueError,'Legacy synthesis'):module.compare(a,b)
            self.assertEqual(module.compare(a,b,allow_unsealed=True)['evidence_integrity']['variant'],'UNVERIFIED_LEGACY')

    def test_relocation_aliases_preserve_recorded_input_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'old';old.mkdir();(old/'macros').mkdir()
            self.fixture_run(old/'base',old);self.fixture_run(old/'variant',old)
            moved=root/'moved';old.rename(moved);old.symlink_to(moved,target_is_directory=True)
            result=module.compare(moved/'base',moved/'variant')
            self.assertTrue(result['same_hardware_inputs'])
            launch=json.loads((moved/'base/launch.json').read_text());config=json.loads((moved/'base/configuration.json').read_text())
            ident=module.hardware_identity(config,launch)
            self.assertEqual(ident['macros'],['macro-fixture'])
            self.assertEqual(ident['inputs']['rtl'],'fixture-rtl.v')
            launch['inputs_sha256'].pop(str(old/'rtl.v'))
            with self.assertRaisesRegex(ValueError,'identity missing'):module.hardware_identity(config,launch)

    def test_explicit_library_roles_do_not_depend_on_removed_paths(self):
        config=dict(platform_id='fixture',top='Top',flow='flat')
        roles=dict(inputs=dict(rtl='rtl',blackboxes='bb',std_lib='std',spec='spec'),macros=['macro'],hold_corner=dict(corner_id='tt',std_lib='std',macros=['macro']))
        launch=dict(hardware_inputs=roles,inputs_sha256={name:name for name in ['rtl','bb','std','spec','macro']},tools_sha256={'genus':'tool'})
        result=module.hardware_identity(config,launch)
        self.assertEqual(result['macros'],['macro'])
        launch['inputs_sha256'].pop('macro')
        with self.assertRaisesRegex(ValueError,'no pinned input'):module.hardware_identity(config,launch)

    def test_unknown_timing_format_is_unavailable(self):
        self.assertIsNone(module.timing('No timing paths.'))
        self.assertEqual(module.timing('Path 1: VIOLATED (-40 ps) Late Setup'),-40)
        self.assertEqual(module.timing('-12.750 slack (VIOLATED)'),-12.75)

if __name__=='__main__':unittest.main()
