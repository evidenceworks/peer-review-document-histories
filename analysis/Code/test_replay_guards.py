"""Focused integrity, semantic-comparison and child-runtime tests of replay.

No analytical pipeline is rerun. All fixtures are disposable new files under
the provided work directory; published inputs and results remain read only.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

sys.dont_write_bytecode = True


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def main(repository, work):
    repository, work = Path(repository).resolve(), Path(work).resolve()
    require(work != repository and repository not in work.parents and work not in repository.parents, "Work must be separate from the repository")
    require(not work.exists(), "Work must be new")
    work.mkdir(parents=True)
    script = repository / "reproduce.py"
    before = sha(script)
    spec = importlib.util.spec_from_file_location("reviewed_replay", script)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    checks = []
    def test(name, action):
        try:
            detail = action()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as error:
            checks.append({"name": name, "passed": False, "error": str(error), "traceback": traceback.format_exc()})

    fixture = work / "fixture_repository"
    (fixture / "Inputs").mkdir(parents=True)
    (fixture / "Evidence").mkdir()
    data = fixture / "Inputs/value.json"
    data.write_text('{"n":1}\n', encoding="utf-8")
    record = {"path": "Inputs/value.json", "bytes": data.stat().st_size, "sha256": sha(data)}
    def manifest(rows):
        (fixture / "input_manifest.json").write_text(json.dumps({"files": rows}), encoding="utf-8")
    manifest([record])
    def rejected(action):
        try:
            action()
        except (ValueError, FileExistsError):
            return True
        return False
    def manifest_gates():
        require(replay.verify_inputs(fixture)["files"] == 1, "Valid fixture failed")
        outcomes = {}
        manifest([record, record])
        outcomes["duplicate_manifest_entry_rejected"] = rejected(lambda: replay.verify_inputs(fixture))
        manifest([record]); extra = fixture / "Evidence/unlisted.json"; extra.write_text("{}")
        outcomes["extra_unmanifested_input_rejected"] = rejected(lambda: replay.verify_inputs(fixture))
        extra.unlink(); data.write_text('{"n":2}\n', encoding="utf-8")
        outcomes["same_length_scientific_value_corruption_rejected"] = rejected(lambda: replay.verify_inputs(fixture))
        data.write_text('{"n":1}\n', encoding="utf-8")
        data.rename(fixture / "Inputs/renamed.json")
        outcomes["missing_manifested_input_rejected"] = rejected(lambda: replay.verify_inputs(fixture))
        (fixture / "Inputs/renamed.json").rename(data)
        require(all(outcomes.values()), str(outcomes))
        return outcomes
    test("input_membership_and_SHA_gates", manifest_gates)

    def semantics():
        left, right = work / "left.json", work / "right.json"
        left.write_text('{"a":1,"b":[1,2]}\n')
        right.write_text('{\n "b": [1, 2], "a": 1\n}\r\n')
        outcomes = {"JSON_whitespace_and_object_key_order_allowed": replay.semantic_equal(left, right)}
        right.write_text('{"a":2,"b":[1,2]}')
        outcomes["changed_JSON_number_detected"] = not replay.semantic_equal(left, right)
        right.write_text('{"a":true,"b":[1,2]}')
        outcomes["boolean_not_equated_with_integer"] = not replay.semantic_equal(left, right)
        right.write_text('{"a":1,"b":[2,1]}')
        outcomes["list_order_preserved"] = not replay.semantic_equal(left, right)
        right.write_text('{"a":1,"a":2,"b":[1,2]}')
        outcomes["duplicate_JSON_key_rejected"] = rejected(lambda: replay.semantic_equal(left, right))
        right.write_text('{"a":NaN,"b":[1,2]}')
        outcomes["nonfinite_JSON_rejected"] = rejected(lambda: replay.semantic_equal(left, right))
        left, right = work / "left.csv", work / "right.csv"
        left.write_bytes(b"name,count\na,1\n")
        right.write_bytes(b"name,count\r\na,1\r\n")
        outcomes["CSV_line_endings_allowed"] = replay.semantic_equal(left, right)
        right.write_bytes(b"name,count\r\na,2\r\n")
        outcomes["changed_CSV_cell_detected"] = not replay.semantic_equal(left, right)
        require(all(outcomes.values()), str(outcomes))
        return outcomes
    test("semantic_comparison_preserves_actual_scientific_values", semantics)

    def output_gates():
        outcomes = {
            "existing_output_rejected": rejected(lambda: replay.output_path(repository, work)),
            "repository_child_rejected": rejected(lambda: replay.output_path(repository, repository / "never_created")),
            "repository_ancestor_rejected": rejected(lambda: replay.output_path(repository, repository.parent)),
            "new_separate_output_allowed": replay.output_path(repository, work / "future") == work / "future",
        }
        require(all(outcomes.values()), str(outcomes))
        return outcomes
    test("output_location_and_no_overwrite_rules", output_gates)

    def child_guard():
        output = work / "child_output"; output.mkdir()
        guards = work / "guard_module"; guards.mkdir()
        protected = work / "protected_fixture.txt"; protected.write_text("preserved")
        (guards / "sitecustomize.py").write_text(replay.guard_source(output), encoding="utf-8")
        program = '''
import sys,pathlib,json,socket,subprocess,os
output,protected=map(pathlib.Path,sys.argv[1:3])
(output/'allowed.txt').write_text('allowed')
checks={}
actions=[('write',lambda:protected.write_text('changed')),('remove',lambda:protected.unlink()),
         ('rename',lambda:protected.rename(output/'stolen.txt')),
         ('mkdir',lambda:(protected.parent/'forbidden').mkdir()),('socket',lambda:socket.socket()),
         ('subprocess',lambda:subprocess.run([sys.executable,'-B','-c','pass'])),
         ('system',lambda:os.system('exit 0')),
         ('symlink',lambda:(output/'link').symlink_to(protected))]
for key,action in actions:
    try:action();checks[key]=False
    except PermissionError:checks[key]=True
print(json.dumps(checks))
'''
        environment = dict(os.environ, PYTHONPATH=str(guards), PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="0",
                           TEMP=str(output), TMP=str(output), TMPDIR=str(output))
        completed = subprocess.run([sys.executable, "-B", "-c", program, str(output), str(protected)], env=environment,
                                   text=True, capture_output=True, cwd=output)
        require(completed.returncode == 0, completed.stderr)
        outcomes = json.loads(completed.stdout)
        require(all(outcomes.values()) and protected.read_text() == "preserved", str(outcomes))
        require((output / "allowed.txt").read_text() == "allowed", "Permitted child write failed")
        return outcomes
    test("actual_child_sitecustomize_guard_blocks_external_effects", child_guard)

    def optimized():
        output = work / "optimized_output"
        completed = subprocess.run([sys.executable, "-O", "-B", str(script), "--root", str(repository), "--output", str(output)], text=True, capture_output=True, cwd=work)
        require(completed.returncode != 0 and not output.exists() and "Run without -O" in completed.stderr, "Optimized mode not refused")
        return {"refused_before_output_creation": True}
    test("optimized_runner_execution_refused", optimized)

    checks.append({"name": "reviewed_runner_unchanged", "passed": sha(script) == before})
    receipt = {"status": "PASS" if all(x["passed"] for x in checks) else "FAIL", "checks_run": len(checks),
               "checks_passed": sum(x["passed"] for x in checks), "checks": checks,
               "runner_sha256": before, "review_script_sha256": sha(__file__), "full_analysis_replayed": False}
    (work / "replay_guard_independent_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("status", "checks_run", "checks_passed", "runner_sha256")}, indent=2))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.repository, args.work))
