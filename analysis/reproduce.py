#!/usr/bin/env python3
"""Offline replay of retained observations and corrected source projections.

Run with an installed Python 3.9+ interpreter and a new output directory
separate from the repository. Source inputs and published result tables remain read only.
"""
import sys
sys.dont_write_bytecode = True
if sys.flags.optimize:
    raise RuntimeError('Run without -O or PYTHONOPTIMIZE; validation checks must remain enabled')
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object, parse_constant=invalid)


def equal_values(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equal_values(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(equal_values(a, b) for a, b in zip(left, right))
    return left == right


def semantic_equal(left, right):
    if left.suffix == '.json':
        return equal_values(read_json(left), read_json(right))
    if left.suffix == '.csv':
        with left.open(encoding='utf-8-sig', newline='') as a, right.open(encoding='utf-8-sig', newline='') as b:
            return list(csv.reader(a, strict=True)) == list(csv.reader(b, strict=True))
    return left.read_bytes() == right.read_bytes()


def verify_inputs(root):
    rows = read_json(root / 'input_manifest.json')['files']
    seen = set()
    for row in rows:
        name = row['path']
        path = (root / name).resolve()
        if root not in path.parents or name in seen:
            raise ValueError('Unsafe or duplicate input manifest path')
        seen.add(name)
        if not path.is_file() or path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError('Input integrity mismatch: ' + name)
    actual = {p.relative_to(root).as_posix() for branch in ('Inputs', 'Evidence') for p in (root / branch).rglob('*') if p.is_file()}
    if seen != actual:
        raise ValueError('Input manifest membership differs from retained Inputs/Evidence trees')
    return {'files': len(rows), 'bytes': sum(r['bytes'] for r in rows), 'all_hashes_verified': True}


def output_path(root, value):
    out = Path(value).resolve()
    if out == root or root in out.parents or out in root.parents:
        raise ValueError('Output must be separate from the read-only Repository')
    if out.exists():
        raise FileExistsError('Output already exists; retained runs are never overwritten')
    return out


def guard_source(output):
    return '''import os,sys
from pathlib import Path
sys.dont_write_bytecode=True
_allowed=Path(%r).resolve()
def _writeable(value):
    if isinstance(value,int):
        raise PermissionError('Unclassified file descriptor mutation')
    path=Path(os.fsdecode(value)).resolve()
    if path!=_allowed and _allowed not in path.parents:
        raise PermissionError('Replay write outside controlled output')
def _audit(event,args):
    if event.startswith('socket.'):
        raise PermissionError('Offline replay prohibits network operations')
    if event in {'subprocess.Popen','os.system','os.posix_spawn'}:
        raise PermissionError('Analysis stages cannot launch external processes')
    if event=='open':
        name,mode,flags=args
        if not isinstance(name,int) and ((isinstance(mode,str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            _writeable(name)
    elif event in {'os.remove','os.rmdir','os.mkdir','os.chmod','os.chown','os.utime','os.truncate'}:
        _writeable(args[0])
    elif event in {'os.rename','os.replace','os.link'}:
        _writeable(args[0]);_writeable(args[1])
    elif event=='os.symlink':
        raise PermissionError('Replay prohibits symbolic-link creation')
sys.addaudithook(_audit)
''' % str(output)


def run(root, destination):
    root = Path(root).resolve()
    output = output_path(root, destination)
    started = time.monotonic()
    inputs = verify_inputs(root)
    output.mkdir(parents=True)
    work, logs, runtime = output / 'Work', output / 'Logs', output / 'Runtime'
    for p in (work, logs, runtime):
        p.mkdir()
    guard = runtime / 'guard'; guard.mkdir()
    (guard / 'sitecustomize.py').write_text(guard_source(output), encoding='utf-8')
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONOPTIMIZE='0',
                       PYTHONPATH=str(guard), TEMP=str(runtime), TMP=str(runtime), TMPDIR=str(runtime))
    provenance, content, empirical = work / 'Provenance', work / 'ContentValidation', work / 'Empirical'
    shutil.copytree(root / 'Evidence/Provenance', provenance)
    shutil.copytree(root / 'Evidence/ContentValidation', content)
    empirical.mkdir()
    shutil.copyfile(root / 'Code/matched_provenance.py', provenance / 'matched_provenance.py')
    source = root / 'Inputs/records'
    results = source / 'Results'
    additions, histories, source_audit = work / 'ScientificAdditions', work / 'DocumentHistories', work / 'SourceAuditIntegration'
    commands = [
        ('source_audit_integration.py', ['--repository', root, '--output', source_audit]),
        ('endpoint_audit.py', ['--input', results, '--source-states', root / 'Evidence/SourceAudit/corrected_source_states.json', '--output', provenance]),
        ('matched_provenance.py', ['--audit', provenance, '--inputs', results]),
        ('publisher_comparator.py', ['--offline', '--audit', provenance, '--inputs', results]),
        ('exception_census.py', ['--offline', '--audit', provenance, '--inputs', results]),
        ('validate_raw_provenance.py', ['--source', source, '--provenance', provenance, '--output', empirical]),
        ('check_content.py', ['--validation', content, '--eeb', provenance / 'matched_eeb_outputs.json']),
        ('audit_content_controls.py', ['--controls', content, '--provenance', provenance, '--output', empirical]),
        ('validate_exception_census.py', ['--source', source, '--provenance', provenance, '--output', empirical]),
        ('validate_source_case.py', ['--case', root / 'Evidence/SourceCase87297', '--source', source, '--output', empirical]),
        ('ScientificAdditions/independent_tasks.py', ['--repo', root, '--out', additions]),
        ('ScientificAdditions/verify_task_diagnostics.py', ['--repo', root, '--reference-results', additions, '--output', additions / 'Independent']),
        ('analyze_document_histories.py', ['--repository', root, '--output', histories]),
        ('verify_step_structure.py', ['--repository', root, '--comparison-results', histories, '--output', work / 'StepVerification']),
    ]
    executed, comparisons = [], []
    receipt = {'status': 'INCOMPLETE', 'network': 'runtime prohibited', 'output_scope': 'All replay writes confined to the requested output',
               'input_integrity': inputs, 'full_raw_source_extraction_repeated': False,
               'original_source_audit_status': 'COMPLETED_WITH_UNRESOLVED_ITEMS', 'original_full_corpus_audit_closed': False}
    try:
        for script, arguments in commands:
            path = root / script if '/' in script else root / 'Code' / script
            command = [sys.executable, '-B', str(path), *map(str, arguments)]
            completed = subprocess.run(command, env=environment, cwd=root, text=True, encoding='utf-8',
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (logs / (path.name + '.stdout.txt')).write_text(completed.stdout, encoding='utf-8')
            (logs / (path.name + '.stderr.txt')).write_text(completed.stderr, encoding='utf-8')
            executed.append({'script': script, 'sha256': digest(path), 'exit_code': completed.returncode})
            if completed.returncode:
                raise RuntimeError(script + ' failed; inspect its captured log in Logs')
            print('PASS ' + script, flush=True)
            if script == 'endpoint_audit.py':
                frozen = root / 'Evidence/Provenance/raw_validation_plan.json'
                original_plan = read_json(frozen)
                reproduced_plan = read_json(provenance / 'raw_validation_plan.json')
                original_plan.pop('label', None)
                reproduced_plan.pop('label', None)
                if not equal_values(original_plan, reproduced_plan):
                    raise ValueError('Corrected source projection changed the frozen sample draw')
                shutil.copyfile(frozen, provenance / 'raw_validation_plan.json')
        expected = read_json(root / 'result_manifest.json')['files']
        published = {p.relative_to(root / 'Results').as_posix() for p in (root / 'Results').rglob('*') if p.is_file()}
        if published != {r['path'] for r in expected}:
            raise ValueError('Published result manifest membership differs')
        for row in expected:
            name = row['path']; frozen = root / 'Results' / name; generated = work / name
            if frozen.stat().st_size != row['bytes'] or digest(frozen) != row['sha256']:
                raise ValueError('Published result identity differs: ' + name)
            if not generated.is_file() or not semantic_equal(frozen, generated):
                raise ValueError('Regenerated scientific result differs: ' + name)
            comparisons.append({'path': name, 'semantic_equal': True, 'byte_equal': digest(frozen) == digest(generated)})
        diagnostic = read_json(additions / 'Independent/task_diagnostics_audit.json')
        histories_receipt = read_json(histories / 'reproduction_receipt.json')
        groups = [read_json(empirical / 'raw_provenance_validation.json')['boundary_tests'],
                  read_json(empirical / 'exception_census_validation.json')['boundary_tests'],
                  diagnostic['scientific_boundary_controls'], histories_receipt['scientific_boundary_controls']]
        if not all(all(group.values()) for group in groups):
            raise ValueError('A scientific boundary control failed')
        if not all(diagnostic['posthoc_heading_count_controls'].values()):
            raise ValueError('A post hoc heading diagnostic changed')
        verify_inputs(root)
        receipt.update(status='PASS_OFFLINE_REPLAY', inputs_unchanged=True, stages=executed, comparisons=comparisons,
                       outputs_compared=len(comparisons), scientific_boundary_controls_passed=sum(len(g) for g in groups),
                       posthoc_heading_controls_passed=len(diagnostic['posthoc_heading_count_controls']),
                       scope='Retained raw service captures and derived source evidence; original full-source execution remains a separate supplied result')
        for row in expected:
            destination = output / 'Results' / row['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / row['path'], destination)
    except Exception as error:
        receipt.update(status='FAIL_CLOSED', failure=str(error), stages=executed, comparisons=comparisons)
        raise
    finally:
        receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (output / 'reproduction_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: receipt[key] for key in ['status', 'outputs_compared', 'scientific_boundary_controls_passed', 'elapsed_seconds']}, indent=2))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == '__main__':
    main()
