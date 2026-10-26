import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from enrichment_index import SCHEMA, ingest
from enrich_campaign import validate


class EnrichmentIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:');self.db.executescript(SCHEMA)
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def write(self,rows):
        path=self.root/'evidence.jsonl'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows));return path
    def test_candidates_remain_candidates_and_unhashable_records_are_retained(self):
        path=self.write([dict(kind='program',sha256='hash'),
            dict(kind='function',address='100',status='candidate',full_hash='abc',specific_hash='def',
                 matches=[dict(name='candidate_name',library='library',score=22)]),
            dict(kind='function',address='200',status='unhashable',matches=[]),
            dict(kind='function',address='300',status='error',error='unreadable'),dict(kind='complete')])
        ingest(self.db,path,'hash')
        self.assertEqual(self.db.execute('select count(*) from fingerprints').fetchone()[0],3)
        self.assertEqual(self.db.execute('select method from library_candidates').fetchone()[0],'fid_candidate')
        ingest(self.db,path,'hash')
        self.assertEqual(self.db.execute('select count(*) from fingerprints').fetchone()[0],3)
    def test_incomplete_source_rolls_back_all_rows(self):
        path=self.write([dict(kind='program',sha256='hash'),dict(kind='transfer',site='100',candidates=[])])
        with self.assertRaisesRegex(ValueError,'completion'):ingest(self.db,path,'hash')
        self.assertEqual(self.db.execute('select count(*) from evidence').fetchone()[0],0)
        self.assertEqual(self.db.execute('select count(*) from sources').fetchone()[0],0)
    def test_exception_cannot_replace_expected_binary_identity(self):
        path=self.root/'exception.json'
        path.write_text(json.dumps(dict(complete=True,sha256='different',functions=[])))
        with self.assertRaisesRegex(ValueError,'identity'):ingest(self.db,path,'expected',exception=True)
        self.assertEqual(self.db.execute('select count(*) from sources').fetchone()[0],0)
    def test_same_source_path_cannot_silently_change(self):
        path=self.write([dict(kind='program',sha256='hash'),dict(kind='complete')]);ingest(self.db,path,'hash')
        path.write_text(path.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'changed'):ingest(self.db,path,'hash')
    def test_rtti_table_keeps_slot_to_function_association(self):
        path=self.write([dict(kind='program',sha256='hash'),dict(kind='function_pointer_table_candidate',address='200',
            rtti={'name':'class_name'},entries=[dict(slot=3,target='400')]),dict(kind='complete')])
        ingest(self.db,path,'hash')
        self.assertEqual(self.db.execute('select table_address,slot,function_address,type_name from class_members').fetchone(),
                         ('200',3,'400','class_name'))
    def test_fid_validation_rejects_duplicate_even_when_total_count_matches(self):
        rows=[dict(kind='program',sha256='hash',snapshot_id='snap',shard=0,shards=2),
              dict(kind='function',ordinal=0),dict(kind='function',ordinal=0),
              dict(kind='complete',attempted=2,total_enumerated=4)]
        job=dict(program={'sha256':'hash'},snapshot_id='snap',kind='fid',functions=4,shard=0,shards=2)
        with self.assertRaisesRegex(ValueError,'Duplicate'):validate(job,self.write(rows))


if __name__=='__main__':unittest.main()
