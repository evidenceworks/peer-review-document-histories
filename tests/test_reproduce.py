"""Numerical, matching, integrity and observed-data regression tests."""
from decimal import Decimal
from fractions import Fraction
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import hashlib
import math
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location('reproduce', ROOT / 'scripts/reproduce.py')
r = module_from_spec(spec)
spec.loader.exec_module(r)


class MatchingTests(unittest.TestCase):
    def test_timestamp_offsets_and_fractional_precision(self):
        a = r.time_key('2026-11-10T12:34:56.123456789Z')
        self.assertEqual(a, r.time_key('2026-11-10T14:34:56.123456789+02:00'))
        self.assertEqual(a, r.time_key('2026-11-10 12:34:56.123456789'))
        self.assertEqual(a[1], Decimal('0.123456789'))
        self.assertNotEqual(a, r.time_key('2026-11-10T12:34:56.123456788Z'))
        self.assertEqual(r.time_key('2026-11-10T01:00:00+02:00', 'days'),
                         r.time_key('2026-11-09T23:00:00Z', 'days'))

    def test_ineligible_and_invalid_times_are_rejected(self):
        for value in ['', '2026-11-10', '2026-11-10T10:30', '2026-13-10T10:30:00Z']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                r.time_key(value)
        with self.assertRaises(ValueError):
            r.time_key('2026-11-10T10:30:00Z', 'unknown')

    def test_doi_normalization(self):
        for value in ['10.7554/ELIFE.100000', ' DOI: 10.7554/elife.100000 ',
                      'https://doi.org/10.7554%2Felife.100000',
                      'http://dx.doi.org/10.7554/elife.100000']:
            self.assertEqual(r.normalize_doi(value), '10.7554/elife.100000')
        with self.assertRaises(ValueError):
            r.normalize_doi('not-a-doi')

    def test_binary_values_do_not_silently_coerce_missingness(self):
        for value in ['True', 1, True, ' true ']:
            self.assertTrue(r.positive(value))
        for value in ['False', 0, False]:
            self.assertFalse(r.positive(value))
        for value in ['', None, 'yes', 2]:
            with self.assertRaises(ValueError):
                r.positive(value)

    def test_history_classification_allows_unexpected_patterns(self):
        cases = [(['complete'], 'complete'),
                 (['unrepresented'], 'empty'),
                 (['complete', 'unrepresented'], 'incomplete_nonempty_prefix'),
                 (['unrepresented', 'complete'], 'incomplete_nonprefix'),
                 (['complete', 'partial'], 'incomplete_with_partial_steps')]
        for values, expected in cases:
            self.assertEqual(r.history_pattern(values), expected)
        with self.assertRaises(ValueError):
            r.history_pattern([])


class IntervalTests(unittest.TestCase):
    def test_reported_exact_count_bounds(self):
        self.assertEqual(r.count_interval(2829, 150, 112), (1900, 2298))
        self.assertEqual(r.count_interval(2829, 150, 127), (2209, 2543))
        self.assertEqual(r.count_interval(2829, 150, 15), (165, 446))
        self.assertEqual(r.count_interval(397, 60, 60), (376, 397))

    def test_boundary_samples(self):
        self.assertEqual(r.count_interval(10, 0, 0), (0, 10))
        self.assertEqual(r.count_interval(10, 10, 4), (4, 4))
        self.assertEqual(r.count_interval(0, 0, 0), (0, 0))
        with self.assertRaises(ValueError):
            r.estimate(10, 10, 0, 0, 0)

    def test_invalid_inputs(self):
        for values in [(10, 11, 4), (10, 5, 6), (-1, 0, 0), (10, 5, -1)]:
            with self.assertRaises(ValueError):
                r.count_interval(*values)
        with self.assertRaises(TypeError):
            r.count_interval(10.0, 5, 2)
        with self.assertRaises(ValueError):
            r.count_interval(10, 5, 2, 2)

    def test_exact_finite_population_coverage(self):
        # Exhaustively integrate over every possible sample outcome in small frames.
        for population in range(1, 13):
            for sample in range(population + 1):
                denominator = math.comb(population, sample)
                for positives in range(population + 1):
                    covered = 0
                    for observed in range(max(0, sample-population+positives), min(sample, positives)+1):
                        low, high = r.count_interval(population, sample, observed)
                        if low <= positives <= high:
                            covered += math.comb(positives, observed)*math.comb(population-positives, sample-observed)
                    self.assertGreaterEqual(Fraction(covered, denominator), Fraction(95, 100))


class IntegrityTests(unittest.TestCase):
    def test_snapshot_manifest(self):
        self.assertEqual(r.verify_inputs(), 15)

    def test_modified_data_are_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'example.csv').write_bytes(b'value\n1\n')
            digest = hashlib.sha256((root/'example.csv').read_bytes()).hexdigest()
            (root/'manifest.sha256').write_text(f'{digest}  example.csv\n')
            self.assertEqual(r.verify_inputs(root), 1)
            (root/'example.csv').write_bytes(b'value\n0\n')
            with self.assertRaises(ValueError):
                r.verify_inputs(root)

    def test_explicit_validation(self):
        with self.assertRaises(ValueError):
            r.require(False, 'deliberately inconsistent data')


class EvidenceTests(unittest.TestCase):
    @staticmethod
    def attachment(**changes):
        row = {'filename': 'report.pdf', 'source_pdf_sha256': 'a'*64,
               'archive_pdf_sha256': 'a'*64, 'source_bytes': '500',
               'inventory_match': 'true', 'bytes_equal': 'true'}
        row.update(changes)
        return row

    def test_inventory_mismatch_prevents_complete_preservation(self):
        self.assertTrue(r.preserved_attachment_set([self.attachment()]))
        self.assertFalse(r.preserved_attachment_set([self.attachment(inventory_match='false')]))

    def test_different_files_are_not_preserved(self):
        self.assertFalse(r.preserved_attachment_set([
            self.attachment(archive_pdf_sha256='b'*64, bytes_equal='false')]))

    def test_inconsistent_attachment_evidence_is_rejected(self):
        for group in ([], [self.attachment(), self.attachment()],
                      [self.attachment(archive_pdf_sha256='b'*64)],
                      [self.attachment(source_bytes='0')]):
            with self.subTest(group=group), self.assertRaises(ValueError):
                r.preserved_attachment_set(group)

    def test_recorded_text_identity_results(self):
        result = r.text_identity(r.records('elife_documents.csv'),
                                 r.records('elife_text_identity.csv'))
        self.assertEqual(result['text_bearing_source_objects'], 1614)
        self.assertEqual(result['within_family_duplicate_text_groups'], 23)
        self.assertEqual(result['same_DOI_multiple_texts'], 86)
        self.assertEqual(result['later_steps_with_at_least_one_new_stable_text'], 177)
        self.assertEqual(result['recovered_steps_with_new_stable_text_vs_endpoint'], 43)

    def test_missing_or_duplicate_text_records_are_rejected(self):
        docs = r.records('elife_documents.csv'); rows = r.records('elife_text_identity.csv')
        with self.assertRaises(ValueError):
            r.text_identity(docs, rows + rows[:1])
        removed_doi = rows[0]['source_doi']
        with self.assertRaises(ValueError):
            r.text_identity(docs, [x for x in rows if x['source_doi'] != removed_doi])


if __name__ == '__main__':
    unittest.main()
