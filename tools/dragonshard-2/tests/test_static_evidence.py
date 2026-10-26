import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from static_evidence import Slice


class Memory:
    imports={0x2000:'test.dll!callback'}
    def read(self,address,size):
        return (0x401000).to_bytes(size,'little') if address in (0x1000,0x2000) else None
    def section(self,address):
        return dict(writable=False)


def constant(i,value,size=8):
    return dict(id=i,constant=True,offset=f'{value:x}',size=size)


def operation(i,op,*inputs,size=8):
    return dict(id=i,constant=False,opcode=op,inputs=list(inputs),size=size)


class StaticEvidenceTests(unittest.TestCase):
    def test_pointer_arithmetic_load_records_file_memory_dependency(self):
        s=Slice([operation(0,'LOAD',1,2),constant(1,0),operation(2,'PTRADD',3,4,5),
                 constant(3,0xff0),constant(4,2),constant(5,8)],Memory())
        self.assertEqual(s.values(0),{0x401000})
        self.assertIn(0x1000,s.loads)

    def test_import_thunk_is_not_interpreted_as_disk_value_call_target(self):
        s=Slice([operation(0,'LOAD',1,2),constant(1,0),constant(2,0x2000)],Memory())
        self.assertEqual(s.values(0),set())
        self.assertEqual(s.loads[0x2000]['import_symbol'],'test.dll!callback')

    def test_phi_keeps_known_branch_and_reports_unknown_branch(self):
        s=Slice([operation(0,'MULTIEQUAL',1,2),constant(1,0x401000),
                 dict(id=2,size=8,input=True)],Memory())
        self.assertEqual(s.values(0),{0x401000})
        self.assertIn('input',s.unknown)

    def test_cycle_terminates_without_fabricating_a_target(self):
        s=Slice([operation(0,'COPY',1),operation(1,'COPY',0)],Memory())
        self.assertEqual(s.values(0),set())
        self.assertIn('cycle_or_depth_or_missing_node',s.unknown)

    def test_sign_extension_and_modular_subtraction(self):
        s=Slice([operation(0,'INT_SEXT',1),constant(1,0xff,1)],Memory())
        self.assertEqual(s.values(0),{2**64-1})
        s=Slice([operation(0,'INT_SUB',1,2,size=4),constant(1,0),constant(2,1)],Memory())
        self.assertEqual(s.values(0),{2**32-1})

    def test_unknown_receiver_still_exposes_dispatch_slot(self):
        s=Slice([operation(0,'LOAD',1,2),constant(1,0),operation(2,'INT_ADD',3,4),
                 dict(id=3,size=8,input=True),constant(4,0x28)],Memory())
        self.assertEqual(s.values(0),set())
        self.assertEqual(s.slot(0)['offset'],0x28)

    def test_value_set_limit_is_explicit(self):
        s=Slice([operation(0,'MULTIEQUAL',1,2,3),constant(1,1),constant(2,2),constant(3,3)],Memory(),limit=2)
        self.assertEqual(s.values(0),{1,2});self.assertTrue(s.truncated)

    def test_promoted_global_import_retains_symbol_without_using_file_pointer(self):
        s=Slice([dict(id=0,space='ram',offset='2000',size=8,input=True)],Memory())
        self.assertEqual(s.values(0),set())
        self.assertEqual(s.loads[0x2000]['import_symbol'],'test.dll!callback')

    def test_promoted_global_writable_pointer_remains_a_candidate(self):
        class Writable(Memory):
            def section(self,address):return dict(writable=True)
        s=Slice([dict(id=0,space='ram',offset='1000',size=8,input=True)],Writable())
        self.assertEqual(s.values(0),{0x401000})
        self.assertIn('runtime_writable_storage',s.unknown)

    def test_zero_vtable_slot_and_dependency_origins(self):
        nodes=[operation(0,'LOAD',1,2),constant(1,0),operation(2,'LOAD',1,3),
               dict(id=3,space='register',offset='8',size=8,input=True)]
        s=Slice(nodes,Memory())
        self.assertEqual(s.slot(0)['offset'],0)
        d=s.dependencies(0)
        self.assertEqual(d['nodes'],4)
        self.assertEqual(d['origins'],[dict(space='register',offset='8',size=8,input=True)])


if __name__=='__main__':unittest.main()
