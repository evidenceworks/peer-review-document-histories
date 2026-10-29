#!/usr/bin/env python3
"""Recalculate the study measures from the released derived records."""

import csv
import io
import json
import platform
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'analysis' / 'FigureProduction' / 'Data'
RESULTS = ROOT / 'analysis' / 'Results'


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream, strict=True))


def records(name):
    return read_csv(DATA / name)


def count(rows, key):
    return sum(int(row[key]) for row in rows)


class Analysis:
    def __init__(self):
        self.measures = []
        self.checks = []
        self.tables = {}

    def measure(self, label, numerator, denominator='', scope=''):
        rate = '' if denominator == '' else f'{100 * numerator / denominator:.2f}'
        self.measures.append(dict(measure=label, count=numerator, denominator=denominator,
                                  percent=rate, scope=scope))
        return numerator

    def check(self, label, observed, reference):
        if observed != reference:
            raise AssertionError(f'{label}: observed {observed!r}; reference {reference!r}')
        self.checks.append(dict(check=label, observed=str(observed), reference=str(reference), result='PASS'))

    def table(self, name, rows):
        if not rows:
            raise AssertionError('No records for ' + name)
        self.tables[name] = rows


def quick():
    a = Analysis()
    whole = [r for r in records('Families.csv') if r['exact_MED'] == '1']
    a.check('Canonical MED population', len(whole), 3547)
    a.check('Unique MED families', len({r['family'] for r in whole}), 3547)
    a.measure('Canonical MED families', len(whole), scope='MED population')
    for key, label, expected in [
        ('native_review', 'Native review-event coverage', 2312),
        ('enriched_review', 'Enriched review-event coverage', 2900),
        ('admissible_EEB_fallback', 'Eligible EEB fallback families', 509),
        ('combined_event_presence', 'Combined review-event coverage', 3409),
    ]:
        value = count(whole, key)
        a.check(label, value, expected)
        a.measure(label, value, 3547, 'MED population')
    a.check('Combined presence is at least native presence',
            all(int(r['combined_event_presence']) >= int(r['native_review']) for r in whole), True)

    residual = records('ResidualCensus.csv')
    a.check('Residual families', len(residual), 647)
    a.check('Residual family identifiers unique', len({r['family'] for r in residual}), 647)
    a.measure('Residual families', len(residual), scope='Residual cohort')
    for key, label, expected in [
        ('upstream_review', 'Residual families with upstream review', 512),
        ('eligible_declared_EEB_fallback', 'Residual families eligible for EEB fallback', 509),
    ]:
        observed = count(residual, key)
        a.check(label, observed, expected)
        a.measure(label, observed, 647, 'Residual cohort')

    families = records('family_coverage_structure.csv')
    a.check('Matched-document sample families', len(families), 60)
    a.check('Sample family identifiers unique', len({r['family'] for r in families}), 60)
    strata = Counter(r['stratum'] for r in families)
    a.check('Fixed sample allocation', strata,
            Counter({'review_no_url': 20, 'review_with_DOI_url': 20, 'review_with_generic_url': 20}))
    a.measure('Sample families', len(families), scope='Fixed stratified sample')
    for key, label, expected in [
        ('endpoint_rows', 'Eligible endpoint rows', 462),
        ('matched_rows', 'Matched endpoint rows', 458),
        ('source_objects', 'Source documents', 471),
        ('reconstructed_objects', 'Uniquely reconstructed source documents', 362),
        ('all_source_roles_complete', 'Families with complete document histories', 37),
    ]:
        observed = count(families, key)
        a.check(label, observed, expected)
        denominator = 60 if label.startswith('Families with') else 471 if label.startswith('Uniquely reconstructed') else ''
        a.measure(label, observed, denominator, 'Fixed stratified sample')

    steps = records('evaluation_step_coverage.csv')
    states = Counter(r['coverage_state'] for r in steps)
    a.check('Evaluation-step coverage', states, Counter({'complete': 91, 'none': 26}))
    a.check('No partially reconstructed steps', 'partial' in states, False)
    a.measure('Complete evaluation steps', states['complete'], len(steps), 'Fixed stratified sample')
    a.measure('Unrepresented evaluation steps', states['none'], len(steps), 'Fixed stratified sample')

    objects = records('source_object_structure.csv')
    a.check('Source-object count independently from ledger', len(objects), 471)
    a.check('Unique source document DOIs', len({r['source_doi'] for r in objects}), 471)
    a.check('Reconstructed objects independently from ledger', count(objects, 'endpoint_unique_match'), 362)
    a.check('All source documents assigned to sampled families',
            {r['family'] for r in objects}, {r['family'] for r in families})
    return a


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def precision_key(moment, mode):
    if mode == 'seconds':
        return moment.replace(microsecond=0)
    if mode == 'day':
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return moment


def core():
    a = quick()
    families = records('family_coverage_structure.csv')
    objects = records('source_object_structure.csv')
    steps = records('evaluation_step_coverage.csv')
    precision = records('endpoint_precision_reconstruction.csv')

    by_step = defaultdict(list)
    for row in objects:
        by_step[row['family'], int(row['step_rank'])].append(row)
    a.check('Source steps independently grouped by family and position', len(by_step), 117)
    states = Counter()
    eeb_states = Counter()
    for row in steps:
        key = (row['family'], int(row['step_rank']))
        members = by_step[key]
        a.check('Source objects in step ' + str(key), len(members), int(row['source_objects']))
        recovered = sum(int(m['endpoint_unique_match']) for m in members)
        a.check('Reconstructed objects in step ' + str(key), recovered,
                int(row['reconstructed_objects']))
        state = 'complete' if recovered == len(members) else 'none' if recovered == 0 else 'partial'
        a.check('Coverage of step ' + str(key), state, row['coverage_state'])
        states[state] += 1
        if state == 'none':
            eeb_states['EEB present' if all(int(m['EEB_counterparts']) > 0 for m in members) else 'EEB absent'] += 1
    a.check('Recomputed complete and absent steps', states, Counter({'complete': 91, 'none': 26}))
    a.check('Missing steps with EEB counterparts', eeb_states,
            Counter({'EEB present': 6, 'EEB absent': 20}))

    families_by_stratum = defaultdict(list)
    for f in families:
        families_by_stratum[f['stratum']].append(f)
    strata = []
    for stratum in sorted(families_by_stratum):
        rows = families_by_stratum[stratum]
        members = [o for o in objects if o['stratum'] == stratum]
        these_steps = [s for s in steps if s['stratum'] == stratum]
        total = len(members)
        recovered = count(members, 'endpoint_unique_match')
        complete = sum(s['coverage_state'] == 'complete' for s in these_steps)
        complete_families = sum(int(f['all_source_roles_complete']) for f in rows)
        a.check('Twenty sample families in ' + stratum, len(rows), 20)
        a.check('Source-object totals for ' + stratum,
                (total, recovered), (count(rows, 'source_objects'), count(rows, 'reconstructed_objects')))
        a.check('Step totals for ' + stratum,
                (len(these_steps), complete), (count(rows, 'evaluation_steps'), count(rows, 'complete_steps')))
        strata.append(dict(stratum=stratum, sample_families=len(rows),
                           population_families=rows[0]['stratum_N'], source_documents=total,
                           reconstructed_documents=recovered, complete_families=complete_families,
                           evaluation_steps=len(these_steps), complete_steps=complete))
    a.table('coverage_by_stratum.csv', strata)

    roles = []
    for role in ('review-article', 'evaluation-summary', 'reply'):
        rr = [x for x in objects if x['source_role'] == role]
        source_n = len(rr)
        retrieved_n = count(rr, 'endpoint_unique_match')
        roles.append(dict(role=role, source_documents=source_n,
                          reconstructed_documents=retrieved_n,
                          not_reconstructed=source_n - retrieved_n))
    a.check('Role totals', [(r['source_documents'], r['reconstructed_documents']) for r in roles],
            [(269, 212), (117, 91), (85, 59)])
    a.table('coverage_by_role.csv', roles)

    # Recompute timestamp candidates from the original source times and DOI bindings.
    modes = ('microseconds', 'seconds', 'day')
    index = {mode: defaultdict(set) for mode in modes}
    for row in objects:
        moment = utc(row['source_time_utc'])
        for binding in json.loads(row['input_bindings']):
            for mode in modes:
                key = row['family'], binding[0].lower(), precision_key(moment, mode)
                index[mode][key].add(row['source_doi'])
    a.check('Timestamp rows', len(precision), 462)
    a.check('Originally matched timestamp rows', sum(bool(r['source_doi']) for r in precision), 458)
    timestamp_table = []
    for mode, field in [('microseconds', 'original_candidates'),
                        ('seconds', 'seconds_candidates'), ('day', 'day_candidates')]:
        n_unique = n_ambiguous = n_absent = n_retained = 0
        for row in precision:
            key = row['family'], row['input_doi'].lower(), precision_key(utc(row['original_timestamp']), mode)
            candidates = index[mode].get(key, set())
            a.check('Candidate set for ' + mode + ' / ' + row['epmc_id'] + ' / ' + row['epmc_record'],
                    sorted(candidates), sorted(json.loads(row[field])))
            n_unique += len(candidates) == 1
            n_ambiguous += len(candidates) > 1
            n_absent += len(candidates) == 0
            n_retained += bool(row['source_doi']) and candidates == {row['source_doi']}
        a.check('Candidate classification for ' + mode,
                (n_unique, n_ambiguous, n_absent, n_retained),
                {'microseconds': (458, 0, 4, 458),
                 'seconds': (274, 185, 3, 273),
                 'day': (5, 454, 3, 5)}[mode])
        timestamp_table.append(dict(timestamp_precision=mode, endpoint_rows=len(precision),
                                    unique_candidates=n_unique, ambiguous_candidates=n_ambiguous,
                                    no_candidate=n_absent, original_matches_retained=n_retained))
    a.table('timestamp_precision.csv', timestamp_table)

    current = read_csv(RESULTS / 'SourceAuditIntegration' / 'current_comparators.csv')
    a.check('Current-version comparison rows', len(current), 3550)
    comparison_rows = []
    for key, label, expected in [
        ('latest_dated_status', 'Latest-dated', {'concordant': 3533, 'discordant': 7, 'abstained': 10}),
        ('forward_typed_status', 'Forward-typed', {'concordant': 3528, 'discordant': 1, 'abstained': 21}),
    ]:
        observed = Counter(r[key] for r in current)
        a.check(label + ' comparator', observed, Counter(expected))
        for status in ('concordant', 'discordant', 'abstained'):
            comparison_rows.append(dict(comparison=label, status=status,
                                        count=observed[status], denominator=len(current)))
    a.table('version_comparison.csv', comparison_rows)

    publisher = read_csv(RESULTS / 'Provenance' / 'publisher_comparator.csv')
    a.check('Publisher comparison sample', len(publisher), 120)
    a.check('Publisher version DOI concordance', count(publisher, 'doi_concordance'), 118)
    a.check('Publisher comparison abstentions', count(publisher, 'abstained'), 2)
    a.measure('Publisher DOI concordance', 118, 120, 'Publisher comparator sample')
    a.measure('Publisher comparison abstentions', 2, 120, 'Publisher comparator sample')
    a.measure('Matched source objects', 362, 471, 'Fixed stratified sample')
    a.measure('Reconstructed evaluation steps', 91, 117, 'Fixed stratified sample')
    a.measure('Second-precision original matches retained', 273, 458, 'Matched endpoint rows')
    a.measure('Day-precision original matches retained', 5, 458, 'Matched endpoint rows')
    return a


def csv_text(rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def output_files(analysis, mode):
    all_files = {'results.csv': csv_text(analysis.measures), 'checks.csv': csv_text(analysis.checks)}
    all_files.update({name: csv_text(rows) for name, rows in analysis.tables.items()})
    intro = [
        'Public peer review: document histories',
        'Reproduction from the released derived records',
        '',
        f'{mode} analysis: PASS',
        f'Scientific checks: {len(analysis.checks)} passed',
        'MED population: 3,547 families; combined review-event coverage: 3,409.',
        'Fixed stratified sample: 60 families; 471 publisher source documents.',
        'Uniquely reconstructed documents: 362; reconstructed evaluation steps: 91 of 117.',
    ]
    if mode == 'Core':
        intro += [
            'Timestamp ablation: 458 original matches retained at microsecond precision;',
            '273 at whole-second precision and 5 at calendar-day precision.',
            'The sample consists of 20 families from each of three strata.',
        ]
    intro += ['', 'The public analysis uses derived records. Original source capture replay requires the separately held source archive.']
    all_files['summary.txt'] = '\n'.join(intro) + '\n'
    all_files['run_info.txt'] = (f'Analysis: {mode}\nPython: {platform.python_version()}\n'
                                 'Inputs: public derived records\nRandom sampling: none\n'
                                 'Reference: published study results\n')
    return all_files


def run(mode='Quick', output=None):
    analysis = quick() if mode == 'Quick' else core() if mode == 'Core' else None
    if analysis is None:
        raise ValueError('Expected Quick or Core')
    if output is not None:
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
            for name, contents in output_files(analysis, mode).items():
                archive.writestr(name, contents.encode('utf-8'))
        print(f'{mode}: PASS | {len(analysis.checks)} scientific checks | {path.name}')
    else:
        print(f'{mode}: PASS | {len(analysis.checks)} scientific checks')
    return analysis
