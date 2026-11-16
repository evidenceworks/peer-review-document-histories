"""Reproduce documentary coverage and conditional finite-population estimates."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
import argparse
import hashlib
import csv
import json
import math
import random
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
STRATA = {'review_with_generic_url': 2849, 'review_with_DOI_url': 21, 'review_no_url': 30}
OUTCOMES = ['complete', 'augmented_complete', 'complete_gain', 'incomplete',
            'augmented_incomplete', 'missed_reply', 'augmented_missed_reply',
            'missed_multistep', 'augmented_missed_multistep', 'empty_reconstruction',
            'source_reply', 'source_multistep', 'reply_gain', 'multistep_gain', 'any_recovery']

def records(name):
    with (DATA / name).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))

def require(condition, message):
    """Validate recorded data even when interpreter optimization is enabled."""
    if not condition:
        raise ValueError(message)


def positive(value):
    """Read a recorded binary value; reject missing or misspelled values."""
    text = str(value).strip().lower()
    if text in ('true', '1'):
        return True
    if text in ('false', '0'):
        return False
    raise ValueError(f"Invalid binary value: {value!r}")


def normalize_doi(value):
    """Normalize an identifier, DOI label, or resolver URL without guessing a DOI."""
    text = unquote(str(value).strip()).lower()
    text = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', text)
    if not re.fullmatch(r'10\.\d{4,9}/\S+', text):
        raise ValueError(f"Invalid DOI: {value!r}")
    return text


def time_key(value, precision='exact'):
    """Normalize a timestamp to UTC while retaining all fractional-second digits.

    Hours, minutes, and seconds are mandatory. Offset-free values are UTC,
    following the study protocol. Date-only rows do not enter the matched table.
    """
    if precision not in ('exact', 'seconds', 'days'):
        raise ValueError(f"Unknown time precision: {precision!r}")
    text = str(value).strip()
    match = re.fullmatch(
        r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})'
        r'(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?', text)
    if match is None:
        raise ValueError(f"An eligible timestamp must include seconds: {value!r}")
    base, fraction_text, offset = match.groups()
    dt = datetime.fromisoformat(base + ('+00:00' if offset in (None, 'Z') else offset))
    seconds = int(dt.timestamp())
    fraction = Decimal('0.' + fraction_text) if fraction_text else Decimal(0)
    if precision == 'seconds':
        return seconds
    if precision == 'days':
        return seconds // 86400
    return seconds, fraction


def history_pattern(sequence):
    """Classify an observed sequence; do not require the study's prefix finding."""
    if not sequence or any(s not in ('complete', 'partial', 'unrepresented') for s in sequence):
        raise ValueError('A history must contain valid source-step states')
    if all(s == 'complete' for s in sequence):
        return 'complete'
    if all(s == 'unrepresented' for s in sequence):
        return 'empty'
    if 'partial' in sequence:
        return 'incomplete_with_partial_steps'
    is_prefix = sequence == sorted(sequence, key=lambda s: 0 if s == 'complete' else 1)
    return 'incomplete_nonempty_prefix' if is_prefix else 'incomplete_nonprefix'


def verify_inputs(directory=DATA):
    """Check the distributed derived-data snapshot, not historical source authenticity."""
    directory = Path(directory)
    manifest = directory / 'manifest.sha256'
    if not manifest.is_file():
        raise FileNotFoundError('Missing data/manifest.sha256')
    listed = set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        digest, filename = line.split('  ', 1)
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or Path(filename).name != filename:
            raise ValueError('Malformed data manifest entry')
        if filename in listed:
            raise ValueError(f'Duplicate manifest entry: {filename}')
        listed.add(filename)
        target = directory / filename
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Input checksum mismatch: {filename}')
    actual = {p.name for p in directory.iterdir() if p.is_file() and p.name != manifest.name}
    require(listed == actual, 'Data files and manifest entries differ')
    return len(listed)


@lru_cache(maxsize=None, typed=True)
def count_interval(population, sample, successes, tail_denominator=40):
    """Invert the two exact hypergeometric tails, retaining their boundary mass."""
    for name, value in [('population', population), ('sample', sample),
                        ('successes', successes), ('tail_denominator', tail_denominator)]:
        if type(value) is not int:
            raise TypeError(f'{name} must be an integer')
    if not (0 <= successes <= sample <= population) or tail_denominator <= 2:
        raise ValueError('Require 0 <= successes <= sample <= population and tail_denominator > 2')
    denominator = math.comb(population, sample)
    def tail(candidate, upper):
        low = max(0, sample - population + candidate)
        high = min(sample, candidate)
        indices = range(max(low, successes), high + 1) if upper else range(low, min(high, successes) + 1)
        numerator = sum(math.comb(candidate, k) * math.comb(population - candidate, sample - k)
                        for k in indices)
        return numerator * tail_denominator >= denominator
    low, high = successes, population - sample + successes
    a, b = low, high
    while a < b:
        mid = (a + b) // 2
        if tail(mid, True):
            b = mid
        else:
            a = mid + 1
    lower = a
    a, b = low, high
    while a < b:
        mid = (a + b + 1) // 2
        if tail(mid, False):
            a = mid
        else:
            b = mid - 1
    return lower, a

def estimate(population, remainder, sample, successes, known, unknown=0):
    if population <= 0 or sample <= 0 or known < 0 or unknown < 0:
        raise ValueError('Invalid finite-population estimate inputs')
    if remainder + unknown > population or known > population - remainder - unknown:
        raise ValueError('Known and unknown counts do not fit the target population')
    lower, upper = count_interval(remainder, sample, successes)
    return {'estimate_lower': (known + remainder * successes / sample) / population,
            'estimate_upper': (known + remainder * successes / sample + unknown) / population,
            'lower95': (known + lower) / population, 'upper95': (known + upper + unknown) / population}

def validate_selection(census, families, bmc, embo):
    plans = json.loads((DATA / 'sampling.json').read_text())
    e = plans['elife']
    med = [r for r in census if positive(r['exact_MED'])]
    generator = random.Random(e['initial_seed'])
    initial = []
    for stratum in sorted({r['residual_class'] for r in med}):
        group = sorted((r for r in med if r['residual_class'] == stratum), key=lambda r: r['family'])
        initial.extend(generator.sample(group, 20))
    require([r['family'] for r in initial] == e['initial_all'], "Recorded-data check failed: [r['family'] for r in initial] == e['initial_all']")
    known = {r['family'] for r in e['known']}
    pool = sorted((r['family'] for r in census if positive(r['exact_MED']) and
                   r['residual_class'] == 'review_with_generic_url' and r['family'] not in known), key=int)
    require(len(pool) == 2829, 'Recorded-data check failed: len(pool) == 2829')
    require(random.Random(e['seed']).sample(pool, 150) == e['selected'], "Recorded-data check failed: random.Random(e['seed']).sample(pool, 150) == e['selected']")
    require(known == {r['family'] for r in families if r['sample_arm'] == 'original'}, "Recorded-data check failed: known == {r['family'] for r in families if r['sample_arm'] == 'original'}")
    require(set(e['selected']) == {r['family'] for r in families if r['sample_arm'] == 'additional'}, "Recorded-data check failed: set(e['selected']) == {r['family'] for r in families if r['sample_arm'] == 'additional'}")
    for name, source, key in [('bmc', bmc, 'doi'), ('embo', embo, 'preprint_doi')]:
        plan = plans[name]
        require(len(plan['frame']) == len(set(plan['frame'])) == plan['N'], "Recorded-data check failed: len(plan['frame']) == len(set(plan['frame'])) == plan['N']")
        pool = sorted(set(plan['frame']) - set(plan['known']))
        require(len(pool) == plan['D'], "Recorded-data check failed: len(pool) == plan['D']")
        require(random.Random(plan['seed']).sample(pool, plan['n']) == plan['selected'], "Recorded-data check failed: random.Random(plan['seed']).sample(pool, plan['n']) == plan['selected']")
        require(set(plan['selected']) == {r[key] for r in source if r['cohort'] == 'extension'}, "Recorded-data check failed: set(plan['selected']) == {r[key] for r in source if r['cohort'] == 'extension'}")
        require(set(plan['known']) == {r[key] for r in source if r['cohort'] != 'extension'}, "Recorded-data check failed: set(plan['known']) == {r[key] for r in source if r['cohort'] != 'extension'}")
    return {'elife_initial_draw': 120, 'elife_additional_draw': 150, 'bmc_additional_draw': 60, 'embo_additional_draw': 30,
            'recorded_draws_reproduced': True}

def reconstruct(documents, endpoint_rows, original_families):
    by_family = defaultdict(list)
    by_step = defaultdict(list)
    by_doi = {}
    for document in documents:
        by_family[document['family']].append(document)
        by_step[document['family'], document['step_id']].append(document)
        require(document['source_doi'] not in by_doi, "Recorded-data check failed: document['source_doi'] not in by_doi")
        by_doi[document['source_doi']] = document
    matched = set()
    row_counts, unresolved = Counter(), Counter()
    precision = {}
    for unit in ['exact', 'seconds', 'days', 'input_doi']:
        result = Counter()
        represented_steps = set()
        for row in endpoint_rows:
            candidates = [d for d in by_family[row['family']] if normalize_doi(row['input_doi']) in {normalize_doi(v) for v in d['input_dois'].split('|')}
                          and (unit == 'input_doi' or time_key(row['evaluation_time'], unit) == time_key(d['published'], unit))]
            if unit == 'exact':
                require(len(candidates) == int(row['candidate_count']), "Recorded-data check failed: len(candidates) == int(row['candidate_count'])")
                selected = candidates[0]['source_doi'] if len(candidates) == 1 else ''
                require(selected == row['selected_source_doi'], "Recorded-data check failed: selected == row['selected_source_doi']")
                if selected:
                    matched.add(selected)
                    row_counts[row['family']] += 1
                else:
                    unresolved[row['family']] += 1
            unique_step = len({d['step_id'] for d in candidates}) == 1
            if unique_step:
                represented_steps.add((row['family'], candidates[0]['step_id']))
                result['all_eligible_single_step_rows'] += 1
            if row['selected_source_doi']:
                result['rows'] += 1
                result['unique_document'] += len(candidates) == 1
                result['unique_role'] += len({d['role'] for d in candidates}) == 1
                result['unique_step'] += unique_step
        result['all_eligible_represented_steps'] = len(represented_steps)
        result['previously_missing_steps_recovered'] = sum(not any(positive(d['endpoint']) for d in by_step[s]) for s in represented_steps)
        precision[unit] = dict(result)
    require(matched == {d['source_doi'] for d in documents if positive(d['endpoint'])}, "Recorded-data check failed: matched == {d['source_doi'] for d in documents if positive(d['endpoint'])}")
    require(all(positive(d['augmented']) == (positive(d['endpoint']) or positive(d['intermediary'])) for d in documents), "Recorded-data check failed: all(positive(d['augmented']) == (positive(d['endpoint']) or positive(d['intermediary'])) for d in documents)")
    families, steps = [], []
    for original in original_families:
        family = original['family']
        source = by_family[family]
        selected = [d for d in source if d['source_doi'] in matched]
        augmented = [d for d in source if positive(d['augmented'])]
        source_steps = {d['step_id'] for d in source}
        selected_steps = {d['step_id'] for d in selected}
        augmented_steps = {d['step_id'] for d in augmented}
        source_reply = any(d['role'] == 'reply' for d in source)
        row = {'family': family, 'stratum': original['stratum'], 'sample_arm': original['sample_arm'],
               'source_n': len(source), 'matched_n': len(selected), 'augmented_n': len(augmented),
               'source_steps': len(source_steps), 'represented_steps': len(selected_steps),
               'augmented_steps': len(augmented_steps), 'complete': int(len(source) == len(selected)),
               'augmented_complete': int(len(source) == len(augmented)), 'source_reply': int(source_reply),
               'source_multistep': int(len(source_steps) > 1),
               'missed_reply': int(source_reply and not any(d['role'] == 'reply' for d in selected)),
               'augmented_missed_reply': int(source_reply and not any(d['role'] == 'reply' for d in augmented)),
               'missed_multistep': int(len(source_steps) > 1 and len(selected_steps) <= 1),
               'augmented_missed_multistep': int(len(source_steps) > 1 and len(augmented_steps) <= 1),
               'empty_reconstruction': int(not selected), 'matched_rows': row_counts[family],
               'unresolved_rows': unresolved[family]}
        row.update(complete_gain=row['augmented_complete'] - row['complete'], incomplete=1 - row['complete'],
                   augmented_incomplete=1 - row['augmented_complete'], reply_gain=row['missed_reply'] - row['augmented_missed_reply'],
                   multistep_gain=row['missed_multistep'] - row['augmented_missed_multistep'], any_recovery=int(len(augmented) > len(selected)))
        for key, value in row.items():
            if key in original:
                require(str(value) == original[key], (family, key, value, original[key]))
        families.append(row)
        for step in sorted(source_steps, key=lambda s: int(by_step[family, s][0]['step_rank'])):
            ds = by_step[family, step]
            n, m, a = len(ds), sum(positive(d['endpoint']) for d in ds), sum(positive(d['augmented']) for d in ds)
            steps.append({'family': family, 'step_id': step, 'step_rank': int(ds[0]['step_rank']),
                          'documents': n, 'endpoint_documents': m, 'augmented_documents': a,
                          'endpoint_state': 'complete' if m == n else ('partial' if m else 'unrepresented'),
                          'augmented_state': 'complete' if a == n else ('partial' if a else 'unrepresented'),
                          'latest_publication': max(ds, key=lambda d: time_key(d['published']))['published']})
    return families, steps, precision, by_doi

def standardize(documents):
    weight = sum(int(d['step_rank']) == 1 for d in documents) / len(documents)
    roles = {}
    for role in ['review-article', 'evaluation-summary', 'reply']:
        ds = [d for d in documents if d['role'] == role]
        first = [d for d in ds if int(d['step_rank']) == 1]
        later = [d for d in ds if int(d['step_rank']) > 1]
        counts = {'source': len(ds), 'represented': sum(positive(d['endpoint']) for d in ds),
                  'first_n': len(first), 'later_n': len(later),
                  'first_represented': sum(positive(d['endpoint']) for d in first),
                  'later_represented': sum(positive(d['endpoint']) for d in later)}
        counts['raw'] = counts['represented'] / counts['source']
        counts['standardized'] = weight * counts['first_represented'] / len(first) + (1 - weight) * counts['later_represented'] / len(later)
        roles[role] = counts
    report, reply = roles['review-article'], roles['reply']
    wr, wa = report['first_n'] / report['source'], reply['first_n'] / reply['source']
    rf, rl = report['first_represented'] / report['first_n'], report['later_represented'] / report['later_n']
    af, al = reply['first_represented'] / reply['first_n'], reply['later_represented'] / reply['later_n']
    return {'weight': weight, 'roles': roles, 'raw_gap': report['raw'] - reply['raw'],
            'standardized_gap': report['standardized'] - reply['standardized'],
            'composition': (wr - wa) * ((rf + af) - (rl + al)) / 2,
            'within_position': (wr + wa) / 2 * (rf - af) + (2 - wr - wa) / 2 * (rl - al),
            'common_weight_gap_min': min(rf - af, rl - al), 'common_weight_gap_max': max(rf - af, rl - al)}

def sampling_sensitivity(families):
    output = {}
    for key in OUTCOMES:
        pooled = {'estimate': 0.0, 'lower95': 0.0, 'upper95': 0.0}
        conditional = {'estimate': 0.0, 'lower95': 0.0, 'upper95': 0.0}
        for stratum, population in STRATA.items():
            group = [r for r in families if r['stratum'] == stratum]
            sample, successes = len(group), sum(r[key] for r in group)
            low, high = count_interval(population, sample, successes, 120)
            pooled['estimate'] += population * successes / sample / 2900
            pooled['lower95'] += low / 2900
            pooled['upper95'] += high / 2900
            if stratum == 'review_with_generic_url':
                known = sum(r[key] for r in group if r['sample_arm'] == 'original')
                successes = sum(r[key] for r in group if r['sample_arm'] == 'additional')
                low, high = count_interval(2829, 150, successes, 120)
                conditional['estimate'] += (known + 2829 * successes / 150) / 2900
                conditional['lower95'] += (known + low) / 2900
                conditional['upper95'] += (known + high) / 2900
            else:
                conditional['estimate'] += population * successes / sample / 2900
                conditional['lower95'] += low / 2900
                conditional['upper95'] += high / 2900
        output[key] = {'pooled': pooled, 'conditional_dominant': conditional}
    return output

def preserved_attachment_set(group):
    """Use recorded inventory agreement as well as matched-file comparisons.

    These are historical comparison records, not a fresh audit of PDF bytes.
    """
    require(bool(group), 'An attachment set must contain recorded files')
    require(len({row['filename'] for row in group}) == len(group),
            'Duplicate filenames within a BMC attachment set')
    inventory = {positive(row['inventory_match']) for row in group}
    require(len(inventory) == 1, 'Conflicting per-article inventory outcomes')
    equal = []
    for row in group:
        digests = (row['source_pdf_sha256'], row['archive_pdf_sha256'])
        require(all(re.fullmatch(r'[0-9a-f]{64}', value) for value in digests),
                'Invalid BMC attachment digest')
        require(int(row['source_bytes']) > 0, 'Invalid BMC source-file size')
        same = positive(row['bytes_equal'])
        require(same == (digests[0] == digests[1]),
                'BMC byte-comparison outcome contradicts its recorded digests')
        equal.append(same)
    return next(iter(inventory)) and all(equal)


def text_identity(documents, observations):
    """Compare normalized exact strings without inferring semantic equivalence."""
    by_doi = {d['source_doi']: d for d in documents}
    texts, by_family, by_step = defaultdict(set), defaultdict(list), defaultdict(list)
    locations = set()
    for row in observations:
        ident = row['source_doi']
        require(ident in by_doi and row['family'] == by_doi[ident]['family'],
                'Text fingerprint does not identify a source object in its family')
        require(re.fullmatch(r'[0-9a-f]{64}', row['normalized_text_sha256']) is not None
                and int(row['normalized_characters']) > 0, 'Invalid text fingerprint')
        location = tuple(row[k] for k in ('source_record', 'docmap_index', 'step_id',
                                         'action_index', 'output_index'))
        require(location not in locations, 'Repeated intermediary output locator')
        locations.add(location)
        texts[ident].add(row['normalized_text_sha256'])
    require(set(texts) == {d['source_doi'] for d in documents if positive(d['intermediary'])},
            'Intermediary document flags and text-observation universe differ')
    by_text = defaultdict(set)
    for ident, hashes in texts.items():
        for digest in hashes:
            by_text[by_doi[ident]['family'], digest].add(ident)
    for d in documents:
        by_family[d['family']].append(d)
        by_step[d['family'], d['step_id']].append(d)
    complete_families = {f for f, ds in by_family.items()
                         if all(d['source_doi'] in texts for d in ds)}
    def hashes(ds):
        return set().union(*(texts.get(d['source_doi'], set()) for d in ds))
    def has_new_stable(ds, previous):
        return any(len(texts.get(d['source_doi'], set())) == 1
                   and bool(texts[d['source_doi']] - previous) for d in ds)
    later = later_new = recovered = recovered_new = recovered_earlier_new = 0
    for (family, _), ds in by_step.items():
        rank = int(ds[0]['step_rank']); history = by_family[family]
        earlier = [d for d in history if int(d['step_rank']) < rank]
        if family in complete_families and rank > 1:
            later += 1
            later_new += has_new_stable(ds, hashes(earlier))
        if not any(positive(d['endpoint']) for d in ds) and all(positive(d['augmented']) for d in ds):
            recovered += 1
            endpoint = [d for d in history if positive(d['endpoint'])]
            if all(d['source_doi'] in texts for d in endpoint):
                recovered_new += has_new_stable(ds, hashes(endpoint))
            observed_earlier = [d for d in earlier if d['source_doi'] in texts]
            recovered_earlier_new += has_new_stable(ds, hashes(observed_earlier))
    return {'matched_text_occurrences': len(observations),
            'text_bearing_source_objects': len(texts),
            'normalized_text_variants': sum(len(values) for values in texts.values()),
            'same_DOI_multiple_texts': sum(len(values) > 1 for values in texts.values()),
            'within_family_duplicate_text_groups': sum(len(ids) > 1 for ids in by_text.values()),
            'complete_histories_with_text': len(complete_families),
            'later_steps_in_complete_histories': later,
            'later_steps_with_at_least_one_new_stable_text': later_new,
            'recovered_steps': recovered,
            'recovered_steps_with_new_stable_text_vs_endpoint': recovered_new,
            'recovered_steps_with_new_stable_text_vs_earlier': recovered_earlier_new}


def run(output):
    documents, endpoint_rows = records('elife_documents.csv'), records('elife_endpoint_rows.csv')
    original_families, census = records('elife_families.csv'), records('elife_census.csv')
    bmc, embo = records('bmc_documents.csv'), records('embo_families.csv')
    families, steps, precision, by_doi = reconstruct(documents, endpoint_rows, original_families)
    selection = validate_selection(census, families, bmc, embo)
    counts = {k: sum(r[k] for r in families) for k in families[0] if k not in ['family', 'stratum', 'sample_arm']}
    conditional = {k: estimate(2900, 2829, 150, sum(r[k] for r in families if r['sample_arm'] == 'additional'),
                              sum(r[k] for r in families if r['sample_arm'] == 'original'), 11) for k in OUTCOMES}
    role_groups = {'all': standardize(documents)}
    for stratum in STRATA:
        role_groups[stratum] = standardize([d for d in documents if d['stratum'] == stratum])
    multi = {r['family'] for r in families if r['source_steps'] > 1}
    role_groups['multistep'] = standardize([d for d in documents if d['family'] in multi])
    step_coverage, histories, source_age = {}, {}, []
    for rank in sorted({s['step_rank'] for s in steps}):
        group = [s for s in steps if s['step_rank'] == rank]
        step_coverage[rank] = {'source': len(group), 'endpoint': sum(s['endpoint_state'] == 'complete' for s in group),
                               'augmented': sum(s['augmented_state'] == 'complete' for s in group),
                               'partial_endpoint': sum(s['endpoint_state'] == 'partial' for s in group),
                               'partial_augmented': sum(s['augmented_state'] == 'partial' for s in group)}
    for route in ['endpoint', 'augmented']:
        patterns = Counter()
        for f in families:
            sequence = [s[f'{route}_state'] for s in steps if s['family'] == f['family']]
            patterns[history_pattern(sequence)] += 1
        histories[route] = dict(patterns)
    for s in steps:
        if s['endpoint_state'] == 'unrepresented':
            cutoff = datetime.fromisoformat(next(d['capture'] for d in documents if d['family'] == s['family'])).replace(tzinfo=timezone.utc)
            published = datetime.fromisoformat(s['latest_publication'])
            source_age.append({'family': s['family'], 'step_id': s['step_id'],
                               'age_days': (cutoff - published).total_seconds() / 86400,
                               'augmented_state': s['augmented_state']})
    age = {'missing_steps': len(source_age), 'at_least_90': sum(r['age_days'] >= 90 for r in source_age),
           'at_least_180': sum(r['age_days'] >= 180 for r in source_age),
           'residual_at_least_180': sum(r['age_days'] >= 180 and r['augmented_state'] == 'unrepresented' for r in source_age),
           'minimum_days': min(r['age_days'] for r in source_age)}
    composition = {}
    for label, ds in [('source', documents), ('reconstructed', [d for d in documents if positive(d['endpoint'])]),
                      ('rows', [by_doi[r['selected_source_doi']] for r in endpoint_rows if r['selected_source_doi']])]:
        roles = Counter(d['role'] for d in ds)
        composition[label] = {'n': len(ds), 'roles': dict(roles), 'reply_share': roles['reply'] / len(ds)}
    recovery, bounds = defaultdict(Counter), {}
    for family in families:
        ds = [d for d in documents if d['family'] == family['family']]
        present = {d['source_doi'] for d in ds if positive(d['endpoint'])}
        def features(ids):
            found = [d for d in ds if d['source_doi'] in ids]
            return {'nonempty': bool(found), 'reply': any(d['role'] == 'reply' for d in found),
                    'multistep': len({d['step_id'] for d in found}) > 1, 'complete': len(found) == len(ds)}
        for rank in sorted({int(d['step_rank']) for d in ds}):
            added = {d['source_doi'] for d in ds if int(d['step_rank']) == rank and positive(d['augmented'])} - present
            if not added:
                continue
            before, after = features(present), features(present | added)
            recovery[rank]['steps'] += 1
            recovery[rank]['documents'] += len(added)
            for key in before:
                recovery[rank][key] += after[key] - before[key]
            present |= added
    for route in ['endpoint', 'augmented']:
        result = Counter()
        for f in families:
            ds = [d for d in documents if d['family'] == f['family']]
            present = [d for d in ds if positive(d[route])]
            missing, unresolved = len(ds) - len(present), f['unresolved_rows']
            result['observed_missing_objects'] += missing
            result['minimum_missing_objects'] += max(0, missing - unresolved)
            result['maximum_complete_histories'] += missing <= unresolved
            result['minimum_missed_replies'] += bool(f['source_reply'] and not any(d['role'] == 'reply' for d in present) and unresolved == 0)
            result['minimum_missed_multistep'] += bool(f['source_multistep'] and len({d['step_id'] for d in present}) + unresolved <= 1)
        bounds[route] = dict(result)
    med = [r for r in census if positive(r['exact_MED'])]
    fields = ['native_provider', 'enriched_provider', 'native_review', 'enriched_review', 'review_gain',
              'native_review_url', 'enriched_review_url', 'admissible_EEB_fallback', 'combined_event_presence']
    discovery = {'all_families': len(census), 'vor_families': sum(positive(r['has_vor']) for r in census),
                 'exact_med_families': len(med), **{k: sum(int(r[k]) for r in med) for k in fields}}
    older = [r for r in med if float(r['first_vor_age_days']) >= 90]
    discovery['older_vor'] = {'n': len(older), **{k: sum(int(r[k]) for r in older) for k in ['native_review', 'enriched_review', 'review_gain']}}
    residual = records('elife_residual.csv')
    discovery['residual'] = {'n': len(residual), 'upstream_positive': sum(positive(r['upstream_review']) for r in residual),
                             'operational_fallback': sum(positive(r['eligible_declared_EEB_fallback']) for r in residual)}
    current = records('elife_assessments.csv')
    comparisons = {key: dict(Counter(r[key + '_status'] for r in current)) for key in ['latest_dated', 'forward_typed']}
    api = records('elife_publisher_api.csv')
    comparisons['publisher_api'] = {'n': len(api), **{k: sum(int(r[k]) for r in api) for k in ['doi_concordance', 'strength_concordance', 'abstained']}}
    bmc_groups = defaultdict(list)
    for r in bmc:
        bmc_groups[r['doi']].append(r)
    preserved = {doi: preserved_attachment_set(group) for doi, group in bmc_groups.items()}
    bmc_result = {'families': len(bmc_groups), 'documents': len(bmc), 'roles': dict(Counter(r['role'] for r in bmc)),
                  'preserved_sets': sum(preserved.values()), 'conditional': estimate(409, 397, 60,
                    sum(preserved[k] for k, g in bmc_groups.items() if g[0]['cohort'] == 'extension'),
                    sum(preserved[k] for k, g in bmc_groups.items() if g[0]['cohort'] == 'initial'))}
    fields = ['event_present', 'event_with_url', 'direct_pointer_pdf', 'pointer_homepage', 'source_pdf_recovered']
    embo_result = {'groups': {}, 'conditional': {}, 'extension_event_rows': sum(int(r['matching_event_rows']) for r in embo if r['cohort'] == 'extension')}
    for group in ['EMBO journals', 'Life Science Alliance']:
        ds = [r for r in embo if r['cohort'] == 'extension' and r['journal_group'] == group]
        embo_result['groups'][group] = {'n': len(ds), **{k: sum(positive(r[k]) for r in ds) for k in fields}}
    for key in fields[:3]:
        embo_result['conditional'][key] = estimate(1207, 1205, 30,
            sum(positive(r[key]) for r in embo if r['cohort'] == 'extension'),
            sum(positive(r[key]) for r in embo if r['cohort'] != 'extension'))
    supporting = json.loads((DATA / 'supporting_records.json').read_text())
    content = supporting['content_validation']
    historical = records('elife_assessment_histories.csv')
    supporting_results = {'heading_agreement': sum(int(r['rows']) for r in supporting['heading_baseline'] if r['heading_role'] == r['source_role']),
                          'heading_total': sum(int(r['rows']) for r in supporting['heading_baseline']),
                          'content_n': len(content), 'word_sequence_equal': sum(int(r['word_sequence_equal']) for r in content),
                          'only_list_marker_difference': sum(int(r['only_numeric_list_marker_differences']) for r in content),
                          'historical': {k: sum(int(r[k]) for r in historical) for k in historical[0] if k.endswith(('_lower', '_upper', '_difference'))}}
    result = {'selection': selection, 'elife_counts': counts, 'elife_conditional': conditional,
              'roles': role_groups['all']['roles'], 'first_weight': role_groups['all']['weight'],
              'role_standardization': role_groups, 'corpus_composition': composition, 'step_coverage': step_coverage,
              'history_patterns': histories, 'recovery_by_position': dict(recovery), 'favorable_bounds': bounds,
              'precision_resolution': precision, 'source_age': age, 'discovery': discovery,
              'current_assessments': comparisons, 'sampling_sensitivity': sampling_sensitivity(families),
              'bmc': bmc_result, 'embo': embo_result, 'supporting': supporting_results,
              'text_identity': text_identity(documents, records('elife_text_identity.csv'))}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'statistics.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    tables = {'family_outcomes.csv': families,
              'step_coverage.csv': [{'position': position, **values} for position, values in step_coverage.items()],
              'conditional_estimates.csv': [{'outcome': outcome, **values} for outcome, values in conditional.items()]}
    for filename, rows in tables.items():
        with (output / filename).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"eLife: {counts['matched_n']:,}/{counts['source_n']:,} documents; {counts['complete']}/{len(families)} complete histories.")
    print(f"With intermediary evidence: {counts['augmented_n']:,} documents; {counts['augmented_complete']} complete histories.")
    print(f"BMC: {sum(preserved.values())}/{len(preserved)} families recorded as preserved ({len(bmc)} matched attachments).")
    print(f"EMBO-attributed extension: {sum(g['event_present'] for g in embo_result['groups'].values())}/30 represented events; {embo_result['extension_event_rows']} composite-dossier rows.")
    print('Statistics written to', output / 'statistics.json')
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'results')
    parser.add_argument('--skip-input-hashes', action='store_true',
                        help='Allow intentionally edited derived data; all structural checks remain active')
    args = parser.parse_args()
    if not args.skip_input_hashes:
        verify_inputs()
    run(args.output)
