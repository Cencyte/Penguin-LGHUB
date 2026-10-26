"""Concurrency policy must fill usable capacity without weakening dependencies or reserves."""
import sys
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from swarm import Swarm, ready_jobs, select_jobs


def job(name, *, state='pending', deps=(), priority=0, cpu=1, memory=1, disk=.1):
    return dict(id=name, state=state, deps=list(deps), priority=priority, cpu=cpu,
                memory_gib=memory, disk_gib=disk)


class SwarmPolicyTests(unittest.TestCase):
    def test_packed_exports_wait_for_cache_warm_and_budget_no_full_clones(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'audit.json').write_text(json.dumps({'after': {'functions': 1000}}))
            swarm = Swarm({}, root, cpu_limit=42)
            program = dict(sha256='a' * 64, functions=1000, name='fixture')
            state = dict(program=program, sha256=program['sha256'])
            result = dict(audit=str(root / 'audit.json'), source_dir=str(root), source_name='Fixture',
                          packed_file=str(root / 'packed.gzf'), packed_sha256='packed-hash')
            with patch('swarm.analysis_blockers', return_value=[]), patch('swarm.locks', return_value=[]), \
                 patch('swarm.source_state', return_value={}), patch('swarm.digest', return_value='packed-hash'):
                swarm.prepare_exports(state, result)
            ready = ready_jobs(swarm.jobs)
            self.assertEqual([j['kind'] for j in ready], ['warm'])
            swarm.jobs[ready[0]['id']]['state'] = 'complete'
            self.assertEqual({j['kind'] for j in ready_jobs(swarm.jobs)}, {'export', 'gaps'})
            for job in ready_jobs(swarm.jobs):
                self.assertEqual(job['snapshot_id'], 'packed-hash')
                self.assertLess(job['disk_gib'], .1)
            state.update(state='complete', indirect_jobs=[])
            swarm.programs[program['sha256']] = state
            self.assertEqual(swarm.output_reserve(), .5)

    def test_reboot_follows_owned_parallel_producer_instead_of_dead_serial_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            producer = root / 'parallel'; producer.mkdir()
            (root / 'parallel-analysis.json').write_text(json.dumps(dict(directory=str(producer), unit='dragonshard2-parallel-test.service')))
            (producer / 'owner.json').write_text(json.dumps(dict(tool='dragonshard-2-parallel-analysis')))
            (producer / 'manifest.json').write_text(json.dumps(dict(programs=[dict(sha256='binary')])))
            swarm = Swarm({}, root, adopt=root)
            swarm.programs['binary'] = dict(state='waiting_external', sha256='binary', name='test',
                external_dir=str(root / 'old'), external_unit='dragonshard2-agent-ahead-dead.service')
            with patch('swarm.subprocess.run') as run:
                run.return_value.stdout = 'ActiveState=active\nMemoryCurrent=0\n'
                self.assertEqual(swarm.external_updates(), 56)
                self.assertIn('dragonshard2-parallel-test.service', run.call_args.args[0])
            # An unrelated manifest cannot silently substitute for the expected binary.
            swarm.external_checked_at = 0
            (producer / 'manifest.json').write_text(json.dumps(dict(programs=[dict(sha256='unrelated')])))
            with patch('swarm.subprocess.run') as run:
                run.return_value.stdout = 'ActiveState=inactive\nMemoryCurrent=0\n'
                with self.assertRaises(RuntimeError):
                    swarm.external_updates()
                self.assertIn('dragonshard2-agent-ahead-dead.service', run.call_args.args[0])

    def test_export_of_one_binary_overlaps_analysis_of_another(self):
        jobs = {j['id']: j for j in [job('a.analysis', state='complete'),
            job('a.export.0', deps=['a.analysis']), job('b.analysis'), job('b.export', deps=['b.analysis'])]}
        self.assertEqual({j['id'] for j in ready_jobs(jobs)}, {'a.export.0', 'b.analysis'})

    def test_all_fitting_ready_work_is_dispatched(self):
        jobs = [job(str(i)) for i in range(100)]
        chosen, blocked = select_jobs(jobs, cpu_slots=48, memory_gib=100, disk_gib=100)
        self.assertEqual(len(chosen), 48)
        self.assertEqual(set(blocked.values()), {'cpu_capacity'})

    def test_large_blocked_job_does_not_stall_smaller_ready_work(self):
        chosen, blocked = select_jobs([job('big', memory=12), job('small', memory=1)],
                                     cpu_slots=8, memory_gib=4, disk_gib=10)
        self.assertEqual([j['id'] for j in chosen], ['small'])
        self.assertEqual(blocked, {'big': 'memory_reserve'})

    def test_index_waits_only_for_its_own_shards(self):
        jobs = {j['id']: j for j in [job('a.0', state='complete'), job('a.1', state='complete'),
            job('a.index', deps=['a.0', 'a.1']), job('b.analysis', state='running')]}
        self.assertEqual([j['id'] for j in ready_jobs(jobs)], ['a.index'])

    def test_failed_dependency_never_unblocks_consumer(self):
        jobs = {j['id']: j for j in [job('a', state='failed'), job('index', deps=['a'])]}
        self.assertEqual(ready_jobs(jobs), [])

    def test_disk_and_memory_are_cumulative_reservations(self):
        chosen, blocked = select_jobs([job('a', memory=2, disk=2), job('b', memory=2, disk=2)],
                                     cpu_slots=8, memory_gib=10, disk_gib=3)
        self.assertEqual([j['id'] for j in chosen], ['a'])
        self.assertEqual(blocked, {'b': 'disk_reserve'})

    def test_analysis_cpu_requirement_fits_a_smaller_machine(self):
        swarm = Swarm({}, Path('/unused'), cpu_limit=2)
        identifier = swarm.add_job({'sha256': 'a' * 64, 'functions': 100, 'name': 'fixture'}, 'analyze')
        self.assertEqual(swarm.jobs[identifier]['cpu'], 2)

    def test_no_fixed_disk_cushion_but_pending_outputs_remain_reserved(self):
        swarm = Swarm({}, Path('/unused'))
        self.assertEqual(swarm.disk_reserve_gib, 0)
        swarm.programs['a'] = dict(state='complete', sha256='a', program={'functions': 100}, indirect_jobs=[])
        self.assertEqual(swarm.output_reserve(), .125)
        swarm.programs['a']['gap_audit'] = {'output': 'verified.jsonl'}
        self.assertEqual(swarm.output_reserve(), 0)

    def test_indirect_sites_are_grouped_once_per_function_then_partitioned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'gaps.jsonl'
            records = [dict(kind='unresolved_computed_transfer', function=str(i), address=str(i * 16 + j))
                       for i in range(300) for j in range(2)]
            records.append(dict(kind='unresolved_computed_transfer', address='unowned'))
            path.write_text(''.join(json.dumps(r) + '\n' for r in records))
            swarm = Swarm({}, root, cpu_limit=48)
            program = dict(sha256='a' * 64, name='fixture', functions=300)
            state = dict(program=program)
            gap_job = dict(program=program, source_dir=str(root), source_name='Project', snapshot_id='snapshot')
            swarm.add_indirect(state, gap_job, {'output': str(path)})
            requests = [f for job in swarm.jobs.values() for f in job['functions']]
            self.assertEqual(len(requests), 300)
            self.assertEqual(len({r['address'] for r in requests}), 300)
            self.assertTrue(all(len(r['sites']) == 2 for r in requests))
            self.assertEqual(state['unowned_indirect_sites'], 1)
            self.assertEqual(len(swarm.jobs), 3)


if __name__ == '__main__':
    unittest.main()
