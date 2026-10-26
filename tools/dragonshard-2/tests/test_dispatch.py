import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dispatch_corpus import classify, index, trap_kind
from reconcile_dispatch import memory_operand


class DispatchTests(unittest.TestCase):
    def test_trap_requires_matching_machine_opcode(self):
        self.assertEqual(trap_kind('INT3',b'\xcc'),'breakpoint')
        self.assertEqual(trap_kind('INT 0x29',b'\xcd\x29'),'fast_fail')
        self.assertIsNone(trap_kind('INT 0x29',b'\xff\xe1'))

    def test_global_storage_is_not_a_concrete_receiver(self):
        self.assertEqual(classify({'dependencies':{'origins':[{'space':'ram'}]}}),'runtime_storage')
        self.assertEqual(classify({'dispatch_slot':{'offset':0}}),'object_dispatch_slot')

    def test_rip_relative_import_address_handles_rex_and_signed_offset(self):
        self.assertEqual(memory_operand(0x1000,bytes.fromhex('48ff25f9ffffff')),0x1000)
        self.assertEqual(memory_operand(0x1000,bytes.fromhex('ff1510000000')),0x1016)
        self.assertIsNone(memory_operand(0x1000,bytes.fromhex('ff5008')))

    def test_site_denominator_preserves_traps_orphans_and_multiple_transfers(self):
        class Image:
            def __init__(self,*args):pass
            def read(self,address,size):return b'\xcc' if address==0x100 else b'\xff\x10'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);gap=root/'gap.jsonl';source=root/'targets.jsonl'
            header={'kind':'program','sha256':'sha','snapshot_id':'snapshot'}
            rows=[header,{'kind':'unresolved_computed_transfer','address':'100','instruction':'INT3'},
                  {'kind':'unresolved_computed_transfer','address':'200','function':'180','instruction':'CALL RAX'},
                  {'kind':'unresolved_computed_transfer','address':'300','instruction':'JMP RAX'},
                  {'kind':'complete'}]
            gap.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            rows=[header,{'kind':'transfer','site':'200','dependencies':{'origins':[{'space':'register'}]}},
                  {'kind':'transfer','site':'200','dispatch_slot':{'offset':0}}, {'kind':'complete'}]
            source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            queue={'jobs':{'one':{'kind':'gaps','program':{'sha256':'sha','name':'test.dll','binary_path':'fixture'},
                'snapshot_id':'snapshot','result':{'output':str(gap)}}}}
            with patch('dispatch_corpus.PE',Image):result=index(queue,[{'output':str(source)}],root)
            self.assertEqual(result['sites'],3)
            self.assertEqual(result['unowned_nontrap_sites'],1)
            self.assertEqual(result['classification'],{'no_high_slice':1,'object_dispatch_slot':1,'processor_trap':1})
            with sqlite3.connect(root/'dispatch.sqlite') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT SUM(runtime_target_proven) FROM sites').fetchone()[0],0)


if __name__=='__main__':unittest.main()
