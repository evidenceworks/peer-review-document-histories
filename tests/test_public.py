"""Tests for the public study reproduction."""

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
        self.assertFalse(any(p.suffix.lower() in ('.docx', '.xlsx', '.pdf')
                             for p in ROOT.rglob('*')))
        self.assertEqual(sum(1 for p in (ROOT / 'analysis' / 'Results').rglob('*')
                             if p.is_file()), 65)

    def test_reproduction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'document_history_results.zip'
            command = [sys.executable, '-B', str(ROOT / 'scripts' / 'reproduce.py'),
                       '--output', str(path)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Checks passed: 1786 of 1786', result.stdout)
            with ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                names = set(archive.namelist())
                self.assertEqual(names, {
                    'results.csv', 'checks.csv', 'coverage_by_stratum.csv',
                    'coverage_by_role.csv', 'timestamp_precision.csv',
                    'version_comparison.csv', 'summary.txt', 'run_info.txt'})
                checks = list(csv.DictReader(io.StringIO(archive.read('checks.csv').decode())))
                self.assertEqual(len(checks), 1786)
                self.assertEqual({row['result'] for row in checks}, {'PASS'})
                results = list(csv.DictReader(io.StringIO(archive.read('results.csv').decode())))
                self.assertTrue(any(r['measure'] == 'Combined review-event coverage'
                                    and r['count'] == '3409' for r in results))
                self.assertIn('3,547', archive.read('summary.txt').decode())

    def test_notebook(self):
        files = list((ROOT / 'notebooks').glob('*.ipynb'))
        self.assertEqual([p.name for p in files], ['reproduce.ipynb'])
        notebook = json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual(notebook['nbformat'], 4)
        self.assertEqual(len([c for c in notebook['cells'] if c['cell_type'] == 'code']), 3)
        self.assertTrue(all(not c.get('outputs') for c in notebook['cells']))
        sources = '\n'.join(''.join(c['source']) for c in notebook['cells'])
        self.assertIn('files.download', sources)
        self.assertIn('document_history_results.zip', sources)
        self.assertIn('notebooks/reproduce.ipynb', (ROOT / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
