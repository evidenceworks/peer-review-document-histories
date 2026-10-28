"""Validate role-reconstruction matches against independently fetched source HTML."""
import argparse
import csv
import difflib
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def normalize(value):
    parser = TextParser()
    parser.feed(value)
    return ' '.join(html.unescape(' '.join(parser.parts)).split())


def classify(text):
    lead = text[:180].lower()
    if re.search(r'^author\s+(response|reply)', lead):
        return 'reply'
    if lead.startswith('elife assessment'):
        return 'evaluation-summary'
    if re.match(r'^reviewer\s*(?:#|\d)', lead):
        return 'review-article'
    return 'unresolved'


def word_tokens(value, source_format='html'):
    # EEB serves Markdown and the source endpoint serves HTML. Ignore markup,
    # hyperlink destinations and typography for a separate lexical comparison.
    value = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', value)
    value = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'^\s*\d+[.)]\s+', '', value, flags=re.M)
    # Markdown is not HTML: a literal '<i compared...' in a review must not
    # become a tag that swallows the following paragraph.
    if source_format == 'html':
        value = normalize(value)
    else:
        # EEB Markdown can include single-line HTML image/link tags. Remove
        # complete tags without treating incomplete angle-bracket text as HTML.
        value = re.sub(r'</?(?:a|img|p|strong|em|b|i|sub|sup|span|div|br|ul|ol|li|blockquote|table|tr|td|th|h[1-6])(?:\s+[^<>\n]*)?\s*/?>', ' ', value, flags=re.I)
        value = html.unescape(value)
    value = unicodedata.normalize('NFKC', value).casefold()
    return re.findall(r'[^\W_]+', value, flags=re.UNICODE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--validation', type=Path, default=Path(__file__).parent / 'content_validation')
    ap.add_argument('--eeb', type=Path, default=Path(__file__).parent.parent / 'provenance/matched_eeb_outputs.json')
    args = ap.parse_args()
    plan = json.loads((args.validation / 'plan.json').read_text())
    eeb = json.loads(args.eeb.read_text())
    receipts = {r['key']: r for r in json.loads((args.validation / 'case_acquisition_receipts.json').read_text())}
    rows = []
    for item in plan['sample']:
        raw = (args.validation / 'case_captures' / item['key']).read_bytes()
        receipt = receipts[item['key']]
        assert receipt['status'] == 200
        assert hashlib.sha256(raw).hexdigest() == receipt['sha256']
        content = raw.decode('utf-8')
        text = normalize(content)
        candidates = [r for r in eeb if r['family'] == item['family']
                      and r['publisher'].casefold() == 'elife'
                      and r['published'] == item['source_date'] and r['type'] == 'review']
        unique_texts = {r['text'] for r in candidates}
        norm_texts = {normalize(t) for t in unique_texts}
        tokens = word_tokens(content)
        comparisons = [difflib.SequenceMatcher(None, tokens, word_tokens(t, 'markdown'), autojunk=False) for t in unique_texts]
        best = max(comparisons, key=lambda c: c.ratio())
        changes = [best.a[i:j] + best.b[k:l] for op, i, j, k, l in best.get_opcodes() if op != 'equal']
        numeric_markers_only = bool(changes) and all(all(t.isdigit() for t in part) for part in changes)
        row = {k: item[k] for k in ('family', 'source_doi', 'source_type', 'source_date')}
        row.update(content_sha256=receipt['sha256'], eeb_candidates=len(candidates),
                   eeb_unique_texts=len(unique_texts), eeb_unique_normalized_texts=len(norm_texts),
                   source_bytes_equal_eeb_text=int(content in unique_texts),
                   normalized_source_equals_eeb=int(text in norm_texts),
                   content_role=classify(text), role_agrees=int(classify(text) == item['source_type']),
                   word_sequence_equal=int(best.a == best.b), word_sequence_similarity=round(best.ratio(), 9),
                   only_numeric_list_marker_differences=int(numeric_markers_only),
                   source_characters=len(text), source_heading=text[:85])
        rows.append(row)
    path = args.validation / 'content_validation.csv'
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        'plan_sha256': hashlib.sha256((args.validation / 'plan.json').read_bytes()).hexdigest(),
        'selected_objects': len(rows),
        'source_roles': dict(Counter(r['source_type'] for r in rows)),
        'successful_hash_verified_captures': len(rows),
        'source_bytes_equal_eeb_text': sum(r['source_bytes_equal_eeb_text'] for r in rows),
        'normalized_source_equals_eeb': sum(r['normalized_source_equals_eeb'] for r in rows),
        'source_content_heading_role_agreement': sum(r['role_agrees'] for r in rows),
        'word_sequence_equal': sum(r['word_sequence_equal'] for r in rows),
        'only_numeric_list_marker_differences': sum(r['only_numeric_list_marker_differences'] for r in rows),
        'minimum_word_sequence_similarity': min(r['word_sequence_similarity'] for r in rows),
        'eeb_unique_normalized_text_one': sum(r['eeb_unique_normalized_texts'] == 1 for r in rows),
        'scope': 'Eight fixed-seed matched objects per role. Validates sampled positive matches using independently fetched complete official HTML. Not a population accuracy estimate or a validation of scientific truth.'
    }
    (args.validation / 'content_validation_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    print(json.dumps([{k: r[k] for k in ('source_doi','source_type','word_sequence_equal','word_sequence_similarity','role_agrees')} for r in rows], indent=2))


if __name__ == '__main__':
    main()
