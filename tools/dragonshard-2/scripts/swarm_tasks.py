#!/usr/bin/env python3
"""Isolated task executors for the DragonShard dependency scheduler."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

from dragonshard import HERE, analysis_blockers, atomic_json, digest, locks, source_state
from corpus import build, inspect


def evidence_summary(path, sha, snapshot):
    with path.open() as stream:
        header = json.loads(next(stream))
        last = None
        for line in stream:
            if last and last.get('kind') == 'complete':
                raise RuntimeError('Evidence after completion marker')
            last = json.loads(line)
    if header.get('sha256') != sha or header.get('snapshot_id') != snapshot or not last or last.get('kind') != 'complete':
        raise RuntimeError('Incomplete or mismatched evidence')
    return last


def run_ghidra(spec, project_dir, project_name, script, arguments, *, writable=False, heap='900m', process=True):
    manifest, program = spec['manifest'], spec['program']
    environment = dict(os.environ, GHIDRA_HEADLESS_MAXMEM=heap)
    if spec.get('packed_file'):
        process = False
        temporary = Path(project_dir) / 'temp'
        temporary.mkdir(exist_ok=True)
        paths = [str(temporary), spec['packed_file'], spec['packed_cache']]
        if any(' ' in p for p in paths):
            raise ValueError('Packed JVM option paths cannot contain spaces')
        environment['JAVA_TOOL_OPTIONS'] = ' '.join([
            f'-Djava.io.tmpdir={temporary}', f'-Dpdb.cache.dir={spec["packed_cache"]}',
            f'-Ddragonshard.packed.path={spec["packed_file"]}',
            f'-Ddragonshard.project.path={program["project_path"]}',
            '-Ddragonshard.analysis.stage=validated_final_analysis'])
    folder = str(PurePosixPath(program['project_path']).parent).strip('/')
    project = project_name + ('/' + folder if process and folder else '')
    command = [str(Path(manifest['ghidra']) / 'support/analyzeHeadless'), str(project_dir), project,
               '-noanalysis', '-max-cpu', str(spec['cpu']), '-scriptPath', str(HERE)]
    if process:
        command += ['-process', PurePosixPath(program['project_path']).name]
        if not writable:
            command += ['-readOnly']
    command += ['-postScript' if process else '-preScript', script, *map(str, arguments)]
    subprocess.run(command, env=environment, check=True)


def execute(spec):
    kind, work = spec['kind'], Path(spec['work'])
    manifest, program = spec['manifest'], spec['program']
    sha = program['sha256']
    work.mkdir(parents=True, exist_ok=True)
    if kind == 'warm':
        if digest(spec['packed_file']) != spec['packed_sha256']:
            raise RuntimeError('Packed analysis changed before warming')
        driver = work / 'warm-driver'
        driver.mkdir(exist_ok=False)
        output = work / 'warm-result'
        request = work / 'warm-request.json'
        atomic_json(request, dict(mode='warm_cache', sha256=sha, output=str(output),
                                 cache_paths=[spec['packed_file']]))
        run_ghidra(spec, driver, 'Driver', 'DragonShardAnalyze.java', [request], process=False)
        result = json.loads((output / 'report.json').read_text())
        if not result.get('complete') or result.get('sha256') != sha:
            raise RuntimeError('Packed analysis warming failed')
        return dict(packed_file=spec['packed_file'], snapshot_id=spec['packed_sha256'])
    if kind == 'stage':
        source = work / 'source'
        source.mkdir(exist_ok=False)
        selection = dict(manifest, programs=[program])
        atomic_json(work / 'selection.json', selection)
        run_ghidra(spec, source, 'Program', 'DragonShardStage.java',
                   [work / 'selection.json', work / 'staging.json'], writable=True, heap='2G', process=False)
        report = json.loads((work / 'staging.json').read_text())
        if not report.get('complete') or [p['sha256'] for p in report['programs']] != [sha]:
            raise RuntimeError('Staging scope mismatch')
        return {'source_dir': str(source), 'source_name': 'Program'}
    if kind == 'analyze':
        report = work / 'depth.json'
        run_ghidra(spec, work / 'source', 'Program', 'DragonShardDeepen.java',
                   [report, sha, 'analyze'], writable=True, heap=spec.get('heap', '4G'))
        audit_program = dict(program, analysis_audit=str(report))
        blockers = analysis_blockers(audit_program)
        if blockers or list((work / 'source').glob('Program.lock*')):
            raise RuntimeError(f'Incomplete analysis: {blockers}')
        return {'audit': str(report), 'source_dir': str(work / 'source'), 'source_name': 'Program'}
    if kind in ('export', 'gaps', 'indirect'):
        # A program's full saved analysis is immutable for every one of its shards.
        source = dict(manifest, project_dir=spec['source_dir'], project_name=spec['source_name'])
        packed = spec.get('packed_file')
        state = None if packed else source_state(source)
        snapshot = digest(packed) if packed else hashlib.sha256(json.dumps(state, separators=(',', ':')).encode()).hexdigest()
        if snapshot != spec['snapshot_id'] or (not packed and locks(source)):
            raise RuntimeError('Analysis snapshot changed or reopened')
        output = (work / 'exports' / f"{spec['shard']:04d}-of-{spec['shards']:04d}.jsonl" if kind == 'export' else
                  work / 'indirect' / (spec['id'] + '.jsonl') if kind == 'indirect' else work / 'gaps.jsonl')
        clone = work / 'clones' / (f"{spec['shard']:04d}" if kind == 'export' else spec['id'])
        clone.mkdir(parents=True, exist_ok=False)
        atomic_json(clone / 'owner.json', {'tool': 'dragonshard-2-swarm', 'task': spec['id'], 'sha256': sha})
        if not packed:
            for suffix in ('.gpr', '.rep'):
                subprocess.run(['cp', '--reflink=auto', '-r', '--', str(Path(spec['source_dir']) / (spec['source_name'] + suffix)), str(clone)], check=True)
            if state != source_state(source) or locks(source):
                raise RuntimeError('Analysis snapshot changed while cloning')
        if kind == 'export':
            run_ghidra(spec, clone, spec['source_name'], 'DragonShardExport.java',
                       [output, spec['shard'], spec['shards'], manifest.get('timeout_seconds', 60), sha, snapshot,
                        spec['output_limit_bytes']])
            header, end = inspect(output)
            if (header['sha256'], header['snapshot_id'], header['shard'], header['shards']) != (sha, snapshot, spec['shard'], spec['shards']):
                raise RuntimeError('Unexpected export identity')
            result = {'output': str(output), 'attempted': end['attempted'], 'ok': end['ok'],
                      'failed': end['failed'], 'timeout': end['timeout']}
        elif kind == 'gaps':
            run_ghidra(spec, clone, spec['source_name'], 'DragonShardGaps.java',
                       [output, sha, snapshot, spec['output_limit_bytes']])
            result = {'output': str(output), 'summary': evidence_summary(output, sha, snapshot)}
        else:
            request = work / 'requests' / (spec['id'] + '.json')
            request.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(request, {'sha256': sha, 'snapshot_id': snapshot, 'functions': spec['functions'],
                                 'audit_requested_sites': bool(spec.get('audit_requested_sites', False)),
                                 'raw_sites': spec.get('raw_sites', []),
                                 'reviewed_regions': spec.get('reviewed_regions', []),
                                 'recovery_style': spec.get('recovery_style', 'normalize'),
                                 'retain_unreachable': bool(spec.get('retain_unreachable', False))})
            run_ghidra(spec, clone, spec['source_name'], 'DragonShardIndirect.java',
                       [request, output, spec['output_limit_bytes']])
            end = evidence_summary(output, sha, snapshot)
            with output.open() as stream:
                rows = [json.loads(line) for line in stream]
            records = [row for row in rows if row['kind'] == 'function']
            contexts = [row['site'] for row in rows if row['kind'] == 'site_context']
            if len(contexts) != len(spec.get('raw_sites', [])) or set(contexts) != set(spec.get('raw_sites', [])):
                raise RuntimeError('Instruction context did not cover the requested sites')
            if (len(records) != end['attempted'] or len(records) != len(spec['functions']) or
                {r['address'] for r in records} != {f['address'] for f in spec['functions']}):
                raise RuntimeError('Indirect evidence did not cover the requested functions')
            result = {'output': str(output), 'summary': end}
        # Read-only worker exited, its completed evidence validated; this task owns this replica.
        if packed and digest(packed) != snapshot:
            raise RuntimeError('Packed analysis changed during evidence export')
        owner = json.loads((clone / 'owner.json').read_text())
        if clone.is_symlink() or owner['task'] != spec['id']:
            raise RuntimeError('Clone ownership changed')
        shutil.rmtree(clone)
        return result
    if kind == 'index':
        report = build((work / 'exports').glob('*.jsonl'), work / 'corpus.sqlite', {sha: program},
                       max_bytes=spec['output_limit_bytes'])
        if len(report) != 1 or report[0]['sha256'] != sha:
            raise RuntimeError('Index scope mismatch')
        listing = report[0]['listing_evidence']
        n = report[0]['recognized_internal_functions']
        missing = listing.get('zero_instruction_functions', n) - listing.get('synthetic_external_thunks', 0)
        if listing.get('functions_with_instruction_metadata') != n or missing > .01 * n or missing < 0:
            raise RuntimeError('Index listing depth failed validation')
        atomic_json(work / 'coverage.json', report)
        return {'database': str(work / 'corpus.sqlite'), 'coverage': report}
    raise ValueError(f'Unknown task kind: {kind}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    args = parser.parse_args()
    spec = json.loads(args.task.read_text())
    result = execute(spec)
    atomic_json(args.task.with_suffix('.result.json'), dict(result, task=spec['id'], complete=True))


if __name__ == '__main__':
    main()
