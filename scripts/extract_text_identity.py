"""Regenerate exact-text fingerprints from retained historical EEB responses.

No network access is used. Source texts remain in the supplied response files.
"""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re
from reproduce import records, normalize_doi, time_key, require


def extract(source_dir, output):
    documents = records('elife_documents.csv')
    index = defaultdict(set)
    for d in documents:
        for value in d['input_dois'].split('|'):
            index[d['family'], normalize_doi(value), time_key(d['published'])].add(d['source_doi'])
    families, result, counts = set(), [], Counter()
    for path in sorted(Path(source_dir).glob('eeb_*.body')):
        match = re.fullmatch(r'eeb_(\d+)(?:_\d+)?\.body', path.name)
        require(match is not None, f'Unrecognized response filename: {path.name}')
        family = match.group(1)
        require(family not in families, f'Multiple response files for family {family}')
        families.add(family)
        payload = json.loads(path.read_text(encoding='utf-8'))
        require(isinstance(payload, list), f'Expected a DocMap list in {path.name}')
        for mi, item in enumerate(payload):
            docmap = item.get('docmap', {})
            if str(docmap.get('publisher', {}).get('name', '')).lower() != 'elife':
                continue
            for sid, step in docmap.get('steps', {}).items():
                inputs = {normalize_doi(i['doi']) for i in step.get('inputs', []) if i.get('doi')}
                for ai, action in enumerate(step.get('actions', [])):
                    for oi, obj in enumerate(action.get('outputs', [])):
                        if obj.get('type') not in ('review', 'reviews-summary', 'reply', 'review-article'):
                            continue
                        counts['eligible_output_occurrences'] += 1
                        try:
                            timestamp = time_key(obj.get('published', ''))
                        except ValueError:
                            counts['ineligible_timestamp'] += 1
                            continue
                        candidates = set().union(*(index[family, value, timestamp] for value in inputs))
                        counts[f'candidate_count_{len(candidates)}'] += 1
                        if len(candidates) != 1:
                            continue
                        text = ' '.join(c['text'] for c in obj.get('content', [])
                                        if isinstance(c, dict) and isinstance(c.get('text'), str)).strip()
                        if not text:
                            counts['matched_without_text'] += 1
                            continue
                        normalized = re.sub(r'\s+', ' ', text).strip().casefold()
                        result.append({'family': family, 'source_doi': next(iter(candidates)),
                                       'source_record': path.name, 'docmap_index': mi,
                                       'step_id': sid, 'action_index': ai, 'output_index': oi,
                                       'normalized_text_sha256': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
                                       'normalized_characters': len(normalized)})
    require(families == {d['family'] for d in documents},
            'Response directory does not contain the complete sampled family set')
    require(bool(result), 'No matched text observations were extracted')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    print(json.dumps({'families': len(families), 'text_observations': len(result),
                      **counts}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True,
                        help='Directory containing the historical eeb_*.body files')
    parser.add_argument('--output', type=Path, required=True, help='Output fingerprint CSV')
    args = parser.parse_args()
    extract(args.source_dir, args.output)
