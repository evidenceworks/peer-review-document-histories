#!/usr/bin/env python3
"""Independent direct latest reviewed-preprint/current VOR comparison.

Reads two retained projections, without importing either producing module.
This is a derived-data check, not a repeat extraction of the XML corpus.
"""
import argparse
from collections import defaultdict
from datetime import date
import csv
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
if sys.flags.optimize:
    raise RuntimeError('Run without optimized Python: checks must remain active')

CUTOFF = date(2026, 10, 24)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(path):
    states = json.loads(path.read_text(encoding='utf-8-sig'))
    assert len(states) == 13230
    assert len({r['filename'] for r in states}) == len(states)
    families = defaultdict(list)
    for row in states:
        if date.fromisoformat(row['timing_date']) <= CUTOFF:
            families[row['family']].append(row)
    records = {}
    for family, rows in sorted(families.items()):
        vors = [r for r in rows if r['role'] == 'article'
                and r['verified_state'] == 'version of record']
        if not vors:
            continue
        rps = [r for r in rows if r['role'] == 'preprint']
        assert rps, family
        selected = []
        for candidates in (vors, rps):
            maximum = max((date.fromisoformat(r['timing_date']), r['file_version'])
                          for r in candidates)
            latest = [r for r in candidates
                      if (date.fromisoformat(r['timing_date']), r['file_version']) == maximum]
            assert len(latest) == 1, (family, 'Unresolved temporal/production tie')
            selected.append(latest[0])
        vor, rp = selected
        assert vor['labels'] is not None and rp['labels'] is not None, family
        records[family] = {
            'family': family,
            'latest_preprint_file': rp['filename'],
            'latest_preprint_source_sha256': rp['sha256'],
            'latest_preprint_timing_date': rp['timing_date'],
            'latest_preprint_labels': sorted(set(rp['labels'])),
            'latest_preprint_group': rp['group'],
            'current_vor_file': vor['filename'],
            'current_vor_source_sha256': vor['sha256'],
            'current_vor_timing_date': vor['timing_date'],
            'current_vor_labels': sorted(set(vor['labels'])),
            'current_vor_group': vor['group'],
            'strength_set_difference': set(rp['labels']) != set(vor['labels']),
            'group_difference': rp['group'] != vor['group'],
        }
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repository, output = args.repository.resolve(), args.output.resolve()
    assert not output.exists(), 'Output must be a new directory'
    assert output != repository and repository not in output.parents and output not in repository.parents, 'Output overlaps the repository'
    paths = {
        'before': repository / 'Inputs/records/Results/source_states.json',
        'after': repository / 'Evidence/SourceAudit/corrected_source_states.json',
    }
    hashes = {name: digest(path) for name, path in paths.items()}
    versions = {name: evaluate(path) for name, path in paths.items()}
    assert set(versions['before']) == set(versions['after'])
    assert len(versions['after']) == 3550
    rows, summary = [], {}
    for name, values in versions.items():
        summary[name] = {'families': len(values), **{
            metric: sum(row[metric] for row in values.values())
            for metric in ('strength_set_difference', 'group_difference')}}
    for family in versions['after']:
        old, new = versions['before'][family], versions['after'][family]
        assert old['latest_preprint_file'] == new['latest_preprint_file']
        assert old['current_vor_file'] == new['current_vor_file']
        record = {'family': family}
        for name, value in [('before', old), ('after', new)]:
            record.update({name + '_' + k: v for k, v in value.items() if k != 'family'})
        record['strength_comparison_changed'] = old['strength_set_difference'] != new['strength_set_difference']
        record['group_comparison_changed'] = old['group_difference'] != new['group_difference']
        rows.append(record)
    changes = [r for r in rows if r['strength_comparison_changed'] or r['group_comparison_changed']]
    assert summary['before'] == {'families': 3550, 'strength_set_difference': 38, 'group_difference': 29}
    assert summary['after'] == {'families': 3550, 'strength_set_difference': 37, 'group_difference': 28}
    assert [r['family'] for r in changes] == ['93656']
    assert changes[0]['before_latest_preprint_labels'] == []
    assert changes[0]['after_latest_preprint_labels'] == ['compelling']
    assert changes[0]['after_current_vor_labels'] == ['compelling']
    assert versions['after']['90046']['latest_preprint_labels'] == ['incomplete', 'solid']
    assert versions['after']['90046']['current_vor_labels'] == ['solid']
    assert hashes == {name: digest(path) for name, path in paths.items()}
    output.mkdir(parents=True)
    destination = output / 'latest_preprint_vor_before_after.csv'
    with destination.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, list) else value
                             for key, value in row.items()})
    receipt = {
        'status': 'PASS_INDEPENDENT_DERIVED_COMPARISON',
        'scope': 'Direct latest reviewed-preprint versus latest verified VOR at 2026-10-24',
        'raw_xml_reextraction': False,
        'selection': 'Maximum timing_date, then file_version within each role; ties rejected; all current families retained',
        'code_sha256': digest(Path(__file__).resolve()),
        'input_sha256': hashes,
        'results': summary,
        'comparison_changed_families': ['93656'],
        'changed_records': changes,
        'retained_mixed_label_case': versions['after']['90046'],
        'distinction': 'This direct last-preprint task differs from nearest prior assessment (36 differences), which excludes the current scientific version',
        'output': {'path': destination.name, 'rows': len(rows), 'sha256': digest(destination)},
        'inputs_unchanged': True,
    }
    (output / 'latest_preprint_vor_independent_receipt.json').write_text(
        json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': receipt['status'], 'results': summary, 'changed_families': ['93656']}))


if __name__ == '__main__':
    main()
