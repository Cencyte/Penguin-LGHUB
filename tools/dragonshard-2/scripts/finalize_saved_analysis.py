#!/usr/bin/env python3
"""Publish a saved full-analysis round only after whole-database width-policy validation."""
import argparse
import json
from pathlib import Path
from dragonshard import atomic_json, digest, locks
from packed_export import validate_checkpoint
from parallel_analysis import load_width_policy, partition, validate_worker


def check_proof(request, report, coverage, checkpoint, merged, sha, policy):
    if (request.get('mode') != 'validate_width_fixed_point' or 'ranges' in request or
        Path(request['baseline']).resolve() != (checkpoint / 'program.gzf').resolve() or
        Path(request['one']).resolve() != (merged / 'program.gzf').resolve() or
        request.get('sha256') != sha or request.get('reviewed_untyped_widths') != policy):
        raise ValueError('Validation request did not cover the required whole snapshots and policy')
    if (report.get('mode') != 'validate_width_fixed_point' or
        not report.get('complete') or report.get('sha256') != sha or
        report.get('actual_diff_filter') != 65535 or report.get('unaccepted_difference_ranges') != [] or
        not report.get('fixed_point_under_recorded_width_policy') or
        report.get('reviewed_untyped_widths') != policy or
        report.get('comparison_scope', 'whole_program') != 'whole_program'):
        raise ValueError('Whole-database fixed-point proof failed')
    if (coverage.get('sha256') != sha or
        coverage.get('baseline_sha256') != digest(checkpoint / 'program.gzf') or
        coverage.get('merge_sha256') != digest(merged / 'program.gzf')):
        raise ValueError('Proof inputs changed since coverage validation')


def run(args):
    source, proof, root = args.source.resolve(), args.proof.resolve(), args.directory.resolve()
    manifest = json.loads((source / 'manifest.json').read_text())
    if len(manifest['programs']) != 1:
        raise ValueError('One selected binary required')
    program = manifest['programs'][0]
    checkpoint, merged = source / args.checkpoint, source / args.round / 'merge'
    count = validate_checkpoint(checkpoint, program)
    before = digest(checkpoint / 'program.gzf')
    merge = json.loads((merged / 'report.json').read_text())
    functions = json.loads((checkpoint / 'functions.json').read_text())
    finish = json.loads((checkpoint / 'report.json').read_text())
    audit = json.loads((checkpoint / 'depth.json').read_text())['after']
    if (len(functions) != finish['functions'] or
        len(functions) != count - audit['functions_without_entry_instruction'] or
        len({f['address'] for f in functions}) != len(functions)):
        raise ValueError('Native-function inventory does not match the saved depth audit')
    shards = partition(functions, args.workers)
    if not merge.get('complete') or merge['sha256'] != program['sha256'] or merge['baseline_sha256'] != before:
        raise ValueError('Saved merge identity mismatch')
    expected = set()
    for i, addresses in enumerate(shards):
        worker = source / args.round / 'workers' / f'{i:03d}'
        validate_worker(json.loads((worker / 'report.json').read_text()), addresses, program['sha256'], before)
        expected.add(str(worker.resolve()))
    steps = merge['steps']
    if (len(steps) != len(expected) or {str(Path(s['worker']).resolve()) for s in steps} != expected or
        any(s['conflicts'] for s in steps)):
        raise ValueError('Saved merge did not verify exactly these workers')
    policy = load_width_policy(args.policy, program['sha256'])
    check_proof(json.loads((proof / 'request.json').read_text()), json.loads((proof / 'report.json').read_text()),
                json.loads((proof / 'coverage.json').read_text()), checkpoint, merged, program['sha256'], policy)
    if locks(dict(project_dir=finish['source_dir'], project_name=finish['source_name'])):
        raise ValueError('Final source project is not closed')
    root.mkdir(parents=True, exist_ok=False)
    atomic_json(root / 'owner.json', dict(tool='dragonshard-2-parallel-analysis'))
    atomic_json(root / 'manifest.json', manifest)
    atomic_json(root / 'width-policy.json', json.loads(args.policy.read_text()))
    descriptor = dict(complete=True, sha256=program['sha256'], constant_propagation_fixed_point=True,
        source_dir=finish['source_dir'], source_name=finish['source_name'], audit=finish['audit'],
        packed_file=str(checkpoint / 'program.gzf'), packed_sha256=before,
        reviewed_undefined_width_policy=str(root / 'width-policy.json'),
        verification_report=str(proof / 'report.json'), coverage_report=str(proof / 'coverage.json'),
        verified_workers=len(shards), functions=count, native_functions=len(functions))
    atomic_json(root / 'complete-analysis.json', descriptor)
    atomic_json(root / 'status.json', dict(state='complete', complete=True, full_auto_analysis_complete=True,
        result=descriptor, requested_workers=args.workers, admitted_workers=0, functions=count,
        scope='Recorded analyzer fixed point modulo explicitly reviewed undefined-data widths'))
    print(json.dumps(descriptor))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('proof', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('policy', type=Path)
    parser.add_argument('--checkpoint', default='finish-03')
    parser.add_argument('--round', default='round-03')
    parser.add_argument('--workers', type=int, default=42)
    run(parser.parse_args())
