"""Independent verification of the V4.2.1 task diagnostics from frozen captures.

This implementation imports no PI or V4.2 analytical module. Predictions and
source-set counts are computed before the PI result files are opened for
comparison. The PI program is retained separately and unchanged.

Only the standard library is required. No network calls are made. An endpoint
timestamp without an offset is treated as UTC for the recorded comparisons,
consistent with the inherited matcher. Fractional seconds are preserved exactly
instead of silently reducing arbitrary precision to Python microseconds.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import random
import re

ROLES = ("review-article", "evaluation-summary", "reply")
SERVICE_TYPES = frozenset(("review", "reviews-summary", "referee-report", "review-article"))
TIME_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_json_object)


def load_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def doi(value):
    result = str(value or "").strip().lower().rstrip("/")
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if result.startswith(prefix):
            return result[len(prefix):]
    return result


@dataclass(frozen=True)
class Instant:
    utc_second: str
    fraction: str
    offset_was_absent: bool = False

    def key(self, precision="original"):
        if precision == "original":
            return self.utc_second + ("." + self.fraction if self.fraction else "") + "Z"
        if precision == "seconds":
            return self.utc_second
        if precision == "day":
            return self.utc_second[:10]
        raise ValueError(f"Unknown precision: {precision}")


def instant(value):
    match = TIME_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    date, clock, fraction, offset = match.groups()
    try:
        parsed = datetime.fromisoformat(date + "T" + clock + ("+00:00" if offset in (None, "Z") else offset))
    except ValueError:
        return None
    return Instant(parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                   (fraction or "").rstrip("0"), offset is None)


@dataclass(frozen=True)
class SourceObject:
    family: str
    identifier: str
    role: str
    when: Instant | None
    input_bindings: tuple
    step: str

    @property
    def input_dois(self):
        return frozenset(item[0] for item in self.input_bindings)


def source_objects(family, document):
    require(str(document.get("publisher", {}).get("name", "")).casefold() == "elife",
            f"Unexpected publisher in source family {family}")
    result = {}
    for step_id, step in document.get("steps", {}).items():
        bindings = tuple(sorted((doi(item.get("doi")), str(item.get("versionIdentifier", "")),
                                 str(item.get("url", "")))
                                for item in step.get("inputs", [])
                                if doi(item.get("doi")) and not doi(item.get("doi")).startswith("10.7554/elife.")))
        for action in step.get("actions", []):
            for output in action.get("outputs", []):
                if output.get("type") not in ROLES or not doi(output.get("doi")):
                    continue
                item = SourceObject(family, doi(output["doi"]), output["type"],
                                    instant(output.get("published")), bindings, step_id)
                previous = result.get(item.identifier)
                if previous is not None:
                    require((previous.role, previous.when, previous.input_bindings) ==
                            (item.role, item.when, item.input_bindings),
                            f"Conflicting source DOI representation: {family} {item.identifier}")
                else:
                    result[item.identifier] = item
    return list(result.values())


def opening_line(text):
    # Deliberately limited to the PI's stated 180-character opening window.
    start = text.lstrip("\ufeff \t\r\n")[:180]
    first = start.splitlines()[0] if start else ""
    first = re.sub(r"^\s*#+\s*", "", first).strip().strip("*_ ")
    return first


def heading_prediction(text):
    first = opening_line(text).casefold()
    patterns = ((r"author(?:s[’']?|[’']s)?\s+response\b", "reply"),
                (r"elife\s+assessment\b", "evaluation-summary"),
                (r"reviewer\s*#?\s*\d+\b", "review-article"))
    for pattern, label in patterns:
        if re.match(pattern, first):
            return label
    return ""


def posthoc_heading_prediction(text):
    """One transparent sensitivity extension identified after inspecting abstentions."""
    prediction = heading_prediction(text)
    if not prediction and re.match(r"joint\s+public\s+review\b", opening_line(text), re.I):
        return "review-article"
    return prediction


def unanimous_heading(texts, predictor=heading_prediction):
    predictions = [predictor(value) for value in texts]
    if not predictions or not all(predictions) or len(set(predictions)) != 1:
        return ""
    return predictions[0]


def intermediate_outputs(documents):
    outputs = []
    for wrapper in documents:
        document = wrapper.get("docmap", wrapper)
        if str(document.get("publisher", {}).get("name", "")).casefold() != "elife":
            continue
        for step_id, step in document.get("steps", {}).items():
            actual_input = frozenset(doi(item.get("doi")) for item in step.get("inputs", []) if item.get("doi"))
            for action in step.get("actions", []):
                for output in action.get("outputs", []):
                    if output.get("type") not in SERVICE_TYPES:
                        continue
                    text = "\n".join(item["text"] for item in output.get("content", []) if item.get("text"))
                    outputs.append({"when": instant(output.get("published")), "input_dois": actual_input,
                                    "text": text, "step": step_id, "identifier": doi(output.get("doi"))})
    return outputs


def candidates(objects, row_doi, row_time, precision="original"):
    if not row_doi or row_time is None:
        return []
    return [item for item in objects if item.when is not None
            and row_doi in item.input_dois and item.when.key(precision) == row_time.key(precision)]


def favorable_bound(deficits, unresolved):
    """Conditional on at most one additional source object per unresolved row."""
    return sum(max(0, amount - unresolved.get(family, 0)) for family, amount in deficits.items())


def verify_capture(path, manifest_entry):
    content = Path(path).read_bytes()
    require(manifest_entry["status"] == 200, f"Non-successful frozen response: {path}")
    require(len(content) == manifest_entry["bytes"], f"Capture byte length changed: {path}")
    require(hashlib.sha256(content).hexdigest() == manifest_entry["sha256"], f"Capture hash changed: {path}")
    return json.loads(content, object_pairs_hook=unique_json_object)


def write_csv(path, rows):
    require(bool(rows), f"Unexpected empty result: {path}")
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def boundary_controls():
    checks = {}
    source = SourceObject("a", "10.7554/elife.a.1.sa0", "review-article",
                          instant("2024-01-01T12:00:00.1234567Z"), (("10.test/input", "1", ""),), "step")
    other = replace(source, identifier="10.7554/elife.a.1.sa1", when=instant("2024-01-01T12:00:00.1234568Z"))
    checks["arbitrary_fraction_precision_not_silently_collapsed"] = len(candidates([source, other], "10.test/input", source.when)) == 1
    checks["second_truncation_exposes_ambiguity"] = len(candidates([source, other], "10.test/input", source.when, "seconds")) == 2
    checks["wrong_actual_input_doi_rejects"] = not candidates([source], "10.test/other", source.when)
    checks["missing_actual_step_input_rejects"] = not candidates([replace(source, input_bindings=())], "10.test/input", source.when)
    checks["date_only_does_not_become_exact_time"] = instant("2024-01-01") is None
    checks["equal_instant_different_offsets_agree"] = instant("2024-01-01T13:00:00.1234567+01:00").key() == source.when.key()
    checks["naive_utc_assumption_explicit"] = instant("2024-01-01 12:00:00.1234567").offset_was_absent
    checks["truncation_is_not_nearest_second_rounding"] = instant("2024-01-01T12:00:00.999Z").key("seconds") == "2024-01-01T12:00:00"
    checks["same_full_timestamp_distinct_objects_remain_ambiguous"] = len(candidates([source, replace(other, when=source.when)], "10.test/input", source.when)) == 2
    checks["later_quoted_heading_is_not_used"] = heading_prediction("A discussion\n# Reviewer #1") == ""
    checks["markdown_opening_heading_recognized"] = heading_prediction("\ufeff\n  ## **Reviewer #2**\ntext") == "review-article"
    checks["author_response_opening_recognized"] = all(heading_prediction(value) == "reply" for value in ("Author response", "Author’s response", "Authors' response", "Authors response"))
    checks["unknown_intermediate_variant_forces_abstention"] = unanimous_heading(["Reviewer #1", "An unrecognized heading"]) == ""
    checks["conflicting_intermediate_roles_force_abstention"] = unanimous_heading(["Reviewer #1", "Author response"]) == ""
    checks["no_intermediate_candidate_forces_abstention"] = unanimous_heading([]) == ""
    checks["joint_public_review_is_only_a_posthoc_extension"] = heading_prediction("Joint Public Review:") == "" and posthoc_heading_prediction("Joint Public Review:") == "review-article"
    checks["extension_still_ignores_later_quoted_headings"] = posthoc_heading_prediction("A discussion\nJoint Public Review:") == ""
    checks["extension_still_abstains_on_conflicting_variants"] = unanimous_heading(["Joint Public Review:", "Author response"], posthoc_heading_prediction) == ""
    checks["family_bound_cannot_borrow_rows_from_other_families"] = favorable_bound({"a": 0, "b": 2}, {"a": 2}) == 2
    checks["one_unresolved_row_cannot_cover_two_joint_role_objects"] = favorable_bound({"a": 2}, {"a": 1}) == 1
    checks["per_role_bounds_not_added_as_joint_minimum"] = favorable_bound({"a": 1}, {"a": 1}) * 2 == 0 and favorable_bound({"a": 2}, {"a": 1}) == 1
    checks["family_bound_never_negative"] = favorable_bound({"a": 1}, {"a": 3}) == 0
    require(all(checks.values()), "A task-diagnostic boundary control failed")
    return checks


def compare_records(expected, supplied, name):
    require(len(expected) == len(supplied), f"{name}: row count differs")
    for index, (computed, frozen) in enumerate(zip(expected, supplied)):
        require(set(computed) == set(frozen), f"{name}: columns differ at {index}")
        for key, value in computed.items():
            if isinstance(value, float):
                require(abs(value - float(frozen[key])) < 1e-12, f"{name}: {index} {key}")
            else:
                require(str(value) == frozen[key], f"{name}: {index} {key}: {value!r} != {frozen[key]!r}")


def run(repo, pi_results, output):
    repo, pi_results, output = Path(repo), Path(pi_results), Path(output)
    require(not output.exists(), "Independent output directory must not already exist")
    plan = load_json(repo / "Evidence/Provenance/raw_validation_plan.json")
    frame = load_csv(repo / "Results/Provenance/endpoint_family_ledger.csv")
    eligible = [row for row in frame if row["has_vor"] == "1" and row["exact_MED"] == "1"]
    randomizer = random.Random(plan["seed"])
    drawn = []
    for stratum in sorted(set(row["residual_class"] for row in eligible)):
        members = sorted((row for row in eligible if row["residual_class"] == stratum), key=lambda row: row["family"])
        drawn.extend((row["family"], stratum) for row in randomizer.sample(members, min(20, len(members))))
    require(drawn == [(row["family"], row["stratum"]) for row in plan["sample"]], "Frozen sample draw differs")

    manifests = load_json(repo / "Evidence/Provenance/validation/capture_manifest.json")
    capture_index = {}
    for entry in manifests:
        key = (entry["provider"], entry["key"])
        require(key not in capture_index, f"Duplicate capture manifest key: {key}")
        capture_index[key] = entry
    read_captures = set()
    def capture(provider, key):
        entry = capture_index[provider, key]
        path = repo / "Evidence/Provenance/validation/raw" / provider / (key + ".json")
        read_captures.add((provider, key))
        return verify_capture(path, entry)

    family_rows, diagnostic_rows, pi_row_projection, object_rows = [], [], [], []
    sensitivity_changes = []
    all_source, all_endpoint, headings = [], [], Counter()
    unresolved, source_matched_rows = Counter(), defaultdict(list)
    ablation_counts = {level: Counter() for level in ("original", "seconds", "day")}
    decimal_lengths = defaultdict(Counter)
    naive_timestamps = Counter()

    for selection in plan["sample"]:
        family = selection["family"]
        source = source_objects(family, capture("elife", family))
        intermediates = []
        for ordinal in range(len(selection["original_preprint_dois"])):
            intermediates.extend(intermediate_outputs(capture("eeb", family + "_" + str(ordinal))))
        endpoint_rows = []
        for record_id in selection["endpoints"]:
            document = capture("epmc", record_id.replace("/", "_"))
            endpoint_rows.extend((record_id, row) for row in document.get("evaluationList", {}).get("evaluation", [])
                                 if str(row.get("platform", "")).casefold() == "elife" and row.get("type") in SERVICE_TYPES)
        all_source.extend(source)
        all_endpoint.extend(endpoint_rows)
        reconstructed = {role: set() for role in ROLES}
        source_sets = {role: {item.identifier for item in source if item.role == role} for role in ROLES}
        for item in source:
            if item.when:
                decimal_lengths["publisher"][len(item.when.fraction)] += 1
                naive_timestamps["publisher"] += int(item.when.offset_was_absent)
        for record_id, row in endpoint_rows:
            row_doi, row_time = doi(row.get("doi")), instant(row.get("evaluationDate"))
            if row_time:
                decimal_lengths["endpoint"][len(row_time.fraction)] += 1
                naive_timestamps["endpoint"] += int(row_time.offset_was_absent)
            exact = candidates(source, row_doi, row_time)
            selected = exact[0] if len(exact) == 1 else None
            if selected is None:
                unresolved[family] += 1
            else:
                reconstructed[selected.role].add(selected.identifier)
                source_matched_rows[family, selected.identifier].append((record_id, str(row.get("id"))))
            compatible = [item for item in intermediates if row_time and item["when"]
                          and row_doi in item["input_dois"] and item["when"].key() == row_time.key()]
            prediction = unanimous_heading([item["text"] for item in compatible])
            extended_prediction = unanimous_heading([item["text"] for item in compatible], posthoc_heading_prediction)
            heading_correct = int(bool(selected and prediction and prediction == selected.role))
            heading_wrong = int(bool(selected and prediction and prediction != selected.role))
            if selected:
                headings[selected.role, prediction or "abstain"] += 1
            projection = {"family": family, "stratum": selection["stratum"], "epmc_record": record_id,
                          "epmc_id": row.get("id"), "status": "matched" if selected else "unresolved",
                          "candidate_n": len(exact), "source_doi": selected.identifier if selected else "",
                          "source_role": selected.role if selected else "", "eeb_candidate_n": len(compatible),
                          "heading_only_role": prediction, "heading_correct": heading_correct,
                          "heading_wrong": heading_wrong, "EPMC_nonempty_URL": int(bool(row.get("url")))}
            pi_row_projection.append(projection)
            diagnostic = dict(projection, input_doi=row_doi, raw_endpoint_time=row.get("evaluationDate", ""),
                              normalized_UTC_time=row_time.key() if row_time else "",
                              compatible_opening_headings=compact([opening_line(item["text"]) for item in compatible]),
                              posthoc_heading_role=extended_prediction,
                              posthoc_heading_correct=int(bool(selected and extended_prediction and extended_prediction == selected.role)),
                              posthoc_heading_wrong=int(bool(selected and extended_prediction and extended_prediction != selected.role)))
            if prediction != extended_prediction:
                sensitivity_changes.append({"family": family, "epmc_record": record_id, "epmc_id": row.get("id"),
                                            "source_doi": selected.identifier if selected else "", "source_role": selected.role if selected else "",
                                            "baseline_role": prediction, "posthoc_role": extended_prediction,
                                            "opening_headings": diagnostic["compatible_opening_headings"],
                                            "in_fixed_matched_cohort": int(selected is not None),
                                            "source_agreement": "not_scored_no_exact_source_match" if selected is None else "agree" if diagnostic["posthoc_heading_correct"] else "contradict"})
            for level in ablation_counts:
                matches = candidates(source, row_doi, row_time, level)
                identities = sorted(item.identifier for item in matches)
                same = bool(selected and len(matches) == 1 and matches[0].identifier == selected.identifier)
                counts = ablation_counts[level]
                counts["rows"] += 1
                counts["unique"] += int(len(matches) == 1)
                counts["ambiguous"] += int(len(matches) > 1)
                counts["none"] += int(len(matches) == 0)
                counts["retained_unique"] += int(same)
                counts["ambiguous_fixed_cohort"] += int(bool(selected) and len(matches) > 1)
                counts["missing_fixed_cohort"] += int(bool(selected) and len(matches) == 0)
                diagnostic[level + "_candidate_n"] = len(matches)
                diagnostic[level + "_source_DOIs"] = compact(identities)
                diagnostic[level + "_retains_original_unique_match"] = int(same)
            diagnostic_rows.append(diagnostic)
        family_row = {"family": family, "stratum": selection["stratum"], "N_h": selection["stratum_N"],
                      "n_h": selection["stratum_n"], "review_rows": len(endpoint_rows)}
        for role in ROLES:
            family_row[role + "_source"] = len(source_sets[role])
            family_row[role + "_retrieved"] = len(reconstructed[role])
            family_row[role + "_missing"] = len(source_sets[role] - reconstructed[role])
            family_row[role + "_complete"] = int(bool(source_sets[role]) and source_sets[role] <= reconstructed[role])
        family_row["all_roles_complete"] = int(all(source_sets[role] <= reconstructed[role] for role in ROLES))
        family_rows.append(family_row)
        if endpoint_rows:
            for item in source:
                bindings = [{"doi": binding[0], "version": binding[1], "url": binding[2]} for binding in item.input_bindings]
                matched_rows = source_matched_rows[family, item.identifier]
                object_rows.append({"family": family, "stratum": selection["stratum"], "source_doi": item.identifier,
                                    "source_role": item.role, "source_step": item.step,
                                    "source_time_UTC": item.when.key() if item.when else "",
                                    "actual_evaluation_input_bindings": compact(bindings),
                                    "uniquely_reconstructed": int(bool(matched_rows)),
                                    "matched_endpoint_rows": len(matched_rows), "matched_row_keys": compact(matched_rows)})

    require(len(read_captures) == 340 == len(capture_index), "Sample raw capture inventory differs")
    require(len({(row["family"], row["epmc_record"], row["epmc_id"]) for row in diagnostic_rows}) == len(diagnostic_rows),
            "Duplicate endpoint-row key")
    positive = [row for row in family_rows if row["review_rows"]]
    role_summary, bounds, family_bounds = [], [], []
    unresolved_count = sum(unresolved.values())
    for role in ROLES:
        denominator = sum(row[role + "_source"] for row in positive)
        matched_count = sum(row[role + "_retrieved"] for row in positive)
        missing = denominator - matched_count
        role_summary.append({"role": role, "source_objects": denominator, "retrieved_objects": matched_count,
                             "unmatched_objects": missing, "recovery_pct": 100 * matched_count / denominator,
                             "families_with_source": sum(row[role + "_source"] > 0 for row in positive),
                             "families_with_complete_role_set": sum(row[role + "_complete"] for row in positive),
                             "families_with_any_retrieved": sum(row[role + "_retrieved"] > 0 for row in positive)})
        bounds.append({"role": role, "source_objects": denominator, "unique_matched_objects": matched_count,
                       "not_uniquely_reconstructed": missing, "unrepresented_lower_bound": max(0, missing - unresolved_count),
                       "all_unresolved_allocated_favorably": unresolved_count,
                       "family_respecting_lower_bound": favorable_bound({row["family"]: row[role + "_missing"] for row in positive}, unresolved)})
    total_source = sum(row["source_objects"] for row in role_summary)
    total_matched = sum(row["retrieved_objects"] for row in role_summary)
    all_deficits = {row["family"]: sum(row[role + "_missing"] for role in ROLES) for row in positive}
    bounds.append({"role": "all_roles", "source_objects": total_source, "unique_matched_objects": total_matched,
                   "not_uniquely_reconstructed": total_source - total_matched,
                   "unrepresented_lower_bound": max(0, total_source - total_matched - unresolved_count),
                   "all_unresolved_allocated_favorably": unresolved_count,
                   "family_respecting_lower_bound": favorable_bound(all_deficits, unresolved)})
    for row in positive:
        family = row["family"]
        detail = {"family": family, "stratum": row["stratum"], "unresolved_endpoint_rows": unresolved[family]}
        for role in ROLES:
            deficit = row[role + "_missing"]
            detail[role + "_source"] = row[role + "_source"]
            detail[role + "_unique_matched"] = row[role + "_retrieved"]
            detail[role + "_not_uniquely_matched"] = deficit
            detail[role + "_conditional_lower_bound"] = max(0, deficit - unresolved[family])
        detail["all_roles_not_uniquely_matched"] = all_deficits[family]
        detail["all_roles_conditional_lower_bound"] = max(0, all_deficits[family] - unresolved[family])
        family_bounds.append(detail)
    heading_summary = [{"source_role": role, "heading_role": prediction, "rows": count}
                       for (role, prediction), count in sorted(headings.items(), key=lambda pair: (pair[0][0], "" if pair[0][1] == "abstain" else pair[0][1]))]
    ablation_summary = [{"timestamp_precision": "microseconds" if level == "original" else level,
                         "unique_matches": counts["unique"], "ambiguous": counts["ambiguous"],
                         "no_candidate": counts["none"], "same_as_fine": counts["retained_unique"],
                         "ambiguous_among_458_fine_matches": counts["ambiguous_fixed_cohort"]}
                        for level, counts in ablation_counts.items()]

    # Only after all predictions and counts exist do we open the PI output tables.
    comparisons = {}
    for name, computed in (("independent_row_check.csv", pi_row_projection),
                           ("family_role_coverage.csv", family_rows),
                           ("role_coverage_summary.csv", role_summary),
                           ("coverage_bounds.csv", bounds),
                           ("heading_baseline.csv", heading_summary),
                           ("timestamp_ablation.csv", ablation_summary)):
        compare_records(computed, load_csv(pi_results / name), name)
        comparisons[name] = {"all_rows_and_columns_agree": True, "rows": len(computed)}
    pi_summary = load_json(pi_results / "independent_results.json")
    summary_fields = {"sampling_exactly_reproduced": True, "raw_files_read": len(read_captures),
                      "sample_families": len(family_rows), "positive_sample_families": len(positive),
                      "eligible_rows": len(diagnostic_rows), "matched": sum(row["status"] == "matched" for row in diagnostic_rows),
                      "distinct_source_DOIs": len({row["source_doi"] for row in diagnostic_rows if row["source_doi"]}),
                      "unique_report_objects": len({row["source_doi"] for row in diagnostic_rows if row["source_role"] == "review-article"}),
                      "source_objects_positive": total_source,
                      "all_source_roles_complete_families": sum(row["all_roles_complete"] for row in positive),
                      "heading_baseline": {"correct_rows": sum(row["heading_correct"] for row in diagnostic_rows),
                                           "wrong_rows": sum(row["heading_wrong"] for row in diagnostic_rows),
                                           "matched_abstentions": sum(row["status"] == "matched" and not row["heading_only_role"] for row in diagnostic_rows)}}
    for key, value in summary_fields.items():
        require(pi_summary[key] == value, f"PI summary differs: {key}")
    comparisons["independent_results.json"] = {"all_new_diagnostic_fields_agree": True, "fields": len(summary_fields),
                                               "residual_census": "Replayed by the unchanged PI script and the existing full-census auditors; not independently recomputed again here."}
    require(all(counts["missing_fixed_cohort"] == 0 for counts in ablation_counts.values()), "Coarsening unexpectedly loses an original candidate")
    matched_sensitivity_changes = [row for row in sensitivity_changes if row["in_fixed_matched_cohort"]]
    sensitivity_controls = {
        "changed_rows_were_initial_lexicon_abstentions": all(not row["baseline_role"] for row in sensitivity_changes),
        "every_changed_row_has_literal_joint_heading": all(json.loads(row["opening_headings"]) == ["Joint Public Review:"] for row in sensitivity_changes),
        "all_fixed_cohort_changes_agree_with_source_role": all(row["source_agreement"] == "agree" and row["source_role"] == "review-article" for row in matched_sensitivity_changes),
        "extended_agree_equals_baseline_agree_plus_fixed_cohort_changed_rows": sum(row["posthoc_heading_correct"] for row in diagnostic_rows) == summary_fields["heading_baseline"]["correct_rows"] + len(matched_sensitivity_changes)
    }
    require(all(sensitivity_controls.values()), "A post hoc heading count control failed")

    proof = {
        "status": "PASS", "implementation": "Separate standard-library implementation; no PI or V4.2 analytical module imported.",
        "sampling_exactly_reconstructed": True, "raw_sample_captures_hash_verified": len(read_captures),
        "calculations_completed_before_PI_outputs_read": True, "comparison_to_PI": comparisons,
        "counts": summary_fields, "role_coverage": role_summary, "coverage_bounds": bounds,
        "bound_assumption": "At most one additional captured source DOI object can be assigned to each unresolved endpoint row, within its own family. This is an accounting bound for that assignment, not proof of service-wide absence or of the unit represented by an unresolved row.",
        "role_bounds_are_separate_not_jointly_attainable": True,
        "unresolved_families": [{"family": family, "unresolved_rows": unresolved[family], "source_deficit_all_roles": all_deficits.get(family, 0)} for family in sorted(unresolved) if unresolved[family]],
        "new_unique_matches_after_second_truncation_are_unvalidated": [{"family": row["family"], "epmc_record": row["epmc_record"], "epmc_id": row["epmc_id"], "candidate": json.loads(row["seconds_source_DOIs"])[0]}
            for row in diagnostic_rows if row["status"] == "unresolved" and row["seconds_candidate_n"] == 1],
        "heading_abstentions": [{"family": row["family"], "epmc_record": row["epmc_record"], "epmc_id": row["epmc_id"], "source_doi": row["source_doi"], "opening_headings": json.loads(row["compatible_opening_headings"])}
            for row in diagnostic_rows if row["status"] == "matched" and not row["heading_only_role"]],
        "posthoc_heading_lexicon_sensitivity": {
            "added_pattern": "Opening heading begins with 'Joint Public Review' (case-insensitive). All compatible variants must still agree.",
            "timing": "Added after inspecting the seven initial comparator abstentions; this is within-sample sensitivity, not external validation.",
            "fixed_matched_row_denominator": summary_fields["matched"],
            "baseline_agree": sum(row["heading_correct"] for row in diagnostic_rows),
            "baseline_abstain": summary_fields["heading_baseline"]["matched_abstentions"],
            "extended_agree": sum(row["posthoc_heading_correct"] for row in diagnostic_rows),
            "extended_contradict": sum(row["posthoc_heading_wrong"] for row in diagnostic_rows),
            "extended_abstain": sum(row["status"] == "matched" and not row["posthoc_heading_role"] for row in diagnostic_rows),
            "changed_rows_in_fixed_matched_cohort": len(matched_sensitivity_changes),
            "changed_rows_in_all_eligible_rows": len(sensitivity_changes),
            "changed_distinct_source_DOIs_in_fixed_matched_cohort": len({row["source_doi"] for row in matched_sensitivity_changes}),
            "changed_families_in_fixed_matched_cohort": len({row["family"] for row in matched_sensitivity_changes}),
            "changed_families_in_all_eligible_rows": len({row["family"] for row in sensitivity_changes}),
            "unresolved_source_rows_newly_classified": sum(row["status"] == "unresolved" and bool(row["posthoc_heading_role"]) for row in diagnostic_rows),
            "all_changes_have_literal_joint_public_review_opening": all(json.loads(row["opening_headings"]) == ["Joint Public Review:"] for row in sensitivity_changes)
        },
        "precision_operation": "Truncate UTC-normalized instants to whole seconds or UTC calendar days; do not round. Offset-free endpoint clocks are treated as UTC, as in the inherited comparison.",
        "fractional_precision_after_trailing_zero_normalization": {kind: dict(sorted(counts.items())) for kind, counts in decimal_lengths.items()},
        "offset_free_timestamp_counts": dict(naive_timestamps),
        "precision_fixed_cohort": {level: {"denominator": summary_fields["matched"], "same_unique_match": counts["retained_unique"], "ambiguous": counts["ambiguous_fixed_cohort"], "no_candidate": counts["missing_fixed_cohort"]} for level, counts in ablation_counts.items()},
        "precision_all_eligible_rows": {level: dict(counts) for level, counts in ablation_counts.items()},
        "scientific_boundary_controls": boundary_controls(),
        "posthoc_heading_count_controls": sensitivity_controls,
        "scope": "Exploratory post-V4.2 diagnostics for the fixed stratified sample. Source DOI objects need not be independent intellectual contributions. No population recall or external classifier validation is asserted."
    }
    output.mkdir(parents=True)
    write_csv(output / "source_object_coverage.csv", sorted(object_rows, key=lambda row: (row["family"], row["source_doi"])))
    write_csv(output / "row_task_diagnostics.csv", diagnostic_rows)
    write_csv(output / "family_coverage_bounds.csv", sorted(family_bounds, key=lambda row: row["family"]))
    write_csv(output / "heading_lexicon_sensitivity.csv", sensitivity_changes)
    (output / "task_diagnostics_audit.json").write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "raw_sample_captures_verified": len(read_captures),
                      "PI_tables_independently_recomputed": 6, "additional_PI_summary_checked": True,
                      "scientific_boundary_controls": len(proof["scientific_boundary_controls"]),
                      "source_objects_positive": total_source, "matched_source_objects": total_matched,
                      "conditional_all_roles_lower_bound": bounds[-1]["family_respecting_lower_bound"]}, indent=2))
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--pi-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.repo, args.pi_results, args.output)
