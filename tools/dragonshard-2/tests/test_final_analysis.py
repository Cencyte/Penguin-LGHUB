"""A width annotation exception must never admit partial or stale final-analysis proof."""
import copy
import hashlib
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from finalize_saved_analysis import check_proof


class FinalAnalysisProofTests(unittest.TestCase):
    def test_only_complete_whole_same_snapshot_proof_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); before=root/'before'; after=root/'after'
            before.mkdir(); after.mkdir()
            (before/'program.gzf').write_bytes(b'before')
            (after/'program.gzf').write_bytes(b'after')
            policy=[dict(address='100',hex='0000')]
            request=dict(mode='validate_width_fixed_point',baseline=str(before/'program.gzf'),
                         one=str(after/'program.gzf'),sha256='binary',reviewed_untyped_widths=policy)
            report=dict(mode='validate_width_fixed_point',complete=True,sha256='binary',actual_diff_filter=65535,
                        unaccepted_difference_ranges=[],fixed_point_under_recorded_width_policy=True,
                        reviewed_untyped_widths=policy,comparison_scope='whole_program')
            coverage=dict(sha256='binary',baseline_sha256=hashlib.sha256(b'before').hexdigest(),
                          merge_sha256=hashlib.sha256(b'after').hexdigest())
            check_proof(request,report,coverage,before,after,'binary',policy)
            for target, change in [('request',dict(ranges=[['100','101']])),
                ('report',dict(unaccepted_difference_ranges=[['200','201']])),
                ('report',dict(complete=False)),('report',dict(actual_diff_filter=1)),
                ('report',dict(comparison_scope='explicit_address_ranges')),
                ('coverage',dict(merge_sha256='stale'))]:
                values=copy.deepcopy(dict(request=request,report=report,coverage=coverage))
                values[target].update(change)
                with self.subTest(target=target,change=change), self.assertRaises(ValueError):
                    check_proof(**values,checkpoint=before,merged=after,sha='binary',policy=policy)
            (after/'program.gzf').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                check_proof(request,report,coverage,before,after,'binary',policy)


if __name__=='__main__':
    unittest.main()
