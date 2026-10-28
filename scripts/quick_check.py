#!/usr/bin/env python3
"""Check the published result identities and recalculate central results from derived data."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / 'analysis'

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()

def rows(name):
    with (A / 'FigureProduction' / 'Data' / name).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))

def require(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label}: expected {expected!r}, observed {actual!r}')
    return label

def run():
    checks = []
    def checked(actual, expected, label):
        checks.append(require(actual, expected, label))
    results = json.loads((A / 'result_manifest.json').read_text())['files']
    actual = {p.relative_to(A/'Results').as_posix() for p in (A/'Results').rglob('*') if p.is_file()}
    checked(actual, {x['path'] for x in results}, 'Result manifest membership')
    for x in results:
        path = A / 'Results' / x['path']
        checked(path.stat().st_size, x['bytes'], 'Result size: ' + x['path'])
        checked(digest(path), x['sha256'], 'Result SHA-256: ' + x['path'])
    figures = A/'FigureProduction'/'Data'
    manifest = json.loads((figures/'input_manifest.json').read_text())
    for name, sha in manifest.items():
        checked(digest(figures/name), sha, 'Figure data SHA-256: ' + name)
    frame = [r for r in rows('Families.csv') if r['exact_MED']=='1']
    checked(len(frame), 3547, 'Canonical MED population')
    for key, count in [('native_review',2312),('enriched_review',2900),('admissible_EEB_fallback',509),('combined_event_presence',3409)]:
        checked(sum(int(r[key]) for r in frame), count, key)
    cens = rows('ResidualCensus.csv')
    checked(len(cens), 647, 'Residual families')
    checked(sum(int(r['upstream_review']) for r in cens), 512, 'Upstream positive families')
    checked(sum(int(r['eligible_declared_EEB_fallback']) for r in cens), 509, 'Operational fallback in residual census')
    members = rows('family_coverage_structure.csv')
    checked(len(members), 60, 'Matched-document families')
    checked(Counter(r['stratum'] for r in members), Counter({'review_with_generic_url':20,'review_with_DOI_url':20,'review_no_url':20}), 'Matched sample strata')
    checked(sum(int(r['source_objects']) for r in members), 471, 'Publisher source documents')
    checked(sum(int(r['reconstructed_objects']) for r in members), 362, 'Reconstructed source documents')
    checked(sum(int(r['endpoint_rows']) for r in members), 462, 'Eligible endpoint rows')
    checked(sum(int(r['matched_rows']) for r in members), 458, 'Matched endpoint rows')
    checked(sum(int(r['all_source_roles_complete']) for r in members), 37, 'Complete family histories')
    steps = rows('evaluation_step_coverage.csv')
    checked(Counter(r['coverage_state'] for r in steps), Counter({'complete':91,'none':26}), 'Evaluation-step completeness')
    checked(all(r['coverage_state']!='none' or int(r['step_rank'])>1 for r in steps), True, 'All missing steps are later steps')
    checked(sum(int(r['source_objects']) for r in steps if r['coverage_state']=='none'), 109, 'Unrepresented source objects')
    objects = rows('source_object_structure.csv')
    checked(len(objects), 471, 'Source-object ledger membership')
    checked(len({r['source_doi'] for r in objects}), 471, 'Distinct source identifiers')
    checked(sum(int(r['endpoint_unique_match']) for r in objects), 362, 'Object-level reconstruction')
    precision = rows('endpoint_precision_reconstruction.csv')
    checked(len(precision), 462, 'Timestamp source rows including unresolved rows')
    precision = [r for r in precision if r['source_doi']]
    checked(len(precision), 458, 'Timestamp ablation matched cohort')
    checked(sum(int(r['seconds_same_unique']) for r in precision), 273, 'Whole-second timestamp matches')
    checked(sum(int(r['day_same_unique']) for r in precision), 5, 'Calendar-day timestamp matches')
    # Recompute published comparator counts directly from their archived tables.
    comparison = A / 'Results' / 'SourceAuditIntegration' / 'current_comparators.csv'
    with comparison.open(newline='', encoding='utf-8-sig') as stream:
        comparator_rows = list(csv.DictReader(stream))
    checked(len(comparator_rows), 3550, 'Current-version comparison population')
    checked(Counter(r['latest_dated_status'] for r in comparator_rows),
            Counter({'concordant':3533,'discordant':7,'abstained':10}),
            'Latest-dated baseline classification')
    checked(Counter(r['forward_typed_status'] for r in comparator_rows),
            Counter({'concordant':3528,'discordant':1,'abstained':21}),
            'Forward-typed comparison classification')
    with (A/'Results'/'Provenance'/'publisher_comparator.csv').open(newline='',encoding='utf-8-sig') as stream:
        publisher = list(csv.DictReader(stream))
    checked(len(publisher), 120, 'Publisher independent comparison population')
    checked(Counter(r['doi_concordance'] for r in publisher),Counter({'1':118,'0':2}),
            'Publisher version DOI concordance')
    checked(Counter(r['abstained'] for r in publisher),Counter({'0':118,'1':2}),
            'Publisher verification abstentions')
    print(json.dumps({'status':'PASS','checked_results':len(results),'figure_data_files_checked':len(manifest),
                      'assertions_passed':len(checks),'population':3547,'matched_rows':458,
                      'source_objects':471,'reconstructed_objects':362,'complete_steps':91,
                      'missing_steps':26,'operational_event_coverage':3409}, indent=2))
    return len(checks)

if __name__=='__main__':
    run()
