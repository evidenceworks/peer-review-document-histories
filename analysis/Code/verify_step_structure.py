#!/usr/bin/env python3
"""Separate raw-capture verification of native evaluation-step coverage."""
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROLES = {'review-article', 'evaluation-summary', 'reply'}
EVENTS = {'review', 'reviews-summary', 'referee-report', 'review-article'}


def doi(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value or '').strip(), flags=re.I).lower().rstrip('/')


def stamp(value):
    if not isinstance(value, str) or not re.search(r'[T ]\d{2}:\d{2}:\d{2}', value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def state(objects, found):
    recovered = len(set(objects) & set(found))
    return 'complete' if recovered == len(objects) else 'none' if recovered == 0 else 'partial'


def prefix(states):
    return bool(states) and 'partial' not in states and all(a >= b for a, b in zip([x == 'complete' for x in states], [x == 'complete' for x in states][1:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--comparison-results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    repository = args.repository.resolve()
    comparison = args.comparison_results.resolve()
    assert not output.exists(), 'Output must be a new directory'
    for protected in (repository, comparison):
        assert output != protected and protected not in output.parents and output not in protected.parents, 'Output overlaps an input'
    output.mkdir(parents=True)
    import csv
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    root = args.repository.resolve() / 'Evidence/Provenance'
    plan = read(root / 'raw_validation_plan.json')
    manifest = read(root / 'validation/capture_manifest.json')
    index = {(r['provider'], r['key']): r for r in manifest}
    assert len(index) == len(manifest) == 340
    accessed = set()
    def captured(provider, key):
        item = index[provider, key]
        data = (root / 'validation/raw' / provider / (key + '.json')).read_bytes()
        assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'] and item['status'] == 200
        accessed.add((provider, key))
        return json.loads(data), item
    objects, steps, families = [], [], []
    matched_rows = 0
    rows_seen = 0
    for sample in plan['sample']:
        family = sample['family']
        doc, capture = captured('elife', family)
        chain, node = [], doc['first-step']
        while node:
            assert node in doc['steps'] and node not in chain
            chain.append(node)
            node = doc['steps'][node].get('next-step')
        source = {}
        source_steps = {}
        for sid in chain:
            step = doc['steps'][sid]
            inputs = {doi(x['doi']) for x in step.get('inputs', []) if x.get('doi') and not doi(x['doi']).startswith('10.7554/elife.')}
            members = []
            for action in step.get('actions', []):
                for item in action.get('outputs', []):
                    if item.get('type') not in ROLES or not item.get('doi'):
                        continue
                    identifier = doi(item['doi'])
                    assert identifier not in source, ('Repeated output identity', family, identifier)
                    when = stamp(item.get('published'))
                    assert when is not None and inputs
                    source[identifier] = {'role': item['type'], 'time': when, 'inputs': inputs, 'step': sid}
                    members.append(identifier)
            if members:
                source_steps[sid] = members
        # No review output may be hidden outside the actual next-step traversal.
        for sid, step in doc['steps'].items():
            if any(x.get('type') in ROLES and x.get('doi') for a in step.get('actions', []) for x in a.get('outputs', [])):
                assert sid in chain
        eeb, queries = [], set()
        for number, original in enumerate(sample['original_preprint_dois']):
            wrappers, entry = captured('eeb', family + '_' + str(number))
            assert doi(entry['original_doi']) == doi(original)
            endpoint_doi = unquote(entry['url'].split('/docmap/', 1)[1])
            assert doi(endpoint_doi) == doi(original), 'Captured query route differs from declared input'
            queries.add(doi(endpoint_doi))
            for wrapper in wrappers:
                document = wrapper.get('docmap', wrapper)
                if document.get('publisher', {}).get('name', '').casefold() != 'elife':
                    continue
                for step in document.get('steps', {}).values():
                    inputs = {doi(x['doi']) for x in step.get('inputs', []) if x.get('doi')}
                    for action in step.get('actions', []):
                        for item in action.get('outputs', []):
                            if item.get('type') in EVENTS:
                                eeb.append((inputs, stamp(item.get('published'))))
        found = set()
        local_rows = 0
        for endpoint in sample['endpoints']:
            response, _ = captured('epmc', endpoint.replace('/', '_'))
            for row in response.get('evaluationList', {}).get('evaluation', []):
                if row.get('platform', '').casefold() != 'elife' or row.get('type') not in EVENTS:
                    continue
                local_rows += 1
                when, input_doi = stamp(row.get('evaluationDate')), doi(row.get('doi'))
                candidates = [identifier for identifier, item in source.items() if when is not None and item['time'] == when and input_doi in item['inputs']]
                if len(candidates) == 1:
                    found.add(candidates[0])
                    matched_rows += 1
        rows_seen += local_rows
        if not local_rows:
            continue
        assert sample['stratum'].startswith('review_')
        step_rows = []
        for number, (sid, members) in enumerate(source_steps.items(), 1):
            represented = {identifier for identifier in members if any(source[identifier]['time'] == when and source[identifier]['inputs'] & inputs for inputs, when in eeb)}
            rec = {'family': family, 'source_step': sid, 'step_rank': number, 'source_objects': len(members),
                   'coverage_state': state(members, found), 'EEB_coverage_state': state(members, represented),
                   'reconstructed_objects': len(set(members) & found), 'EEB_objects_with_counterpart': len(represented)}
            steps.append(rec); step_rows.append(rec)
        states = [r['coverage_state'] for r in step_rows]
        windows = [(min(source[x]['time'] for x in members), max(source[x]['time'] for x in members)) for members in source_steps.values()]
        ordered = all(a[1] < b[0] for a, b in zip(windows, windows[1:]))
        assert prefix(states) and ordered
        for identifier, item in source.items():
            if identifier not in found:
                assert item['inputs'] <= queries
            objects.append({'family': family, 'source_doi': identifier, 'role': item['role'], 'source_step': item['step'],
                            'reconstructed': identifier in found, 'EEB_present': any(item['time'] == when and item['inputs'] & inputs for inputs, when in eeb)})
        families.append({'family': family, 'sequence': states, 'source_objects': len(source), 'reconstructed_objects': len(found),
                         'prefix': prefix(states), 'time_order': ordered})
    assert len(accessed) == 340 and rows_seen == 462 and matched_rows == 458
    assert len(families) == 60 and len(objects) == 471 and sum(r['reconstructed'] for r in objects) == 362
    summary = Counter(r['coverage_state'] for r in steps)
    assert summary == {'complete': 91, 'none': 26} and len(steps) == 117
    absent = [r for r in steps if r['coverage_state'] == 'none']
    partition = Counter((r['EEB_coverage_state'], r['source_objects']) for r in absent)
    absent_summary = {status: {'steps': sum(r['EEB_coverage_state'] == status for r in absent),
                              'objects': sum(r['source_objects'] for r in absent if r['EEB_coverage_state'] == status)} for status in ['none', 'complete', 'partial']}
    assert absent_summary == {'none': {'steps': 20, 'objects': 84}, 'complete': {'steps': 6, 'objects': 25}, 'partial': {'steps': 0, 'objects': 0}}
    with (args.comparison_results / 'evaluation_step_coverage.csv').open(encoding='utf-8-sig', newline='') as stream:
        producer = {(r['family'], r['source_step']): r for r in csv.DictReader(stream)}
    assert len(producer) == len(steps)
    for row in steps:
        other = producer[row['family'], row['source_step']]
        for key, value in row.items():
            assert str(value) == other[key], (row['family'], key, value, other[key])
    # Pure perturbations exercise outcomes that the empirical data did not show.
    assert state({'a', 'b', 'c'}, {'a', 'b'}) == 'partial'
    assert not prefix(['complete', 'none', 'complete'])
    assert not prefix(['complete', 'partial', 'none'])
    assert stamp('2026-10-24') is None
    result = {'status': 'PASS_INDEPENDENT_RAW_STEP_VERIFICATION', 'raw_captures_verified': len(accessed),
        'positive_sample_families': len(families), 'endpoint_rows': rows_seen, 'matched_endpoint_rows': matched_rows,
        'source_objects': len(objects), 'reconstructed_source_objects': sum(r['reconstructed'] for r in objects),
        'native_evaluation_steps': len(steps), 'step_coverage': dict(summary), 'missing_step_EEB_partition': absent_summary,
        'all_missing_input_DOIs_verified_against_actual_captured_query_URLs': True,
        'incomplete_families_with_strict_initial_prefix': sum(r['source_objects'] != r['reconstructed_objects'] for r in families),
        'all_step_orders_agree_with_strict_source_time_intervals': True,
        'synthetic_controls': ['partial_step_detected', 'nonprefix_hole_detected', 'partial_step_not_prefix', 'date_only_abstention'],
        'producer_module_imported': False, 'frozen_raw_evidence_modified': False,
        'scope': '60 review-event-positive sampled families, within the fixed 120-family stratified sample; no unweighted population extrapolation',
        'interpretation': 'Missing counterpart refers to the declared input-DOI/full-timestamp match in captured responses. Native publisher steps are not asserted to be independent review rounds.'}
    (output / 'step_structure_independent_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
