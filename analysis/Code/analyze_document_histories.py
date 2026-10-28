"""Source-set structure and retrieval diagnostics for the frozen aOIR sample.

Standard library only. Reconstructs publisher objects and endpoint candidates
from hash-verified raw captures without importing prior analysis programs.
New analyses retain the original 20-per-stratum draw and report sample counts;
they do not estimate population prevalence from pooled sample proportions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import re
import statistics
import sys
from urllib.parse import unquote

sys.dont_write_bytecode = True
ROLES = ("review-article", "evaluation-summary", "reply")
EVENT_TYPES = {"review", "reviews-summary", "referee-report", "review-article"}
INSTANT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def no_duplicate_keys(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def loads(data):
    return json.loads(data, object_pairs_hook=no_duplicate_keys)


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def doi(value):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value or "").strip(), flags=re.I).lower().rstrip("/")


def instant(value, precision="original"):
    match = INSTANT.fullmatch(str(value or ""))
    if not match:
        return ""
    day, clock, fraction, offset = match.groups()
    try:
        value = datetime.fromisoformat(day + "T" + clock + ("+00:00" if offset in (None, "Z") else offset))
    except ValueError:
        return ""
    second = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if precision == "day":
        return second[:10]
    if precision == "seconds":
        return second
    require(precision == "original", "Unknown precision")
    fraction = (fraction or "").rstrip("0")
    return second + ("." + fraction if fraction else "") + "Z"


def chronological_key(value):
    normalized = instant(value)
    require(normalized, "Unorderable source time")
    return normalized[:19], Decimal("0." + normalized[20:-1]) if len(normalized) > 20 else Decimal(0)


def write_csv(path, rows):
    require(bool(rows), "Unexpected empty table: " + str(path))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def source_inventory(document):
    require(document.get("publisher", {}).get("name", "").casefold() == "elife", "Wrong publisher")
    steps = document["steps"]
    chain, node = [], document["first-step"]
    while node:
        require(node in steps and node not in chain, "Broken or cyclic publisher next-step chain")
        chain.append(node)
        node = steps[node].get("next-step")
    objects, evaluation_steps = {}, []
    for step_id, step in steps.items():
        bindings = sorted({(doi(item.get("doi")), str(item.get("versionIdentifier", "")), str(item.get("url", "")))
                           for item in step.get("inputs", []) if doi(item.get("doi")) and not doi(item.get("doi")).startswith("10.7554/elife.")})
        step_objects = []
        for ai, action in enumerate(step.get("actions", [])):
            for oi, output in enumerate(action.get("outputs", [])):
                if output.get("type") not in ROLES or not output.get("doi"):
                    continue
                identifier = doi(output["doi"])
                record = {"source_doi": identifier, "source_role": output["type"], "source_step": step_id,
                          "source_time_utc": instant(output.get("published")), "input_bindings": bindings,
                          "path": f"steps/{step_id}/actions/{ai}/outputs/{oi}"}
                require(record["source_time_utc"], "Undated source object: " + identifier)
                if identifier in objects:
                    require(objects[identifier] == record, "Conflicting repeated source DOI: " + identifier)
                else:
                    objects[identifier] = record
                    step_objects.append(identifier)
        if step_objects:
            require(step_id in chain, "Evaluation step is outside declared chain")
            evaluation_steps.append({"step": step_id, "chain_position": chain.index(step_id), "objects": step_objects,
                                     "bindings": bindings})
    evaluation_steps.sort(key=lambda item: item["chain_position"])
    for rank, step in enumerate(evaluation_steps, 1):
        step["rank"] = rank
        step["first_object_time"] = min((objects[x]["source_time_utc"] for x in step["objects"]), key=chronological_key)
        step["last_object_time"] = max((objects[x]["source_time_utc"] for x in step["objects"]), key=chronological_key)
        for identifier in step["objects"]:
            objects[identifier]["step_rank"] = rank
    return objects, evaluation_steps


def intermediate_inventory(documents, query_index):
    result = []
    for di, wrapper in enumerate(documents):
        document = wrapper.get("docmap", wrapper)
        provider = document.get("publisher", {}).get("name", "")
        for step_id, step in document.get("steps", {}).items():
            inputs = {doi(item.get("doi")) for item in step.get("inputs", []) if item.get("doi")}
            for ai, action in enumerate(step.get("actions", [])):
                for oi, output in enumerate(action.get("outputs", [])):
                    result.append({"key": f"query{query_index}/doc{di}/steps/{step_id}/actions/{ai}/outputs/{oi}",
                                       "provider": provider, "output_type": output.get("type", ""),
                                       "eligible": provider.casefold() == "elife" and output.get("type") in EVENT_TYPES,
                                       "input_dois": inputs, "time": instant(output.get("published")),
                                       "source_output_doi": doi(output.get("doi")),
                                       "text": "\n".join(x.get("text", "") for x in output.get("content", []) if x.get("text"))})
    return result


def candidate_ids(objects, row, precision="original"):
    stamp = instant(row.get("evaluationDate"), precision)
    source_doi = doi(row.get("doi"))
    if not stamp or not source_doi:
        return []
    return sorted(identifier for identifier, obj in objects.items()
                  if source_doi in {binding[0] for binding in obj["input_bindings"]}
                  and instant(obj["source_time_utc"], precision) == stamp)


def boundary_controls():
    """Small scientific failure controls, with no modification of frozen data."""
    objects = {"10.test/a": {"input_bindings": [("10.test/input", "1", "")], "source_time_utc": "2024-01-01T12:00:00.1Z"},
               "10.test/b": {"input_bindings": [("10.test/input", "1", "")], "source_time_utc": "2024-01-01T12:00:00.12Z"}}
    row = {"doi": "10.test/input", "evaluationDate": "2024-01-01 12:00:00.1"}
    checks = {
        "fractional_time_order_is_numeric_not_lexicographic": chronological_key(objects["10.test/a"]["source_time_utc"]) < chronological_key(objects["10.test/b"]["source_time_utc"]),
        "original_unique_match": candidate_ids(objects, row) == ["10.test/a"],
        "second_truncation_preserves_both_candidates": candidate_ids(objects, row, "seconds") == ["10.test/a", "10.test/b"],
        "wrong_step_input_doi_cannot_match": candidate_ids(objects, dict(row, doi="10.test/other")) == [],
        "date_only_cannot_match": candidate_ids(objects, dict(row, evaluationDate="2024-01-01")) == [],
        "offset_normalization_preserves_same_instant": instant("2024-01-01T13:00:00.12+01:00") == objects["10.test/b"]["source_time_utc"],
        "arbitrary_fractional_precision_preserved": instant("2024-01-01T12:00:00.1234567Z").endswith(".1234567Z"),
    }
    try:
        source_inventory({"publisher": {"name": "eLife"}, "first-step": "a", "steps": {"a": {"next-step": "a"}}})
    except ValueError:
        checks["cycle_in_declared_source_chain_rejected"] = True
    else:
        checks["cycle_in_declared_source_chain_rejected"] = False
    try:
        loads('{"a":1,"a":2}')
    except ValueError:
        checks["duplicate_JSON_field_rejected"] = True
    else:
        checks["duplicate_JSON_field_rejected"] = False
    require(all(checks.values()), "A scientific boundary control failed")
    return checks


def main(repository, output):
    repository, output = Path(repository).resolve(), Path(output).resolve()
    require(output != repository and repository not in output.parents and output not in repository.parents, "Output must be separate from the read-only repository")
    require(not output.exists(), "Output must be a new directory")
    controls = boundary_controls()
    output.mkdir(parents=True)
    hashes = {}
    def read(relative):
        path = repository / relative
        data = path.read_bytes()
        hashes[relative] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return data
    def read_json(relative):
        return loads(read(relative))
    def read_csv(relative):
        return list(csv.DictReader(read(relative).decode("utf-8-sig").splitlines()))
    plan = read_json("Evidence/Provenance/raw_validation_plan.json")
    capture_manifest = read_json("Evidence/Provenance/validation/capture_manifest.json")
    captures = {(x["provider"], x["key"]): x for x in capture_manifest}
    require(len(captures) == len(capture_manifest) == 340, "Wrong or duplicate sample capture manifest")
    captured_reads = set()
    def capture(provider, key):
        entry = captures[provider, key]
        relative = f"Evidence/Provenance/validation/raw/{provider}/{key}.json"
        data = read(relative)
        require(entry["status"] == 200 and len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"], "Frozen capture integrity failure: " + relative)
        captured_reads.add((provider, key))
        return loads(data), entry

    frame = read_csv("Results/Provenance/endpoint_family_ledger.csv")
    population = [x for x in frame if x["has_vor"] == x["exact_MED"] == "1"]
    rng = random.Random(plan["seed"])
    draw = []
    for stratum in sorted({x["residual_class"] for x in population}):
        members = sorted((x for x in population if x["residual_class"] == stratum), key=lambda x: x["family"])
        draw.extend((x["family"], stratum) for x in rng.sample(members, min(20, len(members))))
    require(draw == [(x["family"], x["stratum"]) for x in plan["sample"]], "Frozen sample draw differs")

    object_rows, step_rows, family_rows, endpoint_rows, eeb_accounting = [], [], [], [], []
    for selection in plan["sample"]:
        family, stratum = selection["family"], selection["stratum"]
        document, publisher_receipt = capture("elife", family)
        objects, steps = source_inventory(document)
        intermediates = []
        for i, original_doi in enumerate(selection["original_preprint_dois"]):
            eeb, eeb_receipt = capture("eeb", family + "_" + str(i))
            require(doi(eeb_receipt["original_doi"]) == doi(original_doi), "EEB query receipt input differs")
            require(doi(unquote(eeb_receipt["url"]).split("/docmap/", 1)[1]) == doi(original_doi), "EEB query URL input differs")
            every_output = intermediate_inventory(eeb, i)
            counts = Counter((x["provider"], x["output_type"], x["eligible"]) for x in every_output)
            for (provider, output_type, eligible), count in sorted(counts.items()):
                eeb_accounting.append({"family": family, "stratum": stratum, "query_ordinal": i,
                                       "queried_doi": doi(original_doi), "provider": provider, "output_type": output_type,
                                       "outputs": count, "qualifying_eLife_review_type": int(eligible)})
            intermediates.extend(x for x in every_output if x["eligible"])
        row_matches, endpoint_count, unresolved = defaultdict(list), 0, 0
        local_endpoint_rows = []
        for endpoint in selection["endpoints"]:
            response, _ = capture("epmc", endpoint.replace("/", "_"))
            for entry in response.get("evaluationList", {}).get("evaluation", []):
                if entry.get("platform", "").casefold() != "elife" or entry.get("type") not in EVENT_TYPES:
                    continue
                endpoint_count += 1
                found = candidate_ids(objects, entry)
                matched = found[0] if len(found) == 1 else ""
                if matched:
                    row_matches[matched].append((endpoint, str(entry["id"])))
                else:
                    unresolved += 1
                row = {"family": family, "stratum": stratum, "epmc_record": endpoint, "epmc_id": str(entry["id"]),
                       "source_doi": matched, "source_role": objects[matched]["source_role"] if matched else "",
                       "input_doi": doi(entry.get("doi")), "original_timestamp": entry.get("evaluationDate", ""),
                       "original_candidates": compact(found)}
                for precision in ("seconds", "day"):
                    candidates = candidate_ids(objects, entry, precision)
                    row[precision + "_candidate_n"] = len(candidates)
                    row[precision + "_candidates"] = compact(candidates)
                    row[precision + "_same_unique"] = int(bool(matched) and candidates == [matched])
                local_endpoint_rows.append(row)
        endpoint_rows.extend(local_endpoint_rows)
        require(bool(endpoint_count) == stratum.startswith("review_"), "Positive-stratum membership differs")
        if not endpoint_count:
            continue
        capture_date = datetime.fromisoformat(publisher_receipt["capture_utc"]).date()
        queried_dois = {doi(x) for x in selection["original_preprint_dois"]}
        role_counts = {role: Counter() for role in ROLES}
        family_steps = []
        for obj in objects.values():
            bindings = obj["input_bindings"]
            input_dois = {x[0] for x in bindings}
            eeb_matches = [x for x in intermediates if x["time"] == obj["source_time_utc"] and input_dois & x["input_dois"]]
            retrieved = int(bool(row_matches[obj["source_doi"]]))
            role_counts[obj["source_role"]]["source"] += 1
            role_counts[obj["source_role"]]["retrieved"] += retrieved
            object_rows.append({"family": family, "stratum": stratum, "source_doi": obj["source_doi"],
                                "source_role": obj["source_role"], "source_step": obj["source_step"], "step_rank": obj["step_rank"],
                                "source_time_utc": obj["source_time_utc"], "input_bindings": compact(bindings),
                                "all_source_input_dois_queried": int(bool(input_dois) and input_dois <= queried_dois),
                                "source_input_doi_count": len(input_dois), "endpoint_unique_match": retrieved,
                                "matched_endpoint_rows": len(row_matches[obj["source_doi"]]),
                                "matched_row_keys": compact(row_matches[obj["source_doi"]]),
                                "EEB_counterparts": len(eeb_matches), "EEB_counterpart_keys": compact([x["key"] for x in eeb_matches]),
                                "EEB_text_counterparts": sum(bool(x["text"]) for x in eeb_matches),
                                "publication_age_days_at_source_capture": (capture_date - datetime.fromisoformat(obj["source_time_utc"]).date()).days})
        indexed_objects = {x["source_doi"]: x for x in object_rows if x["family"] == family}
        for step in steps:
            source_set = [indexed_objects[x] for x in step["objects"]]
            count = len(source_set)
            found = sum(x["endpoint_unique_match"] for x in source_set)
            eeb = sum(x["EEB_counterparts"] > 0 for x in source_set)
            row = {"family": family, "stratum": stratum, "stratum_N": selection["stratum_N"], "stratum_n": selection["stratum_n"],
                   "source_step": step["step"], "chain_position": step["chain_position"], "step_rank": step["rank"],
                   "family_evaluation_steps": len(steps), "input_bindings": compact(step["bindings"]),
                   "source_objects": count, "source_reports": sum(x["source_role"] == "review-article" for x in source_set),
                   "source_summaries": sum(x["source_role"] == "evaluation-summary" for x in source_set),
                   "source_replies": sum(x["source_role"] == "reply" for x in source_set),
                   "reconstructed_objects": found, "EEB_objects_with_counterpart": eeb,
                   "coverage_state": "complete" if found == count else "none" if found == 0 else "partial",
                   "EEB_coverage_state": "complete" if eeb == count else "none" if eeb == 0 else "partial",
                   "first_object_time_utc": step["first_object_time"], "last_object_time_utc": step["last_object_time"],
                   "youngest_object_age_days": min(x["publication_age_days_at_source_capture"] for x in source_set),
                   "oldest_object_age_days": max(x["publication_age_days_at_source_capture"] for x in source_set),
                   "all_source_input_dois_queried": int(all(x["all_source_input_dois_queried"] for x in source_set)),
                   "source_object_dois": compact(step["objects"])}
            step_rows.append(row)
            family_steps.append(row)
        states = [x["coverage_state"] for x in family_steps]
        found_first_absence = states.index("none") if "none" in states else len(states)
        prefix = all(x == "complete" for x in states[:found_first_absence]) and all(x == "none" for x in states[found_first_absence:])
        intervals_ordered = all(chronological_key(a["last_object_time_utc"]) < chronological_key(b["first_object_time_utc"]) for a, b in zip(family_steps, family_steps[1:]))
        distinct_matched = sum(bool(value) for value in row_matches.values())
        summary = {"family": family, "stratum": stratum, "stratum_N": selection["stratum_N"], "stratum_n": selection["stratum_n"],
                   "endpoint_rows": endpoint_count, "matched_rows": endpoint_count - unresolved, "unresolved_rows": unresolved,
                   "source_objects": len(objects), "reconstructed_objects": distinct_matched,
                   "evaluation_steps": len(steps), "complete_steps": states.count("complete"), "absent_steps": states.count("none"),
                   "partial_steps": states.count("partial"), "coverage_sequence": "/".join(states), "complete_initial_prefix": int(prefix),
                   "strict_time_order_agrees_with_declared_chain": int(intervals_ordered),
                   "all_source_roles_complete": int(len(objects) == distinct_matched)}
        for role in ROLES:
            source, retrieved = role_counts[role]["source"], role_counts[role]["retrieved"]
            summary[role + "_source"] = source
            summary[role + "_retrieved"] = retrieved
            summary[role + "_missing"] = source - retrieved
            summary[role + "_source_present"] = int(source > 0)
            summary[role + "_set_covered"] = int(source == retrieved)
        family_rows.append(summary)

    require(len(captured_reads) == 340, "Not all fixed sample captures were verified")
    require(len(endpoint_rows) == 462 and sum(bool(x["source_doi"]) for x in endpoint_rows) == 458, "Core match count differs")
    require(len(object_rows) == 471 and sum(x["endpoint_unique_match"] for x in object_rows) == 362, "Core object count differs")
    require(len({(x["family"], x["epmc_record"], x["epmc_id"]) for x in endpoint_rows}) == len(endpoint_rows), "Duplicate endpoint row key")

    # Prior results are opened after raw-derived source sets and predictions exist.
    prior_objects = read_csv("Results/ScientificAdditions/Independent/source_object_coverage.csv")
    prior_rows = read_csv("Results/ScientificAdditions/Independent/row_task_diagnostics.csv")
    prior_families = {x["family"]: x for x in read_csv("Results/ScientificAdditions/family_role_coverage.csv")}
    expected_objects = {(x["family"], x["source_doi"]): x for x in prior_objects}
    expected_rows = {(x["family"], x["epmc_record"], x["epmc_id"]): x for x in prior_rows}
    require(len(expected_objects) == len(object_rows) and len(expected_rows) == len(endpoint_rows), "Prior ledger dimensions differ")
    for row in object_rows:
        previous = expected_objects[row["family"], row["source_doi"]]
        require(row["source_role"] == previous["source_role"] and row["source_step"] == previous["source_step"]
                and row["matched_endpoint_rows"] == int(previous["matched_endpoint_rows"]), "Raw source-object reconstruction differs")
    for row in endpoint_rows:
        previous = expected_rows[row["family"], row["epmc_record"], row["epmc_id"]]
        require(row["source_doi"] == previous["source_doi"] and row["source_role"] == previous["source_role"], "Raw row match differs")
        for precision in ("seconds", "day"):
            require(row[precision + "_candidate_n"] == int(previous[precision + "_candidate_n"])
                    and loads(row[precision + "_candidates"]) == loads(previous[precision + "_source_DOIs"]), "Precision candidate set differs")
    for row in family_rows:
        previous = prior_families[row["family"]]
        require(row["all_source_roles_complete"] == int(previous["all_roles_complete"]), "Joint family completeness differs")
        require(row["reconstructed_objects"] == sum(int(previous[role + "_retrieved"]) for role in ROLES), "Family reconstructed object count differs")
        for role in ROLES:
            for field in ("source", "retrieved", "missing"):
                require(row[role + "_" + field] == int(previous[role + "_" + field]), "Family role count differs")

    stratum_rows = []
    for stratum in sorted({x["stratum"] for x in family_rows}):
        fs = [x for x in family_rows if x["stratum"] == stratum]
        ss = [x for x in step_rows if x["stratum"] == stratum]
        detail = {"stratum": stratum, "population_families": fs[0]["stratum_N"], "sample_families": len(fs),
                  "endpoint_rows": sum(x["endpoint_rows"] for x in fs), "matched_rows": sum(x["matched_rows"] for x in fs),
                  "source_objects": sum(x["source_objects"] for x in fs), "reconstructed_objects": sum(x["reconstructed_objects"] for x in fs),
                  "complete_families": sum(x["all_source_roles_complete"] for x in fs),
                  "source_steps": len(ss), "complete_steps": sum(x["coverage_state"] == "complete" for x in ss),
                  "absent_steps": sum(x["coverage_state"] == "none" for x in ss)}
        for role in ROLES:
            detail[role + "_source"] = sum(x[role + "_source"] for x in fs)
            detail[role + "_reconstructed"] = sum(x[role + "_retrieved"] for x in fs)
        stratum_rows.append(detail)
    multiplicity = []
    for role in (*ROLES, "all_roles"):
        selected = [x for x in object_rows if role == "all_roles" or x["source_role"] == role]
        for count, objects in sorted(Counter(x["matched_endpoint_rows"] for x in selected).items()):
            multiplicity.append({"role": role, "endpoint_rows_per_source_object": count, "source_objects": objects,
                                 "endpoint_rows": count * objects})
    phase_rows = []
    for stratum in ("all", *sorted({x["stratum"] for x in object_rows})):
        for phase in ("first", "later"):
            for role in (*ROLES, "all_roles"):
                selected = [x for x in object_rows if (stratum == "all" or x["stratum"] == stratum)
                            and (phase == "first") == (x["step_rank"] == 1)
                            and (role == "all_roles" or x["source_role"] == role)]
                phase_rows.append({"stratum": stratum, "evaluation_step_position": phase, "role": role,
                                   "source_objects": len(selected), "endpoint_reconstructed": sum(x["endpoint_unique_match"] for x in selected),
                                   "EEB_counterpart_present": sum(x["EEB_counterparts"] > 0 for x in selected),
                                   "families_with_source": len({x["family"] for x in selected})})
    stage_rows = []
    for stratum in ("all", *sorted({x["stratum"] for x in step_rows})):
        combinations = {(x["coverage_state"], x["EEB_coverage_state"]) for x in step_rows}
        combinations |= {("complete", "complete"), ("none", "complete"), ("none", "none"), ("partial", "partial")}
        for endpoint_state, eeb_state in sorted(combinations):
            selected = [x for x in step_rows if (stratum == "all" or x["stratum"] == stratum)
                        and x["coverage_state"] == endpoint_state and x["EEB_coverage_state"] == eeb_state]
            ages = [x["youngest_object_age_days"] for x in selected]
            stage_rows.append({"stratum": stratum, "endpoint_state": endpoint_state, "EEB_key_correspondence_state": eeb_state,
                               "steps": len(selected), "families": len({x["family"] for x in selected}),
                               "source_objects": sum(x["source_objects"] for x in selected),
                               "source_reports": sum(x["source_reports"] for x in selected),
                               "source_summaries": sum(x["source_summaries"] for x in selected),
                               "source_replies": sum(x["source_replies"] for x in selected),
                               "youngest_object_age_min_days": min(ages) if ages else "",
                               "youngest_object_age_median_days": statistics.median(ages) if ages else "",
                               "youngest_object_age_max_days": max(ages) if ages else ""})
    precision_rows = []
    for dimension, levels in (("source_role", ROLES), ("stratum", sorted({x["stratum"] for x in endpoint_rows})), ("all", ["all"])):
        for level in levels:
            rows = [x for x in endpoint_rows if x["source_doi"] and (dimension == "all" or x[dimension] == level)]
            unique_source = {x["source_doi"] for x in rows}
            for precision in ("original", "seconds", "day"):
                retained = rows if precision == "original" else [x for x in rows if x[precision + "_same_unique"]]
                precision_rows.append({"dimension": dimension, "group": level, "precision": precision,
                                       "originally_matched_rows": len(rows), "same_unique_rows": len(retained),
                                       "ambiguous_rows": len(rows) - len(retained), "originally_reconstructed_objects": len(unique_source),
                                       "objects_with_any_retained_row": len({x["source_doi"] for x in retained})})
    burden_rows = []
    for dimension in ("all", "stratum"):
        for stratum in (["all"] if dimension == "all" else sorted({x["stratum"] for x in family_rows})):
            fs = [x for x in family_rows if stratum == "all" or x["stratum"] == stratum]
            for count in sorted({x["review-article_source"] for x in fs}):
                group = [x for x in fs if x["review-article_source"] == count]
                burden_rows.append({"grouping": dimension, "stratum": stratum, "source_reports_per_family": count,
                                    "families": len(group), "complete_report_sets": sum(x["review-article_set_covered"] for x in group),
                                    "source_reports": count * len(group), "reconstructed_reports": sum(x["review-article_retrieved"] for x in group),
                                    "source_steps": sum(x["evaluation_steps"] for x in group), "absent_steps": sum(x["absent_steps"] for x in group)})
    route_rows = []
    for native, bridge in ((0, 0), (0, 1), (1, 0), (1, 1)):
        members = [x for x in population if int(x["native_review"]) == native and int(x["bridge_review"]) == bridge]
        route_rows.append({"native_review": native, "declared_bridge_review": bridge, "families": len(members),
                           "population_denominator": len(population)})
    presence_rows = []
    for role in ROLES:
        absent = [x for x in family_rows if x[role + "_source"] and not x[role + "_retrieved"]]
        potentially_recoverable = [x for x in absent if x["unresolved_rows"]]
        retrieved_presence = sum(x[role + "_retrieved"] > 0 for x in family_rows)
        presence_rows.append({"role": role, "sample_family_denominator": len(family_rows),
                              "source_present_families": sum(x[role + "_source_present"] for x in family_rows),
                              "reconstructed_present_families": retrieved_presence,
                              "source_present_reconstructed_absent_families": len(absent),
                              "source_present_reconstructed_absent_ids": compact([x["family"] for x in absent]),
                              "affected_families_with_unresolved_rows": len(potentially_recoverable),
                              "maximum_reconstructed_presence_with_favorable_unresolved_rows": retrieved_presence + len(potentially_recoverable),
                              "complete_source_positive_family_sets": sum(x[role + "_source_present"] and x[role + "_set_covered"] for x in family_rows),
                              "empty_source_family_sets": sum(not x[role + "_source_present"] for x in family_rows)})
    missing = [x for x in step_rows if x["coverage_state"] == "none"]
    source_keys = Counter((x["family"], binding[0], x["source_time_utc"]) for x in object_rows for binding in loads(x["input_bindings"]))
    summary = {
        "scope": "Descriptive results for the 60 endpoint-positive families in a fixed 120-family draw, with 20 per each of six unequal strata; pooled proportions are not population estimates.",
        "raw_captures_verified": len(captured_reads), "sample_draw_reproduced": True,
        "prior_source_object_rows_reproduced": len(object_rows), "prior_endpoint_matches_and_precision_candidate_sets_reproduced": len(endpoint_rows),
        "families": len(family_rows), "source_objects": len(object_rows), "matched_rows": sum(bool(x["source_doi"]) for x in endpoint_rows),
        "matched_objects": sum(x["endpoint_unique_match"] for x in object_rows), "unmatched_objects": sum(not x["endpoint_unique_match"] for x in object_rows),
        "evaluation_steps": len(step_rows), "step_coverage": dict(Counter(x["coverage_state"] for x in step_rows)),
        "step_EEB_coverage": dict(Counter((x["coverage_state"], x["EEB_coverage_state"]) for x in step_rows)),
        "family_sequences": dict(Counter(x["coverage_sequence"] for x in family_rows)),
        "all_families_complete_initial_prefix": all(x["complete_initial_prefix"] for x in family_rows),
        "all_declared_chains_agree_with_strict_object_time_order": all(x["strict_time_order_agrees_with_declared_chain"] for x in family_rows),
        "all_missing_objects_input_dois_were_queried": all(x["all_source_input_dois_queried"] for x in object_rows if not x["endpoint_unique_match"]),
        "EEB_query_identifiers_verified_against_captured_URL_and_receipt": True,
        "source_input_time_keys_with_multiple_objects": sum(n > 1 for n in source_keys.values()),
        "source_objects_with_future_publication_date": sum(x["publication_age_days_at_source_capture"] < 0 for x in object_rows),
        "missing_steps_objects": sum(x["source_objects"] for x in missing),
        "missing_steps_role_counts": {role: sum(x["source_role"] == role and not x["endpoint_unique_match"] for x in object_rows) for role in ROLES},
        "missing_steps_EEB_counterpart_objects": sum(x["EEB_counterparts"] > 0 for x in object_rows if not x["endpoint_unique_match"]),
        "missing_steps_EEB_text_counterpart_objects": sum(x["EEB_text_counterparts"] > 0 for x in object_rows if not x["endpoint_unique_match"]),
        "missing_step_youngest_object_age_days": {"minimum": min(x["youngest_object_age_days"] for x in missing), "median": statistics.median(x["youngest_object_age_days"] for x in missing), "maximum": max(x["youngest_object_age_days"] for x in missing)},
        "family_joint_coverage": [{"report_complete": a, "summary_complete": b, "reply_set_covered_including_empty": c, "families": n}
                                  for (a,b,c),n in sorted(Counter(tuple(x[r + "_set_covered"] for r in ROLES) for x in family_rows).items())],
        "families_without_source_replies": [x["family"] for x in family_rows if not x["reply_source"]],
        "source_reply_positive_families": sum(x["reply_source_present"] for x in family_rows),
        "complete_reply_positive_families": sum(x["reply_source_present"] and x["reply_set_covered"] for x in family_rows),
        "conditional_unrepresented_lower_bound": sum(max(0, x["source_objects"] - x["reconstructed_objects"] - x["unresolved_rows"]) for x in family_rows),
        "first_later_accounting": [x for x in phase_rows if x["stratum"] == "all"],
        "stage_partition": [x for x in stage_rows if x["stratum"] == "all"],
        "missing_steps_per_family": dict(sorted(Counter(x["absent_steps"] for x in family_rows).items())),
        "native_bridge_union": {"frame": len(population), "native": sum(int(x["native_review"]) for x in population),
                                "bridge": sum(int(x["bridge_review"]) for x in population),
                                "union": sum(int(x["enriched_review"]) for x in population),
                                "native_only_families": [x["family"] for x in population if x["native_review"] == "1" and x["bridge_review"] == "0"]},
        "document_presence_task": presence_rows,
        "interpretation": "Publisher evaluation-step identity is the native DocMap step, ordered by its declared next-step chain and checked against object publication times. It is not an inferred independent review round. EEB presence uses an input-DOI and exact-time correspondence, not full-content equivalence. Absence is failure of this defined correspondence in captured query responses, not proof of an ingestion cause or source-wide unavailability."
    }
    summary["step_EEB_coverage"] = [{"endpoint_step_state": a, "EEB_step_state": b, "steps": n}
                                       for (a,b),n in summary["step_EEB_coverage"].items()]
    for name, rows in (("source_object_structure.csv", object_rows), ("evaluation_step_coverage.csv", step_rows),
                       ("family_coverage_structure.csv", family_rows), ("endpoint_precision_reconstruction.csv", endpoint_rows),
                       ("stratum_coverage_structure.csv", stratum_rows), ("source_object_multiplicity.csv", multiplicity),
                       ("precision_by_role_and_stratum.csv", precision_rows), ("report_burden_descriptive.csv", burden_rows),
                       ("native_bridge_overlap.csv", route_rows), ("first_later_role_accounting.csv", phase_rows),
                       ("stage_partition.csv", stage_rows), ("EEB_provider_type_accounting.csv", eeb_accounting),
                       ("document_presence_task.csv", presence_rows)):
        write_csv(output / name, rows)
    (output / "new_analysis_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    unchanged = all((repository / relative).stat().st_size == item["bytes"] and hashlib.sha256((repository / relative).read_bytes()).hexdigest() == item["sha256"] for relative, item in hashes.items())
    require(unchanged, "An input changed during analysis")
    receipt = {"status": "PASS", "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "input_files": hashes, "all_inputs_unchanged": unchanged, "network_calls": 0,
               "scientific_boundary_controls": controls,
               "outputs": {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in output.iterdir() if p.is_file()}}
    (output / "reproduction_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    main(arguments.repository, arguments.output)
