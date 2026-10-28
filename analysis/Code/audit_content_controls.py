"""Check every intermediate text variant in the 24 source-content controls."""
from __future__ import annotations
import argparse
import csv
import difflib
import hashlib
import html
import json
import random
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from validate_raw_provenance import source_objects, eeb_objects, timestamp, load, save_table, match


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
    def handle_data(self, value):
        self.values.append(value)


def html_text(value):
    parser = HTMLText()
    parser.feed(value)
    return " ".join(parser.values)


def words(value, is_html):
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"^\s*\d+[.)]\s+", "", value, flags=re.M)
    if is_html:
        value = html_text(value)
    else:
        value = re.sub(r"</?(?:a|img|p|strong|em|b|i|sub|sup|span|div|br|ul|ol|li|blockquote|table|tr|td|th|h[1-6])(?:\s+[^<>\n]*)?\s*/?>", " ", value, flags=re.I)
    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    return tuple(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def leading_role(tokens):
    if tokens[:2] in (("author", "response"), ("author", "reply")):
        return "reply"
    if tokens[:2] == ("elife", "assessment"):
        return "evaluation-summary"
    if len(tokens) > 1 and tokens[0] == "reviewer" and tokens[1].isdigit():
        return "review-article"
    return "unresolved"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plan = load(args.controls / "plan.json")
    raw_plan = {item["family"]: item for item in load(args.provenance / "raw_validation_plan.json")["sample"]}
    # Reconstruct the fixed positive-control sample from independently matched
    # source objects before consulting captured content or lexical agreement.
    frame = {}
    for family, item in raw_plan.items():
        source, _ = source_objects(load(args.provenance / "validation/raw/elife" / (family + ".json")))
        for endpoint in item["endpoints"]:
            response = load(args.provenance / "validation/raw/epmc" / (endpoint.replace("/", "_") + ".json"))
            for row in response.get("evaluationList", {}).get("evaluation", []):
                status, obj = match(row, source)
                if status == "matched":
                    frame[obj["doi"]] = {"family": family, "source_doi": obj["doi"], "source_type": obj["role"], "source_date": obj["time"]}
    rng = random.Random(plan["seed"])
    expected_sample = []
    for role in ("review-article", "evaluation-summary", "reply"):
        candidates = sorted((item for item in frame.values() if item["source_type"] == role), key=lambda item: item["source_doi"])
        expected_sample.extend(rng.sample(candidates, 8))
    observed_sample = [{key: item[key] for key in ("family", "source_doi", "source_type", "source_date")} for item in plan["sample"]]
    assert expected_sample == observed_sample, "Content-control sample differs from its fixed-seed frame"
    receipts = {item["key"]: item for item in load(args.controls / "case_acquisition_receipts.json")}
    results = []
    variants = []
    for item in plan["sample"]:
        family = item["family"]
        raw = (args.controls / "case_captures" / item["key"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipts[item["key"]]["sha256"]
        assert receipts[item["key"]]["status"] == 200
        source, _ = source_objects(load(args.provenance / "validation/raw/elife" / (family + ".json")))
        source_match = [obj for obj in source if obj["doi"] == item["source_doi"] and obj["time"] == item["source_date"]]
        assert len(source_match) == 1 and source_match[0]["role"] == item["source_type"]
        eeb = []
        for number in range(len(raw_plan[family]["original_preprint_dois"])):
            eeb.extend(eeb_objects(load(args.provenance / "validation/raw/eeb" / f"{family}_{number}.json")))
        candidates = [obj for obj in eeb if obj["publisher"] == "elife" and obj["role"] == "review" and obj["time"] == item["source_date"] and bool(obj["input_dois"] & source_match[0]["input_dois"])]
        assert candidates
        source_tokens = words(raw.decode("utf-8"), True)
        token_variants = {words(obj["text"], False) for obj in candidates}
        comparisons = []
        for tokens in token_variants:
            if tokens == source_tokens:
                comparisons.append((1.0, True, False))
            else:
                compare = difflib.SequenceMatcher(a=source_tokens, b=tokens, autojunk=False)
                changed = [source_tokens[i:j] + tokens[k:l] for operation, i, j, k, l in compare.get_opcodes() if operation != "equal"]
                numeric_only = bool(changed) and all(all(token.isdigit() for token in change) for change in changed)
                comparisons.append((compare.ratio(), False, numeric_only))
        record = {"family": family, "source_doi": item["source_doi"], "source_role": item["source_type"], "source_hash_verified": 1, "source_heading_agrees": int(leading_role(source_tokens) == item["source_type"]), "eeb_raw_candidates": len(candidates), "eeb_distinct_raw_texts": len({obj["text"] for obj in candidates}), "eeb_distinct_lexical_variants": len(token_variants), "all_lexical_variants_equal_source": int(all(compare[1] for compare in comparisons)), "some_lexical_variant_equal_source": int(any(compare[1] for compare in comparisons)), "all_differences_only_numeric_tokens": int(all(compare[1] or compare[2] for compare in comparisons)), "minimum_lexical_similarity": min(compare[0] for compare in comparisons), "all_EEB_headings_agree": int(all(leading_role(tokens) == item["source_type"] for tokens in token_variants))}
        results.append(record)
        for number, obj in enumerate(candidates):
            tokens = words(obj["text"], False)
            variants.append({"family": family, "source_doi": item["source_doi"], "variant": number, "source_role": item["source_type"], "eeb_locator": obj["locator"], "eeb_text_sha256": hashlib.sha256(obj["text"].encode("utf-8")).hexdigest(), "lexical_equal_source": int(tokens == source_tokens), "eeb_heading_agrees": int(leading_role(tokens) == item["source_type"]), "token_count": len(tokens)})
    assert len(results) == 24
    report = {"objects": len(results), "raw_text_variants": len(variants), "unique_intermediate_objects": sum(row["eeb_raw_candidates"] == 1 for row in results), "multiple_intermediate_objects": sum(row["eeb_raw_candidates"] > 1 for row in results), "multiple_cases": [row for row in results if row["eeb_raw_candidates"] > 1], "all_variants_lexically_equal_source_objects": sum(row["all_lexical_variants_equal_source"] for row in results), "objects_with_only_numeric_differences": sum(not row["all_lexical_variants_equal_source"] and row["all_differences_only_numeric_tokens"] for row in results), "source_heading_agreement": sum(row["source_heading_agrees"] for row in results), "all_variant_heading_agreement": sum(row["all_EEB_headings_agree"] for row in results), "minimum_similarity_across_all_variants": min(row["minimum_lexical_similarity"] for row in results), "scope": "All intermediate candidates were compared, not only the best-matching text. Word-sequence normalization removes markup and typographic differences; it does not demonstrate byte identity or independent scientific validation. Three controls have multiple raw EEB text variants, but each has one normalized lexical sequence. These controls do not resolve all 24 source objects with intermediate variants in the larger sample."}
    save_table(args.output / "content_controls_independent.csv", results)
    report["sample_reconstructed_exactly"] = True
    save_table(args.output / "content_control_variants.csv", variants)
    (args.output / "content_controls_independent.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
