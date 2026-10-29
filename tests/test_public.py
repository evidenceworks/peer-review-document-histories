"""Tests for the released numerical reproduction."""
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_public_data_and_licenses(self):
        self.assertTrue((ROOT / 'LICENSE').is_file())
        self.assertTrue((ROOT / 'licenses' / 'CC-BY-4.0.txt').is_file())
        self.assertFalse((ROOT / 'analysis' / 'Inputs').exists())
        self.assertFalse((ROOT / 'analysis' / 'Evidence').exists())
        self.assertFalse(any(p.suffix.lower() in ('.docx', '.xlsx', '.pdf') for p in ROOT.rglob('*')))
        self.assertEqual(sum(1 for p in (ROOT / 'analysis' / 'Results').rglob('*') if p.is_file()), 65)

    def test_numerical_checks(self):
        for mode, expected in [('quick', 25), ('core', 1786)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / ('aOIR_' + mode + '.zip')
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / (mode + '_check.py')),
                                         '--output', str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('PASS', result.stdout)
                with ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    names = set(archive.namelist())
                    self.assertIn('results.csv', names)
                    self.assertIn('checks.csv', names)
                    self.assertIn('summary.txt', names)
                    self.assertTrue(all(name.endswith(('.csv', '.txt')) for name in names))
                    rows = list(csv.DictReader(io.StringIO(archive.read('checks.csv').decode())))
                    self.assertEqual(len(rows), expected)
                    self.assertEqual({r['result'] for r in rows}, {'PASS'})
                    self.assertIn('3,547', archive.read('summary.txt').decode())
                    self.assertTrue(any(r['measure'] == 'Combined review-event coverage'
                                        for r in csv.DictReader(io.StringIO(archive.read('results.csv').decode()))))
                    if mode == 'core':
                        self.assertIn('timestamp_precision.csv', names)
                        self.assertIn('coverage_by_role.csv', names)
                        self.assertIn('coverage_by_stratum.csv', names)
                        self.assertIn('version_comparison.csv', names)

    def test_notebook_sources(self):
        for mode in ('quick', 'core'):
            notebook = json.loads((ROOT / 'notebooks' / (mode + '.ipynb')).read_text())
            self.assertEqual(notebook['nbformat'], 4)
            self.assertEqual(len([c for c in notebook['cells'] if c['cell_type'] == 'code']), 3)
            self.assertTrue(all(not c.get('outputs') for c in notebook['cells']))
            self.assertIn('files.download', '\n'.join(''.join(c['source']) for c in notebook['cells']))


if __name__ == '__main__':
    unittest.main()
