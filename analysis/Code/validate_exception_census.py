"""Independent full audit of unmet endpoint tasks and declared-route fallback."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from validate_raw_provenance import doi, load, table, save_table, endpoint_class, SERVICE_ROLES


def output_rows(documents, query_doi):
    rows = []
    for wrapper in documents:
        document = wrapper.get("docmap", wrapper)
        publisher = (document.get("publisher") or {}).get("name", "")
        for step_id, step in document.get("steps", {}).items():
            input_ids = {doi(value.get("doi")) for value in step.get("inputs", []) if value.get("doi")}
            bound = doi(query_doi) in input_ids
            for action_index, action in enumerate(step.get("actions", [])):
                for output_index, output in enumerate(action.get("outputs", [])):
                    text = "\n".join(value.get("text", "") for value in output.get("content", []) if value.get("text"))
                    rows.append({"publisher": publisher, "type": output.get("type", ""), "input_bound": bound, "text_present": bool(text), "text": text, "locator": [document.get("id"), step_id, action_index, output_index]})
    return rows


def classify(documents, query_doi, http_status=200):
    if http_status != 200:
        return "request_error", [], []
    events = output_rows(documents, query_doi)
    bound = [row for row in events if row["input_bound"]]
    elife = [row for row in bound if row["publisher"].casefold() == "elife"]
    review = [row for row in elife if row["type"] in SERVICE_ROLES]
    if review:
        status = "elife_review_event_present"
    elif elife:
        status = "elife_event_without_review_type"
    elif bound:
        status = "other_publisher_events_only"
    elif events:
        status = "events_without_input_DOI_concordance"
    else:
        status = "no_event_outputs"
    return status, events, review


def heading_kind(text):
    lead = re.sub(r"[*_#>\\]", "", text).strip().casefold()[:160]
    if re.match(r"author\s+(response|reply)", lead):
        return "author_response_heading"
    if lead.startswith("elife assessment"):
        return "assessment_heading"
    if re.match(r"reviewer\s*\d", lead):
        return "reviewer_heading"
    return "other_or_unresolved_heading"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.source / "Results"
    census = args.provenance / "exception_census"
    source_captures = load(base / "epmc_evaluations.json")
    source_routes = {row["family"]: row for row in load(base / "epmc_route_details.json")}
    source_records = load(base / "epmc_route_records.json")
    crossref = load(base / "crossref_records.json")
    cohort = [row for row in table(base / "epmc_route_ledger.csv") if row["has_vor"] == "1" and row["exact_MED"] == "1"]
    residual = {}
    base_successes = 0
    for row in cohort:
        family = row["family"]
        keys = set(source_routes[family]["native_preprint_targets"]) | set(source_routes[family]["strict_bridge_PPR_targets"])
        kind = endpoint_class(keys, source_captures)
        if kind in ("publication_only", "reachable_PPR_no_elife_event", "no_reachable_PPR"):
            residual[family] = kind
        else:
            base_successes += 1
    assert len(cohort) == 3547 and base_successes == 2900 and len(residual) == 647
    plan = load(census / "plan.json")
    manifest = load(census / "manifest.json")
    assert {row["family"] for row in plan["targets"]} == set(residual)
    assert {row["family"] for row in manifest} == set(residual)
    assert len(manifest) == len(plan["targets"]) == 647
    assert [(row["family"], row["doi"], row["url"], row["path"]) for row in manifest] == [(row["family"], row["doi"], row["url"], row["path"]) for row in plan["targets"]]
    expected_families = {row["family"]: row for row in table(census / "family_ledger.csv")}
    expected_fallback = {row["family"]: row for row in table(census / "fallback_eligibility.csv")}
    independent = []
    actor_summaries = []
    hash_checks = []
    positive_control = None
    for capture in manifest:
        family = capture["family"]
        path = args.provenance / capture["path"]
        data = path.read_bytes()
        assert capture["status"] == 200 and len(data) == capture["bytes"]
        assert hashlib.sha256(data).hexdigest() == capture["sha256"]
        receipt = load(path.with_suffix(".receipt.json"))
        assert receipt["sha256"] == capture["sha256"]
        assert receipt.get("url") == capture["url"]
        documents = json.loads(data)
        status, events, review = classify(documents, capture["doi"], capture["status"])
        assert status == expected_families[family]["upstream_outcome"]
        assert len(review) == int(expected_families[family]["elife_review_event_n"])
        assert sum(row["text_present"] for row in review) == int(expected_families[family]["elife_review_event_with_text_n"])
        # The operational availability check reads the declared metadata edges
        # directly; it does not consult the source-concordant bridge flag.
        canonical = "10.7554/elife." + family
        external = {doi(edge.get("id")) for edge in crossref[canonical].get("relation", {}).get("has-preprint", []) if edge.get("id-type") == "doi" or "doi.org/" in edge.get("id", "")}
        external = {identifier for identifier in external if identifier and not identifier.startswith("10.7554/elife.")}
        native = {doi(source_records.get(key, {}).get("doi")) for key in source_routes[family]["native_preprint_targets"] if source_records.get(key, {}).get("doi")}
        queried = doi(capture["doi"])
        crossref_available = queried in external
        native_available = queried in native
        fallback = bool(review) and (crossref_available or native_available)
        old = expected_fallback[family]
        assert int(crossref_available) == int(old["Crossref_input_available"])
        assert int(native_available) == int(old["native_PPR_input_available"])
        assert int(fallback) == int(old["eligible_declared_EEB_fallback"])
        headers = Counter(heading_kind(row["text"]) for row in review)
        record = {"family": family, "endpoint_class": residual[family], "query_doi": queried, "status": status, "review_type_outputs": len(review), "review_outputs_with_text": sum(row["text_present"] for row in review), "Crossref_query_DOI_declared": int(crossref_available), "native_PPR_query_DOI_declared": int(native_available), "declared_fallback_success": int(fallback), "reviewer_heading_outputs": headers["reviewer_heading"], "assessment_heading_outputs": headers["assessment_heading"], "author_response_heading_outputs": headers["author_response_heading"], "unresolved_heading_outputs": headers["other_or_unresolved_heading"], "raw_sha256": capture["sha256"], "raw_path": capture["path"]}
        independent.append(record)
        hash_checks.append({"family": family, "path": capture["path"], "sha256": capture["sha256"], "bytes": len(data), "verified": 1})
        if review and positive_control is None:
            positive_control = (documents, queried)

    documents, query = positive_control
    changed_type = copy.deepcopy(documents)
    changed_provider = copy.deepcopy(documents)
    for wrapper in changed_type:
        document = wrapper.get("docmap", wrapper)
        for step in document.get("steps", {}).values():
            for action in step.get("actions", []):
                for output in action.get("outputs", []):
                    if output.get("type") in SERVICE_ROLES:
                        output["type"] = "journal-publication"
    for wrapper in changed_provider:
        document = wrapper.get("docmap", wrapper)
        document["publisher"] = {"name": "another-publisher"}
    controls = {"observed_positive_retained": classify(documents, query)[0] == "elife_review_event_present", "different_query_DOI_not_success": classify(documents, "10.0000/unrelated")[0] != "elife_review_event_present", "publisher_change_not_success": classify(changed_provider, query)[0] != "elife_review_event_present", "publication_event_not_review_success": classify(changed_type, query)[0] != "elife_review_event_present", "HTTP_error_never_absence_or_success": classify(documents, query, 503)[0] == "request_error", "empty_valid_response_is_observed_no_outputs": classify([], query)[0] == "no_event_outputs"}
    assert all(controls.values())
    strata = {}
    for kind in sorted(set(residual.values())):
        subset = [row for row in independent if row["endpoint_class"] == kind]
        strata[kind] = {"n": len(subset), "outcomes": dict(Counter(row["status"] for row in subset)), "upstream_positive": sum(row["review_type_outputs"] > 0 for row in subset), "upstream_positive_with_text": sum(row["review_outputs_with_text"] > 0 for row in subset), "declared_fallback_positive": sum(row["declared_fallback_success"] for row in subset)}
    positives = [row for row in independent if row["review_type_outputs"] > 0]
    totals = {"residual_families": len(independent), "hash_verified_responses": len(hash_checks), "raw_bytes": sum(row["bytes"] for row in hash_checks), "outcomes": dict(Counter(row["status"] for row in independent)), "positive_upstream_type": len(positives), "positive_upstream_with_text": sum(row["review_outputs_with_text"] > 0 for row in independent), "positive_declared_Crossref": sum(row["Crossref_query_DOI_declared"] for row in positives), "positive_declared_Crossref_or_native": sum(row["declared_fallback_success"] for row in positives), "source_only_positive_families": [row["family"] for row in positives if not row["declared_fallback_success"]], "native_only_positive_families": [row["family"] for row in positives if not row["Crossref_query_DOI_declared"] and row["native_PPR_query_DOI_declared"]], "base_service_successes": base_successes, "combined_verified_route_presence": base_successes + sum(row["declared_fallback_success"] for row in positives), "denominator": len(cohort), "strata": strata, "positive_family_heading_evidence": {field: sum(row[field] > 0 for row in positives) for field in ("reviewer_heading_outputs", "assessment_heading_outputs", "author_response_heading_outputs", "unresolved_heading_outputs")}, "boundary_tests": controls, "scope": "All 647 unmet role-qualified endpoint tasks, independently reconstructed. The 512 source-identified positive upstream responses include only 509 whose queried input DOI is independently present in the captured canonical Crossref or native PPR metadata. The other three cannot be credited to that operational route. EEB type review is coarse and includes several documentary roles; the result is review-type event presence with text, not a complete review history or current consensus. Additional declared DOI targets not captured in this source-defined census are not asserted absent. No causal ingestion explanation follows from asynchronous endpoint differences."}
    expected_summary = load(census / "summary.json")
    assert totals["outcomes"] == expected_summary["outcome_counts"]
    expected_fallback_summary = load(census / "fallback_summary.json")
    assert totals["positive_declared_Crossref_or_native"] == expected_fallback_summary["declared_Crossref_or_native_PPR_eligible_recovery_n"]
    assert totals["combined_verified_route_presence"] == expected_fallback_summary["combined_review_presence_n"]
    save_table(args.output / "exception_census_independent.csv", independent)
    save_table(args.output / "exception_census_hash_checks.csv", hash_checks)
    (args.output / "exception_census_validation.json").write_text(json.dumps(totals, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
