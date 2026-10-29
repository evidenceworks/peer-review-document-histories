#!/usr/bin/env python3
"""Verify retained source-audit evidence and recompute task-specific consequences.

This program reads derived ledgers and projections. It does not repeat the
separately executed extraction of the original full XML/API corpus.
"""
import sys
sys.dont_write_bytecode = True
if sys.flags.optimize:
    raise RuntimeError('Optimized Python disables required validation checks; run without -O and PYTHONOPTIMIZE')
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
import hashlib
import html
import json
import os
from pathlib import Path
import re

CUTOFF = date(2026, 10, 24)
LEVELS = ('inadequate', 'incomplete', 'solid', 'convincing', 'compelling', 'exceptional')
SOURCE_FIELDS = ('filename', 'role', 'family', 'file_version', 'doi', 'version_doi', 'release_date',
                 'timing_date', 'publication_state', 'verified_state', 'labels', 'group', 'subjects',
                 'article_type', 'title', 'sha256', 'git_blob', 'bytes', 'flags', 'history_events',
                 'assessment_present', 'assessment_doi', 'assessment_candidates')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        yield from csv.DictReader(stream)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def write_csv(path, rows, empty=('status',)):
    fields = list(dict.fromkeys(key for row in rows for key in row)) or list(empty)
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def norm(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value or '').strip(), flags=re.I).lower().rstrip('/')


def rank(row):
    return row['timing_date'], row['file_version'], row['role'] == 'article', row['filename']


def equivalent(field, value):
    if field in {'doi', 'version_doi', 'assessment_doi', 'elife_doi', 'initial_version_doi'}:
        return norm(value)
    if field in {'labels', 'subjects', 'original_preprint_dois'} and isinstance(value, list):
        return sorted(value)
    return value


def compare(left, right, key, fields=None):
    old, new = {row[key]: row for row in left}, {row[key]: row for row in right}
    assert len(old) == len(left) and len(new) == len(right), 'Nonunique comparison key'
    differences, checks = [], 0
    for identifier in sorted(set(old) | set(new)):
        before, after = old.get(identifier), new.get(identifier)
        if before is None or after is None:
            differences.append({'key': identifier, 'field': 'record_presence', 'before': before, 'after': after})
            continue
        for field in fields or sorted(set(before) | set(after)):
            checks += 1
            if field not in before or field not in after or equivalent(field, before[field]) != equivalent(field, after[field]):
                differences.append({'key': identifier, 'field': field, 'before': before.get(field), 'after': after.get(field)})
    return differences, checks


def derive_targets(states):
    families, history, history_rank = defaultdict(list), {}, {}
    for row in states:
        families[row['family']].append(row)
        if row['assessment_doi'] and row['labels'] is not None:
            identifier = norm(row['assessment_doi'])
            if identifier not in history or rank(row) > history_rank[identifier]:
                history[identifier] = {'family': row['family'], 'version_doi': norm(row['version_doi']),
                    'assessment_doi': identifier, 'labels': row['labels'], 'source_file': row['filename'],
                    'sha256': row['sha256'], 'timing_date': row['timing_date']}
                history_rank[identifier] = rank(row)
    current, originals = [], []
    for family, rows in sorted(families.items()):
        initial = [r for r in rows if r['role'] == 'preprint' and r['file_version'] == 1]
        assert len(initial) == 1
        first = initial[0]
        external = {norm(u['href']) for event in first['history_events'] if event['type'] == 'preprint'
                    for u in event['uris'] if norm(u['href']).startswith('10.') and not norm(u['href']).startswith('10.7554/')}
        originals.append({'family': family, 'elife_doi': norm(first['doi']),
            'initial_version_doi': norm(first['version_doi']), 'original_preprint_dois': sorted(external),
            'baseline_file': first['filename'], 'baseline_sha256': first['sha256']})
        possible = [r for r in rows if r['role'] == 'article' and r['verified_state'] == 'version of record'
                    and r['timing_date'] <= CUTOFF.isoformat()]
        if not possible:
            continue
        maximum = max((r['timing_date'], r['file_version']) for r in possible)
        latest = [r for r in possible if (r['timing_date'], r['file_version']) == maximum]
        assert len(latest) == 1, (family, 'Current production-order tie')
        row = latest[0]
        current.append({'family': family, 'doi': norm(row['doi']), 'version_doi': norm(row['version_doi']),
            'assessment_doi': norm(row['assessment_doi']), 'source_file': row['filename'], 'source_sha256': row['sha256'],
            'labels': row['labels'], 'group': row['group'], 'timing_date': row['timing_date']})
    return current, history, originals, families


def edges(record, relation):
    return {norm(x['id']) for x in record.get('relation', {}).get(relation, [])
            if x.get('id-type') == 'doi' or 'doi.org/' in x.get('id', '')}


def consensus(record):
    title = html.unescape(re.sub(r'<[^>]+>', '', ' '.join(record.get('title', []))))
    return record.get('type') == 'peer-review' and ' '.join(title.split()).casefold().startswith('elife assessment')


def expand(start, relations, parents, family):
    pool, visited = set(start), set()
    pattern = re.compile(r'10\.7554/elife\.' + re.escape(family) + r'(?:\.\d+)?$')
    while pool - visited:
        identifier = min(pool - visited)
        visited.add(identifier)
        pool |= {d for kind in relations for d in edges(parents.get(identifier, {}), kind) if pattern.fullmatch(d)}
    return pool


def current_comparators(current, history, parents, reviews):
    reverse = defaultdict(set)
    for identifier, review in reviews.items():
        for parent in edges(review, 'is-review-of'):
            reverse[parent].add(identifier)
    rows = []
    for target in current:
        family = target['family']
        same = expand({target['doi']}, ('is-same-as', 'is-identical-to'), parents, family)
        forward = {d for parent in same for d in edges(parents.get(parent, {}), 'has-review')}
        typed = {d for d in forward if d in reviews and consensus(reviews[d])
                 and edges(reviews[d], 'is-review-of') and edges(reviews[d], 'is-review-of') <= same}
        typed_choice = next(iter(typed)) if len(typed) == 1 else ''
        scope = expand({target['doi']}, ('is-same-as', 'is-identical-to', 'is-version-of', 'has-version'), parents, family)
        candidates = {d for parent in scope for d in reverse[parent] if consensus(reviews[d])
                      and edges(reviews[d], 'is-review-of') <= scope}
        dated = {}
        for identifier in candidates:
            parts = reviews[identifier].get('published', {}).get('date-parts', [[]])[0]
            try:
                dated[identifier] = date(*parts) if len(parts) == 3 else None
            except (ValueError, TypeError):
                dated[identifier] = None
        maximum = max((d for d in dated.values() if d is not None), default=None)
        tied = {d for d, day in dated.items() if day is not None and day == maximum}
        unknown = any(day is None for day in dated.values())
        latest = next(iter(tied)) if len(tied) == 1 and not unknown else ''
        reason = '' if latest else 'no_candidates' if not candidates else 'missing_date' if unknown else 'latest_date_tie'
        status = lambda chosen: 'concordant' if chosen == target['assessment_doi'] else 'discordant' if chosen else 'abstained'
        selected_source = history.get(latest)
        rows.append({'family': family, 'current_assessment_doi': target['assessment_doi'],
            'latest_dated_selected': latest, 'latest_dated_status': status(latest), 'latest_dated_abstention': reason,
            'forward_typed_selected': typed_choice, 'forward_typed_status': status(typed_choice),
            'selected_source_available': selected_source is not None,
            'known_label_difference': selected_source is not None and set(selected_source['labels']) != set(target['labels'])})
    summary = {'n': len(rows), 'latest_dated': dict(Counter(r['latest_dated_status'] for r in rows)),
        'forward_typed': dict(Counter(r['forward_typed_status'] for r in rows)),
        'latest_known_label_differences': sum(r['known_label_difference'] for r in rows),
        'latest_selections_with_resolved_source': sum(r['selected_source_available'] for r in rows),
        'current_task_uses_all_current_references_without_historical_candidate_imputation': True}
    return rows, summary, reverse


def history_metrics(families, current, history, parents, reviews, reverse):
    rows = []
    for target in current:
        family = target['family']
        ordered = sorted((r for r in families[family] if r['timing_date'] <= CUTOFF.isoformat()), key=rank)
        historic = {}
        for row in ordered:
            if norm(row['version_doi']) == target['version_doi'] or not row['assessment_present']:
                continue
            historic[norm(row['assessment_doi']) or 'unresolved_identity:' + row['filename']] = row
        possibilities = []
        for row in historic.values():
            values = [row['labels']] if row['labels'] is not None else [x['labels'] for x in row['assessment_candidates']]
            assert values, (family, 'Missing candidate label evidence')
            possibilities.append([set(labels) != set(target['labels']) for labels in values])
        lower = any(all(values) for values in possibilities)
        upper = any(any(values) for values in possibilities)
        earlier = sorted(historic.values(), key=lambda r: (r['timing_date'], r['role'] == 'article', r['file_version'], r['filename']))
        nearest = earlier[-1] if earlier else None
        assert not nearest or nearest['labels'] is not None, 'Nearest-prior label unresolved'
        latest_reviewed_preprint = max((r for r in ordered if r['role'] == 'preprint'),
                                      key=lambda r: (r['file_version'], r['timing_date']))
        assert latest_reviewed_preprint['labels'] is not None, 'Latest reviewed-preprint labels unresolved'
        critical = any(any(x in LEVELS[:2] for x in (r['labels'] or [])) for r in historic.values())
        same = expand({target['doi']}, ('is-same-as', 'is-identical-to'), parents, family)
        pattern = re.compile(r'10\.7554/elife\.' + re.escape(family) + r'(?:\.\d+)?$')
        scope = same | {d for parent in same for kind in ('is-version-of', 'has-version')
                        for d in edges(parents.get(parent, {}), kind) if pattern.fullmatch(d)}
        registered = {d for parent in scope for d in reverse[parent] if consensus(reviews[d])
                      and d in history and history[d]['version_doi'] != target['version_doi']}
        registered_critical = any(any(x in LEVELS[:2] for x in history[d]['labels']) for d in registered)
        rows.append({'family': family, 'any_historical_label_difference_lower': int(lower),
            'any_historical_label_difference_upper': int(upper),
            'nearest_prior_label_difference': int(nearest is not None and set(nearest['labels']) != set(target['labels'])),
            'latest_reviewed_preprint_label_difference': int(set(latest_reviewed_preprint['labels']) != set(target['labels'])),
            'latest_reviewed_preprint_group_difference': int(latest_reviewed_preprint['group'] != target['group']),
            'source_critical_history_current_favorable': int(target['group'] == 'at_least_solid' and critical),
            'registered_critical_history_current_favorable': int(target['group'] == 'at_least_solid' and registered_critical),
            'latest_reviewed_preprint_file': latest_reviewed_preprint['filename'],
            'latest_reviewed_preprint_assessment_doi': latest_reviewed_preprint['assessment_doi'],
            'latest_reviewed_preprint_labels': latest_reviewed_preprint['labels'],
            'latest_reviewed_preprint_group': latest_reviewed_preprint['group'],
            'current_assessment_doi': target['assessment_doi'], 'current_labels': target['labels'], 'current_group': target['group']})
    metrics = ('any_historical_label_difference_lower', 'any_historical_label_difference_upper',
               'nearest_prior_label_difference', 'latest_reviewed_preprint_label_difference',
               'latest_reviewed_preprint_group_difference', 'source_critical_history_current_favorable',
               'registered_critical_history_current_favorable')
    summary = {key: sum(r[key] for r in rows) for key in metrics}
    return rows, summary


def horizon_counts(states, alternatives):
    families = defaultdict(list)
    for row in states:
        families[row['family']].append(row)
    counts, selections = [], []
    for scenario in ('primary', 'earliest', 'latest'):
        for days in (180, 365, 540):
            count = Counter()
            subsets = {key: Counter() for key in ('all', 'later', 'VOR')}
            for family, rows in families.items():
                baseline = next(r for r in rows if r['role'] == 'preprint' and r['file_version'] == 1)
                start = date.fromisoformat(alternatives[baseline['filename']][scenario + '_date'])
                if (CUTOFF - start).days < days:
                    continue
                eligible = [r for r in rows if start <= date.fromisoformat(alternatives[r['filename']][scenario + '_date']) <= start + timedelta(days=days)
                            and (r['role'] == 'preprint' or r['verified_state'] == 'version of record')]
                latest = max(eligible, key=lambda r: (alternatives[r['filename']][scenario + '_date'], r['role'] == 'article', r['file_version']))
                count['eligible'] += 1
                count['initial_' + baseline['group']] += 1
                count['snapshot_' + latest['group']] += 1
                count['initial_three_categories'] += int(baseline['group'] in {'at_least_solid', 'below_solid', 'mixed_boundary'})
                count['VOR_present'] += int(any(r['role'] == 'article' for r in eligible))
                count['attained_solid'] += int(any(r['group'] == 'at_least_solid' for r in eligible))
                if days == 365 and baseline['group'] == 'below_solid':
                    for subset in subsets:
                        selected = subset == 'all' or (any(r['role'] == 'article' or r['file_version'] > 1 for r in eligible) if subset == 'later'
                                                      else any(r['role'] == 'article' for r in eligible))
                        if selected:
                            subsets[subset]['denominator'] += 1
                            subsets[subset]['favorable_latest'] += int(latest['group'] == 'at_least_solid')
            counts += [{'scenario': scenario, 'horizon_days': days, 'metric': k, 'count': v} for k, v in sorted(count.items())]
            if days == 365:
                selections += [{'scenario': scenario, 'subset': k, **v, 'percent': 100 * v['favorable_latest'] / v['denominator']}
                               for k, v in subsets.items()]
    return counts, selections


def controlled_output(path, protected):
    output = Path(path).resolve()
    if output.exists():
        raise ValueError('Output must be a new directory separate from the inputs')
    for parent in protected:
        parent = Path(parent).resolve()
        if output == parent or parent in output.parents or output in parent.parents:
            raise ValueError('Output overlaps a read-only input')
    output.mkdir(parents=True)
    def allowed(name):
        if isinstance(name, int):
            raise PermissionError('Unclassified file descriptor mutation is prohibited')
        resolved = Path(os.fsdecode(name)).resolve()
        if resolved != output and output not in resolved.parents:
            raise PermissionError('Mutation outside the output directory')
    def guard(event, arguments):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
            raise PermissionError('Network and external processes are disabled')
        if event == 'open':
            name, mode, flags = arguments
            if isinstance(name, int):
                return
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                allowed(name)
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown', 'os.utime', 'os.truncate'}:
            allowed(arguments[0])
        elif event in {'os.rename', 'os.replace', 'os.link'}:
            allowed(arguments[0]); allowed(arguments[1])
        elif event == 'os.symlink':
            raise PermissionError('Symbolic-link creation is prohibited during the audit')
    sys.addaudithook(guard)
    return output



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repository = args.repository.resolve()
    evidence = repository / 'Evidence/SourceAudit'
    baseline = repository / 'Inputs/records/Results'
    output = controlled_output(args.output, [repository])
    retained = read(evidence / 'evidence_manifest.json')['files']
    for name, item in retained.items():
        path = evidence / name
        assert path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256'], name
    execution = read(evidence / 'source_execution_record.json')
    assert execution['record_type'] == 'derived_publication_receipt'
    assert execution['original_execution']['status'] == 'COMPLETED_WITH_UNRESOLVED_ITEMS'
    assert execution['original_execution']['full_original_corpus_audit_closed'] is False
    old = read(baseline / 'source_states.json')
    new = read(evidence / 'corrected_source_states.json')
    by_name = {r['filename']: r for r in new}
    assert len(new) == len(by_name) == 13230
    manifest = list(csv_rows(evidence / 'source_manifest.csv'))
    assert len(manifest) == 13230
    for row in manifest:
        state = by_name[Path(row['relative_path']).name]
        assert state['sha256'] == row['sha256'] and state['git_blob'] == row['git_blob'] and state['bytes'] == int(row['bytes'])
        assert state['family'] == row['family'] and state['role'] == row['role']
    current, history, originals, families = derive_targets(new)
    assert len(current) == 3550 and len(families) == 4856
    differences, field_checks = compare(old, new, 'filename', SOURCE_FIELDS)
    assert len(differences) == 25
    changed = sorted({r['key'] for r in differences})
    assert len(changed) == 7
    current_differences, current_checks = compare(read(baseline / 'crossref_source_targets.json'), current, 'family')
    map_differences, map_checks = compare(read(evidence / 'original_preprint_map.json'), originals, 'family')
    history_differences, history_checks = compare(list(read(baseline / 'assessment_source_targets.json').values()), list(history.values()), 'assessment_doi')
    assert not current_differences and not map_differences
    assert len(history_differences) == 7 and all(r['field'] == 'record_presence' for r in history_differences)
    resolutions = list(csv_rows(evidence / 'assessment_resolution.csv'))
    exceptions = [r for r in resolutions if 'abstention' in r['consensus_resolution']]
    assert {r['filename'] for r in exceptions} == {'elife-preprint-100967-v1.xml', 'elife-preprint-109502-v1.xml'}
    roles, control_objects, case_objects = Counter(), [], defaultdict(list)
    for obj in csv_rows(evidence / 'subarticle_index.csv'):
        roles[obj['interpreted_role']] += 1
        name = Path(obj['manifest_relative_path']).name
        assert obj['source_sha256'] == by_name[name]['sha256']
        assert obj['doi_count'] == '1' and obj['body_present'] == 'True' and obj['role_signal_conflict'] == 'False'
        if name in changed or obj['family'] in {'87297', '89782'}:
            control_objects.append(obj)
            case_objects[name].append(obj)
    assert sum(roles.values()) == 53552
    cases = []
    previous = {r['filename']: r for r in old}
    for name in changed:
        before, after = previous[name], by_name[name]
        candidates = [r for r in case_objects[name] if r['interpreted_role'] == 'consensus_assessment']
        if after['family'] not in {'100967', '109502'}:
            assert len(candidates) == 1 and candidates[0]['title'].casefold() == 'elife assessment:'
            assert candidates[0]['article_type'] == 'editor-report'
            assert not before['assessment_present'] and after['assessment_present']
            assert norm(after['assessment_doi']) == candidates[0]['primary_doi']
            assert after['labels'] == json.loads(candidates[0]['evidence_strength_terms_json'])
        else:
            assert len(candidates) == 2 and len({r['primary_doi'] for r in candidates}) == 2 and after['assessment_doi'] == ''
            same_prose = len({r['body_whitespace_text_sha256'] for r in candidates}) == 1
            assert same_prose == (after['family'] == '100967')
        cases.append({'family': after['family'], 'filename': name, 'before_labels': before['labels'], 'after_labels': after['labels'],
            'before_group': before['group'], 'after_group': after['group'],
            'candidate_DOIs': sorted(r['primary_doi'] for r in candidates),
            'candidate_label_sets': [json.loads(r['evidence_strength_terms_json']) for r in candidates],
            'source_sha256': after['sha256'], 'identity_status': 'ambiguous' if after['family'] in {'100967', '109502'} else 'unique',
            'evidence_level': 'retained derived source-object index and corrected projection'})
    parents, reviews = read(baseline / 'crossref_records.json'), read(baseline / 'crossref_review_graph.json')
    comparator_rows, comparator_summary, reverse = current_comparators(current, history, parents, reviews)
    assert comparator_summary['latest_dated'] == {'concordant': 3533, 'discordant': 7, 'abstained': 10}
    assert comparator_summary['forward_typed'] == {'concordant': 3528, 'discordant': 1, 'abstained': 21}
    assert comparator_summary['latest_known_label_differences'] == 0 and comparator_summary['latest_selections_with_resolved_source'] == 3540
    historical_rows, historical_summary = history_metrics(families, current, history, parents, reviews, reverse)
    assert historical_summary == {'any_historical_label_difference_lower': 1882, 'any_historical_label_difference_upper': 1883,
        'nearest_prior_label_difference': 36, 'latest_reviewed_preprint_label_difference': 37,
        'latest_reviewed_preprint_group_difference': 28, 'source_critical_history_current_favorable': 952,
        'registered_critical_history_current_favorable': 949}
    alternatives = {r['filename']: r for r in csv_rows(evidence / 'source_chronology.csv')}
    horizons, selection = horizon_counts(new, alternatives)
    primary = {(r['horizon_days'], r['metric']): r['count'] for r in horizons if r['scenario'] == 'primary'}
    assert [primary[h, 'eligible'] for h in [180, 365, 540]] == [4384, 3882, 3371]
    assert [primary[365, 'initial_' + g] for g in ['at_least_solid', 'below_solid', 'mixed_boundary', 'ungraded']] == [2490, 986, 402, 4]
    expected = {'all': (493, 986), 'later': (493, 712), 'VOR': (405, 532)}
    assert all((r['favorable_latest'], r['denominator']) == expected[r['subset']] for r in selection)
    ledger_controls = []
    for family in ['87297', '89782']:
        rr = [r for r in new if r['family'] == family]
        if family == '87297':
            pairs = [o for o in control_objects if o['family'] == family and o['parent_parsed_version_doi'] == '10.7554/elife.87297.4' and o['interpreted_role'] == 'consensus_assessment']
            assert len(pairs) == 2 and {r['primary_doi'] for r in pairs} == {'10.7554/elife.87297.4.sa0','10.7554/elife.87297.4.sa1'}
            assert len({r['body_whitespace_text_sha256'] for r in pairs}) == 1
            assert {r['evidence_strength_terms_json'] for r in pairs} == {'["solid"]'}
        else:
            article = sorted([r for r in rr if r['role'] == 'article'], key=rank)
            assert [r['labels'] for r in article] == [[], ['incomplete']]
            assert {norm(r['assessment_doi']) for r in article} == {'10.7554/elife.89782.2.sa0'}
            assert [r['timing_date'] for r in article] == ['2024-06-13','2024-10-31']
        ledger_controls.append({'family': family, 'original_full_source_execution': 'PASS', 'current_replay': 'CORROBORATED_FROM_DERIVED_INDEX_AND_PROJECTION', 'raw_XML_reextracted_in_this_replay': False})
    summary = {'status': 'PASS_RETAINED_DERIVED_EVIDENCE_REPLAY', 'retained_input_files_verified': len(retained),
        'original_source_audit_status': execution['original_execution']['status'],
        'original_full_corpus_audit_closed': execution['original_execution']['full_original_corpus_audit_closed'],
        'full_raw_XML_API_extraction_repeated': False,
        'source_projection_rows': len(new), 'source_bytes_declared': sum(r['bytes'] for r in new), 'families': len(families),
        'source_field_checks': field_checks, 'source_field_differences': len(differences), 'changed_source_representations': len(changed),
        'subarticle_role_counts': dict(roles), 'current_reference_checks': current_checks, 'current_reference_differences': len(current_differences),
        'original_map_checks': map_checks, 'original_map_differences': len(map_differences),
        'historical_reference_field_checks': history_checks, 'historical_reference_record_presence_changes': len(history_differences),
        'source_identity_exceptions': [r['filename'] for r in exceptions], 'current_comparators': comparator_summary,
        'historical_comparisons': historical_summary, 'source_control_reconciliation': ledger_controls,
        'longitudinal_disposition': 'Retained only as a correction record; excluded from active article outcome claims',
        'primary_365_initial_categories': {g: primary[365, 'initial_' + g] for g in ['at_least_solid','below_solid','mixed_boundary','ungraded']}}
    changes = []
    for case in cases:
        changes.append({'id': 'source_' + case['family'], 'unit': case['filename'], 'before': {'labels': case['before_labels'], 'group': case['before_group']},
            'after': {'labels': case['after_labels'], 'group': case['after_group'], 'candidate_DOIs': case['candidate_DOIs']},
            'status': 'retained_ambiguity' if case['identity_status'] == 'ambiguous' else 'corrected_omission',
            'active_article_effect': 'source-method verification; current reference and endpoint-join identity unchanged', 'source_sha256': case['source_sha256']})
    changes += [
        {'id': 'historical_label_variation', 'before': 1882, 'after': {'lower':1882,'upper':1883}, 'status': 'partially_identified', 'active_article_effect': 'SI corrected; not a current-selector error rate'},
        {'id': 'latest_reviewed_preprint_versus_current_VOR', 'before': {'strength_set_differences':38,'group_differences':29},
         'after': {'strength_set_differences':37,'group_differences':28}, 'status':'corrected_source_omission_93656',
         'active_article_effect':'SI direct latest-RP comparison corrected; nearest identified earlier-version difference remains 36; all 3,550 current references retained'},
        {'id': 'current_latest_comparator', 'before': [3533,7,10], 'after': [3533,7,10], 'status': 'unchanged_independently_recomputed', 'active_article_effect': 'retain all 3,550 current references; no whole-history denominator exclusion'},
        {'id': 'critical_history_intersection', 'before':949, 'after':949, 'status':'unchanged_independently_recomputed', 'active_article_effect':'main contextual comparison retained'},
        {'id':'historical_365_below_solid_background','before':{'numerator':493,'denominator':984},'after':{'numerator':493,'denominator':986},'status':'corrected_background','active_article_effect':'not an active article outcome; retained correction receipt'},
        {'id':'source_audit_execution','before':'full source extraction not yet available','after':'completed with two retained source ambiguities','status':'completed_execution_with_unresolved_semantics','active_article_effect':'methods/SI updated; original strict closure flag remains false'}]
    write_json(output / 'source_integration_summary.json', summary)
    write_json(output / 'scientific_change_register.json', changes)
    write_csv(output / 'scientific_change_register.csv', changes)
    write_csv(output / 'source_correction_cases.csv', cases)
    write_csv(output / 'source_field_differences.csv', differences)
    write_csv(output / 'current_comparators.csv', comparator_rows)
    write_csv(output / 'historical_bounds.csv', historical_rows)
    write_csv(output / 'corrected_background_horizons.csv', horizons)
    write_csv(output / 'corrected_background_selection.csv', selection)
    write_json(output / 'source_identity_exceptions.json', exceptions)
    write_json(output / 'reconstructed_current_targets.json', current)
    print(json.dumps({'status':summary['status'], 'current':comparator_summary, 'history':historical_summary},indent=2))


if __name__ == '__main__':
    main()
