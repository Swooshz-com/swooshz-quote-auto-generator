"""Reject anchor moves that preserve mutual alignment or column-only bounds."""
import unittest
from tests import test_current_workbook_receipt as fixture

NS = fixture.NS


class WorkbookAnchorBoundsTest(unittest.TestCase):
    setUpClass = classmethod(fixture.CurrentWorkbookReceiptTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.CurrentWorkbookReceiptTest.tearDownClass.__func__)
    verify = fixture.CurrentWorkbookReceiptTest.verify
    mutate = fixture.CurrentWorkbookReceiptTest.mutate
    def test_large_end_row_cannot_pass_column_only_check(self):
        for shape in ('sp', 'pic'):
            with self.subTest(shape=shape):
                def edit(tree):
                    node = next(anchor for anchor in tree if anchor.find('xdr:' + shape, NS) is not None)
                    node.find('xdr:to/xdr:row', NS).text = '1048575'
                with self.assertRaisesRegex(AssertionError, 'outside print area rows|overlaps logo'):
                    self.verify(self.mutate('xl/drawings/drawing1.xml', edit))

    def test_both_aligned_anchors_moved_to_first_column_are_rejected(self):
        def edit(tree):
            for anchor in tree:
                for marker in ('from', 'to'):
                    anchor.find('xdr:' + marker + '/xdr:col', NS).text = '0'
        with self.assertRaisesRegex(AssertionError, 'differs from selected template'):
            self.verify(self.mutate('xl/drawings/drawing1.xml', edit))

    def test_header_rows_moved_together_within_print_area_are_rejected(self):
        def edit(tree):
            header = next(anchor for anchor in tree if anchor.find('xdr:sp', NS) is not None)
            for marker in ('from', 'to'):
                node = header.find('xdr:' + marker + '/xdr:row', NS)
                node.text = str(int(node.text) + 1)
        with self.assertRaisesRegex(AssertionError, 'differs from selected template'):
            self.verify(self.mutate('xl/drawings/drawing1.xml', edit))

    def test_context_mismatch_errors_name_the_field(self):
        import copy
        from scripts.verify_current_workbook import verify_workbook
        for path, message in ((('client', 'name'), 'Customer name differs in A6'),
                              (('project', 'title'), 'Project title differs in A18'),
                              (('company', 'name'), 'Company name differs'),
                              (('project_number',), 'Project number differs')):
            with self.subTest(path=path):
                payload = copy.deepcopy(self.payload)
                target = payload
                for key in path[:-1]: target = target[key]
                target[path[-1]] = 'Different synthetic value'
                with self.assertRaisesRegex(AssertionError, message):
                    verify_workbook(self.data, payload, self.rows, self.logo_receipt)

    def test_selected_template_supplies_columns_instead_of_fixture_literals(self):
        from pathlib import Path
        import zipfile
        import xml.etree.ElementTree as ET
        from scripts.verify_current_workbook import verify_workbook

        def move_columns(tree):
            for anchor in tree:
                for marker in ('from', 'to'):
                    node = anchor.find('xdr:' + marker + '/xdr:col', NS)
                    node.text = str(int(node.text) - 1)

        template_path = Path(self.temporary.name) / 'selected-layout.xlsx'
        original = Path(fixture.ROOT/'templates/quote-layout/quotation-layout.xlsx')
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(template_path, 'w') as target:
            for name in source.namelist():
                data = source.read(name)
                if name == 'xl/drawings/drawing1.xml':
                    tree = ET.fromstring(data)
                    move_columns(tree)
                    data = ET.tostring(tree)
                target.writestr(name, data)
        data = self.mutate('xl/drawings/drawing1.xml', move_columns)
        self.assertEqual(verify_workbook(data, self.payload, self.rows, self.logo_receipt,
                                         template_path=template_path)['status'], 'valid')
        with self.assertRaisesRegex(AssertionError, 'differs from selected template'):
            verify_workbook(data, self.payload, self.rows, self.logo_receipt)

        # The CLI deliberately uses bundled geometry, even when library callers
        # provide an alternative template. No CLI selected-profile claim is made.
        import json
        import subprocess
        import sys
        expected = Path(self.temporary.name) / 'cli-expectations.json'
        expected.write_bytes(json.dumps({'payload': self.payload, 'rows': self.rows,
                                        'logo': self.logo_receipt}).encode('utf-8'))
        workbook = Path(self.temporary.name) / 'cli-workbook.xlsx'
        for contents, code in ((self.data, 0), (data, 1)):
            workbook.write_bytes(contents)
            result = subprocess.run([sys.executable, 'scripts/verify_current_workbook.py',
                                     str(workbook), str(expected)], cwd=fixture.ROOT,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, code, result.stderr)
            if code:
                self.assertIn('differs from selected template', result.stderr)
