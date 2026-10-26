"""The narrow retry must never restart running or unrelated queue work."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from retry_symbol_export import select_retry


class SymbolRetryTests(unittest.TestCase):
    def test_only_stopped_first_shard_and_dependent_index_are_accepted(self):
        export = dict(id='a.export.0',kind='export',state='failed',shard=0,
                      packed_file='snapshot.gzf',program=dict(sha256='a'))
        index = dict(id='a.index',kind='index',state='pending',deps=['a.export.0'],program=dict(sha256='a'))
        queue = dict(jobs={j['id']:j for j in (export,index)},programs=[dict(state='exporting')])
        self.assertEqual(select_retry(queue)['id'], 'a.export.0')
        for target,changes in [('a.export.0',dict(state='running')),('a.export.0',dict(shard=1)),
            ('a.export.0',dict(packed_file=None)),('a.index',dict(state='failed')),
            ('a.index',dict(deps=[])),('a.index',dict(program=dict(sha256='b')))]:
            wrong=copy.deepcopy(queue);wrong['jobs'][target].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                select_retry(wrong)


if __name__=='__main__':
    unittest.main()
