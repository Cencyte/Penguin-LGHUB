from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pdb_overlay import identifiers, index_destination, verify_section_layout


class PdbOverlayTests(unittest.TestCase):
    def setUp(self):
        self.pe = SimpleNamespace(base=0x180000000, sections=[
            {'start': 0x180001000}, {'start': 0x1800f1000}])
        self.headers = '''SECTION HEADER #1
  .text name
  EFA49 virtual size
  1000 virtual address
SECTION HEADER #2
  .text_hf\ufffd\ufffd\r name
  DFA8C virtual size
  F1000 virtual address
'''

    def test_full_width_section_name_garbage_does_not_change_addresses(self):
        verify_section_layout(self.headers, self.pe)

    def test_wrong_address_rejected_despite_valid_section_count(self):
        with self.assertRaisesRegex(ValueError, 'layout mismatch'):
            verify_section_layout(self.headers.replace('F1000 virtual', 'F2000 virtual'), self.pe)

    def test_missing_or_duplicate_sections_rejected(self):
        for headers in (self.headers.split('SECTION HEADER #2')[0],
                        self.headers + self.headers.split('SECTION HEADER #2')[0]):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                verify_section_layout(headers, self.pe)

    def test_omap_requires_supported_address_translation(self):
        with self.assertRaisesRegex(ValueError, 'OMAP'):
            verify_section_layout('[OMAP From Src]\n' + self.headers, self.pe)

    def test_non_utf8_metadata_retains_exact_identity_and_dbi_age(self):
        raw = (b'PdbStream:\n  Age: 3\n  Guid: {0B6BA9C9-3E14-B527-2985-D031DDE8B845}\n'
               b'DbiStream:\n  Age: 1\n  Name: producer\x8c\n')
        with patch('pdb_overlay.subprocess.run', return_value=subprocess.CompletedProcess([], 0, raw)):
            self.assertEqual(identifiers(Path('fixture.pdb')), {
                'guid': '0B6BA9C93E14B5272985D031DDE8B845', 'age': 1, 'pdb_stream_age': 3})

    def test_resume_preserves_complete_and_partial_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'symbols-recovered.sqlite').write_bytes(b'old evidence')
            (root / 'symbols-recovered-2.sqlite.partial').write_bytes(b'partial evidence')
            self.assertEqual(index_destination(root, True), root / 'symbols-recovered-3.sqlite')
            self.assertEqual((root / 'symbols-recovered.sqlite').read_bytes(), b'old evidence')


if __name__ == '__main__':
    unittest.main()
