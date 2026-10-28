"""Independently inspect the targeted same-version representation case 87297."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def text(element):
    return "" if element is None else " ".join("".join(element.itertext()).split())


def parse_source(content):
    root = ET.fromstring(content)
    versions = [item.text.lower() for item in root.findall("./front/article-meta/article-id") if item.get("pub-id-type") == "doi" and item.get("specific-use") == "version"]
    assert len(versions) == 1
    outputs = []
    for sub in root.findall("./sub-article"):
        title = text(sub.find("./front-stub/title-group/article-title"))
        identifier = text(sub.find("./front-stub/article-id[@pub-id-type='doi']")).lower()
        labels = sorted({text(value).casefold() for value in sub.findall("./front-stub/kwd-group[@kwd-group-type='evidence-strength']/kwd")})
        body = text(sub.find("./body"))
        if title.casefold() == "elife assessment":
            role = "consensus"
        elif re.match(r"Reviewer\s*[#\d]", title, re.I):
            role = "reviewer_report"
        elif title.casefold().startswith("author response"):
            role = "author_reply"
        else:
            role = "unresolved"
        outputs.append({"title": title, "doi": identifier, "xml_article_type": sub.get("article-type"), "role_from_title": role, "evidence_strength": labels, "normalized_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "body": body})
    return versions[0], outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = {row["filename"]: row for row in load(args.source / "Results/source_states.json") if row["family"] == "87297"}
    plan = load(args.case / "case_acquisition_plan.json")
    receipts = {row["key"]: row for row in load(args.case / "case_acquisition_receipts.json")}
    evidence = {row["file"]: row for row in load(args.case / "87297_subarticle_evidence.json")}
    results = []
    successful = []
    failed = []
    for request in plan:
        receipt = receipts[request["key"]]
        assert receipt["url"] == request["url"]
        assert receipt == load(args.case / "case_captures" / (request["key"] + ".receipt.json"))
        if receipt["status"] != 200:
            failed.append({"key": request["key"], "url": request["url"], "status": receipt["status"]})
            continue
        content = (args.case / "case_captures" / request["key"]).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        assert digest == receipt["sha256"] and len(content) == receipt["bytes"]
        successful.append({"key": request["key"], "sha256": digest, "bytes": len(content)})
        if not request["key"].endswith(".xml"):
            continue
        assert digest == request["expected_sha256"] == source[request["key"]]["sha256"]
        version, outputs = parse_source(content)
        assert version == source[request["key"]]["version_doi"].lower()
        consensus = [output for output in outputs if output["role_from_title"] == "consensus"]
        assert len(consensus) == 1
        assert consensus[0]["doi"] == source[request["key"]]["assessment_doi"].lower()
        assert consensus[0]["evidence_strength"] == sorted(source[request["key"]]["labels"])
        expected = evidence[request["key"]]["subarticles"]
        assert [(output["title"], output["doi"], output["evidence_strength"]) for output in outputs] == [(output["title"], output["doi"].lower(), sorted(value.casefold() for value in output["evidence_strength"])) for output in expected]
        results.append({"file": request["key"], "version_doi": version, "source_sha256": digest, "objects": outputs})
    rp = next(row for row in results if row["file"] == "elife-preprint-87297-v4.xml")
    vor = next(row for row in results if row["file"] == "elife-87297-v1.xml")
    assert rp["version_doi"] == vor["version_doi"] == "10.7554/elife.87297.4"
    rp_consensus = next(row for row in rp["objects"] if row["role_from_title"] == "consensus")
    vor_consensus = next(row for row in vor["objects"] if row["role_from_title"] == "consensus")
    assert rp_consensus["doi"] == "10.7554/elife.87297.4.sa1"
    assert vor_consensus["doi"] == "10.7554/elife.87297.4.sa0"
    assert rp_consensus["body"] == vor_consensus["body"]
    assert rp_consensus["evidence_strength"] == vor_consensus["evidence_strength"] == ["solid"]
    rp_report = next(row for row in rp["objects"] if row["role_from_title"] == "reviewer_report")
    vor_report = next(row for row in vor["objects"] if row["role_from_title"] == "reviewer_report")
    assert rp_report["doi"] == vor_consensus["doi"] and vor_report["doi"] == rp_consensus["doi"]
    versions = load(args.case / "case_captures/87297_api_versions.json")["versions"]
    current = max((version for version in versions if version.get("status") == "vor"), key=lambda version: (int(version["version"]), version.get("versionDate") or ""))
    assert current["elifeAssessment"]["doi"].lower() == vor_consensus["doi"]
    # Source prose is retained in the original XML; the ledger needs hashes and
    # roles, not duplicated full report text.
    for representation in results:
        for output in representation["objects"]:
            del output["body"]
    report = {"xml_files_verified_against_original_hashes": len(results), "successful_captures": successful,
              "failed_requests_preserved": failed, "same_scientific_version_doi": rp["version_doi"],
              "RP_consensus_doi": rp_consensus["doi"], "VOR_consensus_doi": vor_consensus["doi"],
              "normalized_consensus_prose_equal": True, "evidence_strength_both": ["solid"],
              "consensus_and_reviewer_DOI_roles_swapped_between_representations": True,
              "publisher_API_current_matches_VOR": True, "representations": results,
              "interpretation": "The same scientific-version DOI has a representation-dependent consensus identifier. Registry alignment with the RP representation is not by itself a wrong-category result. This targeted case does not establish a population rate of full-text identifier conflicts."}
    (args.output / "source_case_87297_validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("representations", "successful_captures")}, indent=2))


if __name__ == "__main__":
    main()
