"""Frozen historical replay plumbing; no real EDA tool is executed."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import legacy_mlem

class LegacyReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ['scripts','reports','synthesis']:(self.root/name).mkdir()
        self.script=self.root/'scripts/mlem-n6-synth.tcl';self.script.write_text('puts MLEM_N6_SYNTH_COMPLETE\n')
        self.pins=self.root/'input-lock.json'
        self.pins.write_text(json.dumps({str(self.script):self.sha(self.script)}))
        self.manifest=self.root/'manifest.json'
        self.manifest.write_text(json.dumps(dict(campaign=str(self.root),genus_lock=str(self.root/'shared.lock'))))
        self.entry=self.root/'entry.py';self.entry.write_text('# entry fixture\n')
    @staticmethod
    def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    @staticmethod
    def write(p,d):Path(p).write_text(json.dumps(d))
    def call(self):return legacy_mlem.replay(self.manifest,'/usr/bin/true',self.sha,self.write,self.entry)
    def test_requires_explicit_frozen_script_hash(self):
        self.pins.write_text('{}')
        with self.assertRaisesRegex(ValueError,'input-lock'):self.call()
    def test_mocked_success_creates_pnr_handoff(self):
        def fake(command,cwd,log,update,env):
            Path(log).write_text('MLEM_N6_SYNTH_COMPLETE\n')
            (self.root/'synthesis/mlem_core.v').write_text('x'*1001)
        with patch.dict(os.environ,{'CDS_LIC_FILE':'fixture-not-a-license'}),patch.object(legacy_mlem,'available',return_value=64*(1<<30)),patch.object(legacy_mlem,'execute',side_effect=fake):
            self.call()
        marker=self.root/'reports/synth.complete.json'
        self.assertEqual(json.loads(marker.read_text())['status'],'completed')
        with self.assertRaisesRegex(ValueError,'already exists'):self.call()
    def test_marker_in_echoed_command_is_not_execution_success(self):
        def fake(command,cwd,log,update,env):Path(log).write_text('puts MLEM_N6_SYNTH_COMPLETE\n')
        with patch.dict(os.environ,{'CDS_LIC_FILE':'fixture-not-a-license'}),patch.object(legacy_mlem,'available',return_value=64*(1<<30)),patch.object(legacy_mlem,'execute',side_effect=fake):
            with self.assertRaisesRegex(RuntimeError,'completion'):self.call()
        self.assertFalse((self.root/'reports/synth.complete.json').exists())

if __name__=='__main__':unittest.main()
