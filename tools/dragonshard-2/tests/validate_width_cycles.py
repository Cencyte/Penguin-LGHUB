#!/usr/bin/env python3
"""Real Ghidra regression: ignore reviewed width churn, never concrete/comment conflicts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def run_case(ghidra, baseline, root, mode):
    root.mkdir(parents=True, exist_ok=False)
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    def launch(name, script, arguments):
        task = root / name
        task.mkdir()
        for directory in ('driver', 'temp', 'cache'):
            (task / directory).mkdir()
        env = dict(os.environ, GHIDRA_HEADLESS_MAXMEM='640m',
                   JAVA_TOOL_OPTIONS=f'-Djava.io.tmpdir={task / "temp"} -Dpdb.cache.dir={task / "cache"}')
        with (task / 'headless.log').open('w') as log:
            subprocess.run([str(ghidra / 'support/analyzeHeadless'), str(task / 'driver'), 'Driver',
                            '-noanalysis', '-max-cpu', '1', '-scriptPath', str(scripts) + ';' + str(Path(__file__).parent),
                            '-preScript', script, *map(str, arguments)], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    launch('generate', 'DragonShardConflictFixture.java', [baseline, root, mode])
    workers = [root / 'left', root / 'right']
    reports = [json.loads((worker / 'report.json').read_text()) for worker in workers]
    policy = json.loads((root / 'width-policy.json').read_text())
    comparison = root / 'merge-base.gzf'
    shutil.copyfile(baseline, comparison)
    output = root / 'result'
    request = dict(mode='merge', baseline=str(baseline), merge_base=str(comparison), output=str(output),
                   sha256=reports[0]['sha256'], baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
                   workers=list(map(str, workers)), retain_access_widths=True,
                   reviewed_untyped_widths=policy['reviewed_untyped_widths'],
                   pack_lock=str(root / 'packing.lock'), max_output_bytes=64 * 1024**2)
    request_path = root / 'request.json'
    request_path.write_text(json.dumps(request, indent=2) + '\n')
    launch('merge', 'DragonShardAnalyze.java', [request_path])
    result = json.loads((output / 'report.json').read_text())
    if mode == 'widths':
        assert result['complete'] and result['changed_address_bytes'] == 0, result
        assert len(result['steps']) == 2
        assert all(s['retained_access_width_alternatives'] for s in result['steps'])
    else:
        assert not result['complete'] and 'Conflicting worker mutations' in result['error'], result
        assert not (output / 'program.gzf').exists()
    proof_output = root / 'fixed-point-result'
    proof_request = dict(mode='validate_width_fixed_point', baseline=str(baseline),
                         one=str(workers[1] / 'program.gzf'), output=str(proof_output),
                         sha256=reports[0]['sha256'], reviewed_untyped_widths=policy['reviewed_untyped_widths'])
    proof_path = root / 'proof-request.json'
    proof_path.write_text(json.dumps(proof_request, indent=2) + '\n')
    launch('proof', 'DragonShardAnalyze.java', [proof_path])
    proof = json.loads((proof_output / 'report.json').read_text())
    if mode == 'widths':
        assert proof['complete'] and proof['fixed_point_under_recorded_width_policy'], proof
        assert proof['all_difference_ranges'] and not proof['unaccepted_difference_ranges'], proof
    else:
        assert not proof['complete'] and proof['unaccepted_difference_ranges'], proof
    return dict(case=mode, passed=True, report=str(output / 'report.json'), proof=str(proof_output / 'report.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ghidra', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda mode: run_case(args.ghidra.resolve(), args.baseline.resolve(), root / mode, mode),
                                ['widths', 'types', 'comments']))
    (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))
