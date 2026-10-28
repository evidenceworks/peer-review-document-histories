"""Minimal regression tests for the standalone public reproduction routes."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class PublicReleaseTests(unittest.TestCase):
    def test_all_public_files_have_expected_types(self):
        assert (ROOT/'analysis/result_manifest.json').is_file()
        assert (ROOT/'analysis/FigureProduction/Data/input_manifest.json').is_file()
        for suffix in ('.docx','.xlsx','.pdf'):
            self.assertFalse(any(x.suffix==suffix for x in ROOT.rglob('*')))
        self.assertFalse((ROOT/'analysis/Evidence').exists())
        self.assertFalse((ROOT/'analysis/Inputs').exists())
    def test_independent_quick(self):
        proc=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/quick_check.py')],capture_output=True,text=True,check=True)
        report=json.loads(proc.stdout)
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['checked_results'],65)
        self.assertEqual(report['operational_event_coverage'],3409)
    def test_manifest_provides_original_capture_hashes(self):
        manifest=json.loads((ROOT/'analysis/input_manifest.json').read_text())['files']
        self.assertEqual(len(manifest),2188)
        self.assertTrue(all(len(x['sha256'])==64 for x in manifest))

if __name__=='__main__':unittest.main()
