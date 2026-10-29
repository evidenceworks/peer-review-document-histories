"""Independently validate matched provenance from archived response bytes.

The source parser, matcher, sample reconstruction, and API comparator here do
not import the producing analysis. Only focused boundary tests optionally call
its public matcher in order to verify that the delivered rule also abstains.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROLES = {"review-article", "evaluation-summary", "reply"}
SERVICE_ROLES = {"review", "reviews-summary", "referee-report", "review-article"}
PROJECTION = ("id", "doi", "dataOrigin", "platform", "type", "url", "evaluationDate", "dateUpdated")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def table(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def save_table(path, records):
    if not records:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value for key, value in record.items()})


def doi(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().lower().rstrip("/")
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?", value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(timespec="microseconds")


def source_objects(document):
    """Preserve the actual evaluation step inputs, separate from containers."""
    objects = {}
    containers = defaultdict(set)
    all_occurrences = []
    for step_id, step in document.get("steps", {}).items():
        input_bindings = [{"doi": doi(item.get("doi")), "version": str(item.get("versionIdentifier", "")), "url": item.get("url", "")} for item in step.get("inputs", []) if item.get("doi")]
        original_bindings = [item for item in input_bindings if not item["doi"].startswith("10.7554/elife.")]
        for action in step.get("actions", []):
            for output in action.get("outputs", []):
                identifier = doi(output.get("doi"))
                if output.get("type") == "preprint" and identifier:
                    for binding in input_bindings:
                        containers[binding["doi"]].add(identifier)
                if output.get("type") not in ROLES or not identifier:
                    continue
                time = timestamp(output.get("published"))
                key = (identifier, output["type"], time)
                occurrence = {"step": step_id, "inputs": original_bindings, "raw_timestamp": output.get("published")}
                all_occurrences.append((key, occurrence))
                if key not in objects:
                    objects[key] = {"doi": identifier, "role": output["type"], "time": time, "occurrences": [], "input_dois": set(), "input_versions": set(), "input_bindings": [], "containers": [], "participant_roles": set()}
                item = objects[key]
                item["occurrences"].append(occurrence)
                item["input_dois"].update(binding["doi"] for binding in original_bindings)
                item["input_versions"].update(binding["version"] for binding in original_bindings)
                for binding in original_bindings:
                    if binding not in item["input_bindings"]:
                        item["input_bindings"].append(binding)
                item["participant_roles"].update(person.get("role", "") for person in action.get("participants", []))
    for item in objects.values():
        item["containers"] = sorted(containers[item["doi"]])
    return list(objects.values()), all_occurrences


def eeb_objects(documents):
    result = []
    for number, wrapper in enumerate(documents):
        document = wrapper.get("docmap", wrapper)
        publisher = (document.get("publisher") or {}).get("name", "").casefold()
        for step_id, step in document.get("steps", {}).items():
            inputs = {doi(item.get("doi")) for item in step.get("inputs", []) if item.get("doi")}
            versions = {str(item.get("versionIdentifier", "")) for item in step.get("inputs", []) if item.get("versionIdentifier") is not None}
            for action_index, action in enumerate(step.get("actions", [])):
                for output_index, output in enumerate(action.get("outputs", [])):
                    result.append({"locator": [number, step_id, action_index, output_index], "publisher": publisher, "role": output.get("type"), "time": timestamp(output.get("published")), "input_dois": inputs, "input_versions": versions, "output_doi": doi(output.get("doi")), "uri": output.get("uri"), "text": "\n".join(item.get("text", "") for item in output.get("content", []) if item.get("text")), "participant_roles": [person.get("role", "") for person in action.get("participants", [])]})
    return result


def match(row, objects):
    if (row.get("platform") or "").casefold() != "elife":
        return "foreign_provider", None
    if row.get("type") not in {"review", "reviews-summary"}:
        return "not_review_type", None
    stamp = timestamp(row.get("evaluationDate"))
    if stamp is None:
        return "missing_or_insufficient_date", None
    same_date = [item for item in objects if item["time"] == stamp]
    possible = [item for item in same_date if doi(row.get("doi")) in item["input_dois"]]
    if len(possible) > 1:
        return "ambiguous_source_date", None
    if len(possible) == 1:
        return "matched", possible[0]
    return ("source_input_DOI_mismatch" if same_date else "no_source_date_match"), None


def endpoint_class(keys, captures):
    events = [row for key in keys for row in captures.get(key, []) if (row.get("platform") or "").casefold() == "elife"]
    reviews = [row for row in events if row.get("type") in SERVICE_ROLES]
    urls = [row.get("url") for row in reviews if row.get("url")]
    if not keys:
        return "no_reachable_PPR"
    if not events:
        return "reachable_PPR_no_elife_event"
    if not reviews and all(row.get("type") == "journal-publication" for row in events):
        return "publication_only"
    if not reviews:
        return "other_event_only"
    if not urls:
        return "review_no_url"
    if any(urlsplit(url).hostname == "doi.org" for url in urls):
        return "review_with_DOI_url"
    if any(urlsplit(url).hostname == "biorxiv.org" for url in urls):
        return "review_with_generic_url"
    return "review_other_url"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.source / "Results"
    audit = args.provenance
    raw = audit / "validation/raw"
    plan_path = audit / "raw_validation_plan.json"
    plan = load(plan_path)
    plan_digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    archived = load(source / "epmc_evaluations.json")
    routes = {row["family"]: row for row in load(source / "epmc_route_details.json")}
    family_map = {row["family"]: row for row in load(source / "epmc_family_links.json")}
    cohort = [row for row in table(source / "epmc_route_ledger.csv") if row["has_vor"] == "1" and row["exact_MED"] == "1"]
    classes = defaultdict(list)
    for row in cohort:
        keys = set(routes[row["family"]]["native_preprint_targets"]) | set(routes[row["family"]]["strict_bridge_PPR_targets"])
        classes[endpoint_class(keys, archived)].append(row["family"])
    rng = random.Random(plan["seed"])
    selected = []
    for stratum in sorted(classes):
        frame = sorted(classes[stratum])
        for family in rng.sample(frame, min(20, len(frame))):
            selected.append({"family": family, "stratum": stratum, "stratum_N": len(frame), "stratum_n": min(20, len(frame)), "endpoints": sorted(set(routes[family]["native_preprint_targets"]) | set(routes[family]["strict_bridge_PPR_targets"])), "original_preprint_dois": family_map[family]["original_preprint_dois"]})
    assert selected == plan["sample"], "Sample membership, order, or bindings differ"
    assert len({item["family"] for item in selected}) == len(selected) == 120

    capture_plan = load(audit / "validation/capture_plan.json")
    manifests = load(audit / "validation/capture_manifest.json")
    assert capture_plan["frozen_plan_sha256"] == plan_digest
    assert len(manifests) == len(capture_plan["targets"]) == 340
    assert {(row["provider"], row["key"], row["url"]) for row in manifests} == {(row["provider"], row["key"], row["url"]) for row in capture_plan["targets"]}
    hash_checks = []
    for row in manifests:
        path = raw / row["provider"] / (row["key"] + ".json")
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == row["sha256"]
        assert len(content) == row["bytes"] and row["status"] == 200
        receipt = load(path.with_suffix(".receipt.json"))
        assert receipt == row
        hash_checks.append({"provider": row["provider"], "key": row["key"], "sha256": row["sha256"], "bytes": len(content), "pass": 1})

    expected_rows = {(row["family"], row["epmc_record"], str(row["epmc_id"])): row for row in table(audit / "matched_row_repairs.csv")}
    expected_families = {row["family"]: row for row in table(audit / "matched_family_ledger.csv")}
    frozen_targets = {row["family"]: row for row in load(source / "crossref_source_targets.json")}
    source_grades = load(source / "assessment_source_targets.json")
    independent_rows = []
    independent_families = []
    source_counts = Counter()
    all_source_keys = set()
    source_duplicate_occurrences = []
    source_timestamp_collisions = []
    projection_agreement = []
    row_repeats = Counter()
    eeb_lacking_matches = []
    valid_test_case = None
    full_schema = set()
    for item in selected:
        family = item["family"]
        objects, occurrences = source_objects(load(raw / "elife" / (family + ".json")))
        source_counts.update(obj["role"] for obj in objects)
        all_source_keys.update((family, obj["doi"], obj["role"], obj["time"]) for obj in objects)
        for obj in objects:
            if len(obj["occurrences"]) > 1:
                source_duplicate_occurrences.append({"family": family, "doi": obj["doi"], "role": obj["role"], "n": len(obj["occurrences"]), "distinct_inputs": len({json.dumps(occurrence["inputs"], sort_keys=True) for occurrence in obj["occurrences"]})})
        time_index = defaultdict(list)
        for obj in objects:
            time_index[obj["time"]].append(obj)
        for time, candidates in time_index.items():
            if time is not None and len(candidates) > 1:
                source_timestamp_collisions.append({"family": family, "time": time, "doi_role_pairs": [[obj["doi"], obj["role"]] for obj in candidates]})
        eeb = []
        for number in range(len(item["original_preprint_dois"])):
            eeb.extend(eeb_objects(load(raw / "eeb" / f"{family}_{number}.json")))
        local_rows = []
        for endpoint in item["endpoints"]:
            data = load(raw / "epmc" / (endpoint.replace("/", "_") + ".json"))
            endpoint_rows = data.get("evaluationList", {}).get("evaluation", [])
            project = [{field: row.get(field) for field in PROJECTION} for row in endpoint_rows]
            assert project == archived[endpoint], endpoint
            projection_agreement.append(endpoint)
            for row in endpoint_rows:
                full_schema.update(row)
                if (row.get("platform") or "").casefold() != "elife":
                    continue
                status, obj = match(row, objects)
                expected = expected_rows[(family, endpoint, str(row.get("id")))]
                assert status == expected["status"], (family, row.get("id"), status, expected["status"])
                assert (obj["doi"] if obj else "") == expected["source_doi"]
                assert (obj["role"] if obj else "") == expected["source_type"]
                time = timestamp(row.get("evaluationDate"))
                eeb_matches = [record for record in eeb if record["publisher"] == "elife" and record["time"] == time and time is not None and doi(row.get("doi")) in record["input_dois"]]
                eeb_review = [record for record in eeb_matches if record["role"] == "review"]
                if obj and len(eeb_review) != 1:
                    eeb_lacking_matches.append({"family": family, "epmc_id": row.get("id"), "source_doi": obj["doi"], "matching_eeb_review_outputs": len(eeb_review)})
                if obj:
                    assert row["type"] == "review"
                    assert doi(row.get("doi")) in obj["input_dois"]
                    assert all(doi(row.get("doi")) in {binding["doi"] for binding in occurrence["inputs"]} for occurrence in obj["occurrences"])
                    if valid_test_case is None:
                        valid_test_case = (copy.deepcopy(row), copy.deepcopy(objects), copy.deepcopy(obj))
                    row_repeats[(family, obj["doi"], obj["role"])] += 1
                record = {"family": family, "stratum": item["stratum"], "epmc_record": endpoint, "epmc_id": row.get("id"), "epmc_type": row.get("type"), "epmc_doi": doi(row.get("doi")), "epmc_date": time or "", "status": status, "source_doi": obj["doi"] if obj else "", "source_role": obj["role"] if obj else "", "source_actual_input_dois": sorted(obj["input_dois"]) if obj else [], "source_actual_input_versions": sorted(obj["input_versions"]) if obj else [], "source_actual_input_bindings": obj["input_bindings"] if obj else [], "source_associated_RP_containers": obj["containers"] if obj else [], "source_participant_roles": sorted(obj["participant_roles"]) if obj else [], "eeb_exact_provider_inputDOI_timestamp_candidates": len(eeb_matches), "eeb_exact_provider_inputDOI_timestamp_review_candidates": len(eeb_review), "eeb_matched_participant_role_sets": sorted({tuple(record["participant_roles"]) for record in eeb_review}), "eeb_any_peer_reviewer_participant": int(any("peer-reviewer" in record["participant_roles"] for record in eeb_review)), "eeb_all_candidates_peer_reviewer_participant": int(bool(eeb_review) and all("peer-reviewer" in record["participant_roles"] for record in eeb_review)), "eeb_matched_distinct_texts": len({record["text"] for record in eeb_review}), "epmc_title_present": int(bool(row.get("title"))), "epmc_evaluators": (row.get("evaluatorsList") or {}).get("evaluator", []), "source_identifier_literal_in_EPMC_row": int(obj is not None and obj["doi"] in json.dumps(row).lower())}
                record["eeb_alternative_output_dois"] = sorted({candidate["output_doi"] for candidate in eeb_review if candidate["output_doi"]})
                record["eeb_candidates_with_nonempty_output_DOI"] = sum(bool(candidate["output_doi"]) for candidate in eeb_review)
                record["eeb_candidates_with_source_output_DOI"] = sum(candidate["output_doi"] == obj["doi"] for candidate in eeb_review) if obj else 0
                record["eeb_candidates_with_explicit_input_version"] = sum(bool(candidate["input_versions"]) for candidate in eeb_review)
                local_rows.append(record)
                independent_rows.append(record)
        matched = [row for row in local_rows if row["status"] == "matched"]
        summaries = {obj["doi"] for obj in objects if obj["role"] == "evaluation-summary"}
        obtained_summaries = {row["source_doi"] for row in matched if row["source_role"] == "evaluation-summary"}
        grade_differences = {identifier for identifier in obtained_summaries if identifier in source_grades and set(source_grades[identifier]["labels"]) != set(frozen_targets[family]["labels"])}
        family_summary = {"family": family, "stratum": item["stratum"], "N": item["stratum_N"], "sample_n": item["stratum_n"], "source_objects": len(objects), "source_reports": sum(obj["role"] == "review-article" for obj in objects), "source_summaries": len(summaries), "source_replies": sum(obj["role"] == "reply" for obj in objects), "eeb_review_present": int(any(obj["publisher"] == "elife" and obj["role"] == "review" for obj in eeb)), "epmc_review_present": int(any(row["epmc_type"] in {"review", "reviews-summary"} for row in local_rows)), "matched_rows": len(matched), "unique_source_objects": len({row["source_doi"] for row in matched}), "unique_reports": len({row["source_doi"] for row in matched if row["source_role"] == "review-article"}), "unique_summaries": len(obtained_summaries), "unique_replies": len({row["source_doi"] for row in matched if row["source_role"] == "reply"}), "full_summary_coverage": int(bool(summaries) and obtained_summaries == summaries), "summary_grade_disagreement_current": len(grade_differences)}
        expected = expected_families[family]
        mapping = {"source_objects": "source_outputs", "source_reports": "source_reports", "source_summaries": "source_summaries", "source_replies": "source_replies", "matched_rows": "epmc_matched_rows", "unique_source_objects": "epmc_unique_source_objects", "unique_reports": "epmc_unique_reports", "unique_summaries": "epmc_unique_summaries", "unique_replies": "epmc_unique_replies", "full_summary_coverage": "source_summary_full_coverage", "summary_grade_disagreement_current": "retrieved_summary_set_disagrees_current_n"}
        assert all(family_summary[key] == int(expected[original]) for key, original in mapping.items()), family
        independent_families.append(family_summary)

    strata = []
    original_strata = {row["stratum"]: row for row in table(audit / "matched_stratum_summary.csv")}
    for stratum in sorted(classes):
        values = [row for row in independent_families if row["stratum"] == stratum]
        row = {"stratum": stratum, "N": len(classes[stratum]), "sample_n": len(values), "source_all_have_review_reports": int(all(value["source_reports"] > 0 for value in values)), "eeb_review_present_n": sum(value["eeb_review_present"] for value in values), "epmc_review_present_n": sum(value["epmc_review_present"] for value in values), "epmc_role_repaired_n": sum(value["matched_rows"] > 0 for value in values), "epmc_author_reply_as_review_n": sum(value["unique_replies"] > 0 for value in values), "epmc_summary_as_review_n": sum(value["unique_summaries"] > 0 for value in values), "full_source_summary_coverage_n": sum(value["full_summary_coverage"] for value in values), "retrieved_summary_disagrees_current_n": sum(value["summary_grade_disagreement_current"] > 0 for value in values), "source_outputs": sum(value["source_objects"] for value in values), "epmc_matched_rows": sum(value["matched_rows"] for value in values), "epmc_unique_source_objects": sum(value["unique_source_objects"] for value in values)}
        assert all(str(value) == original_strata[stratum][key] for key, value in row.items()), stratum
        strata.append(row)

    # Current publisher API: source selection precedes gold scoring.
    publisher_manifest = load(audit / "validation/publisher_comparator_manifest.json")
    assert {row["family"] for row in publisher_manifest} == {item["family"] for item in selected}
    publisher_rows = []
    for receipt in publisher_manifest:
        family = receipt["family"]
        path = raw / "publisher_versions" / (family + ".json")
        content = path.read_bytes()
        assert receipt["sha256"] == hashlib.sha256(content).hexdigest() and receipt["bytes"] == len(content) and receipt["status"] == 200
        assert receipt == load(path.with_suffix(".receipt.json"))
        versions = json.loads(content).get("versions", [])
        candidates = [version for version in versions if version.get("status") == "vor"]
        highest = max(((int(version.get("version", -1)), version.get("versionDate") or "") for version in candidates), default=None)
        latest = [version for version in candidates if (int(version.get("version", -1)), version.get("versionDate") or "") == highest]
        chosen = latest[0] if len(latest) == 1 else None
        assessment = (chosen.get("elifeAssessment") or {}) if chosen else {}
        selected_doi = doi(assessment.get("doi"))
        selected_strength = sorted({value.lower() for value in assessment.get("strength", [])})
        gold = frozen_targets[family]
        publisher_rows.append({"family": family, "chosen_production_version": chosen.get("version") if chosen else "", "chosen_date": chosen.get("versionDate") if chosen else "", "selected_consensus_doi": selected_doi, "selected_strength": selected_strength, "abstained": int(not selected_doi), "doi_concordance": int(selected_doi == gold["assessment_doi"]), "strength_concordance": int(set(selected_strength) == set(gold["labels"])), "sha256": receipt["sha256"]})
    original_publisher = {row["family"]: row for row in table(audit / "publisher_comparator.csv")}
    for row in publisher_rows:
        prior = original_publisher[row["family"]]
        assert row["selected_consensus_doi"] == prior["selected_assessment_doi"]
        assert "|".join(row["selected_strength"]) == prior["selected_strength"]
        for field in ("doi_concordance", "strength_concordance", "abstained"):
            assert row[field] == int(prior[field])

    # Meaningful boundary tests use a real matched row and real source objects.
    row, objects, selected_object = valid_test_case
    tests = {"real_row_matches": match(row, objects)[0] == "matched"}
    wrong_doi = {**row, "doi": "10.0000/different-work"}
    short_date = {**row, "evaluationDate": row["evaluationDate"][:10]}
    wrong_role = {**row, "type": "journal-publication"}
    another_step = copy.deepcopy(objects)
    extra_step = copy.deepcopy(selected_object)
    extra_step["time"] = "1900-01-01T01:02:03.123456"
    extra_step["input_dois"] = {"10.0000/different-work"}
    another_step.append(extra_step)
    ambiguous = copy.deepcopy(objects)
    extra = copy.deepcopy(selected_object)
    extra["doi"] += ".conflict"
    ambiguous.append(extra)
    role_conflict = copy.deepcopy(objects)
    extra_role = copy.deepcopy(selected_object)
    extra_role["role"] = "reply" if extra_role["role"] != "reply" else "evaluation-summary"
    role_conflict.append(extra_role)
    tests.update(perturbed_DOI_abstains=match(wrong_doi, objects)[1] is None, coarsened_date_abstains=match(short_date, objects)[1] is None, event_role_change_abstains=match(wrong_role, objects)[1] is None, ambiguous_timestamp_abstains=match(row, ambiguous)[1] is None, same_identifier_conflicting_source_role_abstains=match(row, role_conflict)[1] is None, wrong_provider_abstains=match({**row, "platform": "different-provider"}, objects)[1] is None)
    tests["DOI_only_in_another_evaluation_step_abstains"] = match(wrong_doi, another_step)[1] is None
    # Verify the producing matcher against the same real-data perturbations.
    spec = importlib.util.spec_from_file_location("producing_matcher", audit / "matched_provenance.py")
    producing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producing)
    def production_index(items):
        index = defaultdict(list)
        for obj in items:
            index[obj["time"]].append({"source_doi": obj["doi"], "source_type": obj["role"], "declared_input_dois": sorted(obj["input_dois"])})
        return index
    tests["producing_real_row_matches"] = producing.repair_decision(row, production_index(objects))[0] == "matched"
    for name, changed_row, changed_objects in (("perturbed_DOI", wrong_doi, objects), ("DOI_only_in_another_evaluation_step", wrong_doi, another_step), ("coarsened_date", short_date, objects), ("event_role_change", wrong_role, objects), ("ambiguous_timestamp", row, ambiguous), ("conflicting_source_role", row, role_conflict)):
        tests["producing_" + name + "_abstains"] = producing.repair_decision(changed_row, production_index(changed_objects))[2] is None
    assert all(tests.values()), tests

    matched_rows = [row for row in independent_rows if row["status"] == "matched"]
    unmatched_review = [row for row in independent_rows if row["epmc_type"] in {"review", "reviews-summary"} and row["status"] != "matched"]
    actor_role_cases = [row for row in matched_rows if row["source_role"] == "reply" and row["eeb_any_peer_reviewer_participant"]]
    eeb_variant_cases = [row for row in matched_rows if row["eeb_exact_provider_inputDOI_timestamp_review_candidates"] > 1]
    alternate_identifiers = [row for row in matched_rows if row["eeb_alternative_output_dois"]]
    alternative_bindings = defaultdict(set)
    alternative_roles = defaultdict(set)
    alternative_families = defaultdict(set)
    for row in alternate_identifiers:
        for identifier in row["eeb_alternative_output_dois"]:
            alternative_bindings[identifier].add(row["source_doi"])
            alternative_roles[identifier].add(row["source_role"])
            alternative_families[identifier].add(row["family"])
    report = {
        "status": "CORE_COUNTS_VERIFIED_WITH_INTERMEDIATE_MULTIPLICITY", "sample_reconstructed_exactly": True, "sample_families": len(selected), "plan_sha256": plan_digest, "population": len(cohort), "stratum_sizes": {key: len(value) for key, value in sorted(classes.items())},
        "raw_captures_verified": len(hash_checks), "raw_bytes_verified": sum(row["bytes"] for row in hash_checks), "publisher_API_captures_verified": len(publisher_rows), "EPMC_projections_equal_archive": len(projection_agreement),
        "raw_EPMC_keys": sorted(full_schema), "source_object_roles": dict(source_counts), "source_distinct_object_keys": len(all_source_keys), "source_duplicate_occurrences": source_duplicate_occurrences, "source_timestamp_collisions": source_timestamp_collisions,
        "service_row_status": dict(Counter(row["status"] for row in independent_rows)), "matched_source_roles_by_row": dict(Counter(row["source_role"] for row in matched_rows)), "matched_source_roles_unique": dict(Counter(role for family, identifier, role in row_repeats)), "unique_matched_source_objects": len(row_repeats), "duplicate_matched_service_rows_beyond_unique_objects": sum(row_repeats.values()) - len(row_repeats),
        "all_matched_EPMC_rows_have_type_review": all(row["epmc_type"] == "review" for row in matched_rows), "all_matches_bind_to_actual_evaluation_step_input": True,
        "matched_rows_without_unique_EEB_provider_inputDOI_timestamp_review": eeb_lacking_matches, "matched_rows_with_nonempty_title": sum(row["epmc_title_present"] for row in matched_rows), "unmatched_review_rows": unmatched_review,
        "EEB_matching_multiplicity": {"no_match_rows": sum(row["eeb_exact_provider_inputDOI_timestamp_review_candidates"] == 0 for row in matched_rows), "unique_match_rows": sum(row["eeb_exact_provider_inputDOI_timestamp_review_candidates"] == 1 for row in matched_rows), "multiple_match_rows": len(eeb_variant_cases), "multiple_match_distinct_source_objects": len({(row["family"], row["source_doi"]) for row in eeb_variant_cases}), "multiple_match_families": len({row["family"] for row in eeb_variant_cases}), "multiple_match_rows_with_distinct_texts": sum(row["eeb_matched_distinct_texts"] > 1 for row in eeb_variant_cases)},
        "source_reply_with_EEB_peer_reviewer_participant": {"matched_rows": len(actor_role_cases), "distinct_source_reply_objects": len({(row["family"], row["source_doi"]) for row in actor_role_cases}), "families": len({row["family"] for row in actor_role_cases}), "all_matching_EEB_variants_agree": all(row["eeb_all_candidates_peer_reviewer_participant"] for row in actor_role_cases)},
        "matched_rows_literally_exposing_source_object_DOI": sum(row["source_identifier_literal_in_EPMC_row"] for row in matched_rows),
        "EEB_identifier_and_input_version_fields": {"candidate_bindings_including_multiplicity": sum(row["eeb_exact_provider_inputDOI_timestamp_review_candidates"] for row in matched_rows), "matched_rows_with_alternative_output_DOI": len(alternate_identifiers), "distinct_alternative_output_DOIs": len({identifier for row in alternate_identifiers for identifier in row["eeb_alternative_output_dois"]}), "distinct_source_objects_with_alternative_EEB_DOI": len({row["source_doi"] for row in alternate_identifiers}), "candidate_bindings_with_eLife_source_output_DOI": sum(row["eeb_candidates_with_source_output_DOI"] for row in matched_rows), "candidate_bindings_with_explicit_evaluated_input_version": sum(row["eeb_candidates_with_explicit_input_version"] for row in matched_rows), "scope": "An alternative intermediate DOI can identify a separate representation. Missing eLife source-object identifiers does not mean all intermediate outputs lack identifiers."},
        "strata": strata, "publisher_current_selection": {"n": len(publisher_rows), "concordant_DOI": sum(row["doi_concordance"] for row in publisher_rows), "concordant_strength_set": sum(row["strength_concordance"] for row in publisher_rows), "abstained": sum(row["abstained"] for row in publisher_rows), "abstaining_families": [row["family"] for row in publisher_rows if row["abstained"]]},
        "boundary_tests": tests,
        "interpretation": "Separate implementation from raw captures. Sample facts are not pooled population percentages. Matching uses the actual evaluation-step original DOI and exact full timestamp; source role, DOI, input version, and associated RP container are returned only after unique source linkage. Frozen current DOI and labels are used only for downstream scoring. All 458 matched EPMC rows have at least one EEB output with the same original input DOI, exact timestamp, eLife provider, and type review; only 404 have a unique intermediate output, while 54 match two or three variants. Unique source-role recovery remains supported, but unique full three-stage content identity is not established for those variants. No causal reader harm is measured."
    }
    save_table(args.output / "raw_provenance_rows_independent.csv", independent_rows)
    report["EEB_identifier_and_input_version_fields"].update({"candidate_bindings_with_nonempty_alternative_DOI": sum(row["eeb_candidates_with_nonempty_output_DOI"] for row in matched_rows), "families_with_alternative_output_DOI": len({row["family"] for row in alternate_identifiers}), "alternative_identifier_prefixes": sorted({identifier.split("/", 1)[0] + "/" + identifier.split("/", 1)[1].split(".", 1)[0] + "." for identifier in alternative_bindings}), "distinct_source_roles_with_alternative_DOI": dict(Counter(role for identifier, role in {(row["source_doi"], row["source_role"]) for row in alternate_identifiers})), "source_objects_per_alternative_DOI_distribution": dict(sorted(Counter(len(values) for values in alternative_bindings.values()).items()))})
    report["EEB_identifier_and_input_version_fields"].update({"alternative_DOI_source_role_sets": dict(Counter("|".join(sorted(values)) for values in alternative_roles.values())), "alternative_DOIs_spanning_multiple_families": sum(len(values) > 1 for values in alternative_families.values()), "distinct_alternative_DOIs_by_source_role": dict(Counter(role for values in alternative_roles.values() for role in values))})
    identifier_map = [{"alternative_DOI": identifier, "families": "|".join(sorted(alternative_families[identifier])), "source_roles": "|".join(sorted(alternative_roles[identifier])), "source_DOI_n": len(alternative_bindings[identifier]), "source_DOIs": "|".join(sorted(alternative_bindings[identifier]))} for identifier in sorted(alternative_bindings)]
    save_table(args.output / "alternative_EEB_identifier_map.csv", identifier_map)
    save_table(args.output / "raw_provenance_families_independent.csv", independent_families)
    save_table(args.output / "raw_provenance_strata_independent.csv", strata)
    save_table(args.output / "raw_capture_hash_checks.csv", hash_checks)
    save_table(args.output / "publisher_current_independent.csv", publisher_rows)
    (args.output / "raw_provenance_validation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "sample_reconstructed_exactly", "raw_captures_verified", "publisher_API_captures_verified", "EPMC_projections_equal_archive", "matched_source_roles_by_row", "matched_source_roles_unique", "unique_matched_source_objects", "duplicate_matched_service_rows_beyond_unique_objects", "EEB_matching_multiplicity", "source_reply_with_EEB_peer_reviewer_participant", "publisher_current_selection", "boundary_tests")}, indent=2))


if __name__ == "__main__":
    main()
