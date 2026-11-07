"""Read native LibreOffice recalculation, independent of generator formula caches."""
from decimal import Decimal, ROUND_HALF_UP
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
import xml.etree.ElementTree as ET

from tests import test_current_workbook_receipt as fixture

NS = fixture.NS


def rewrite_sheet(data, edit):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                tree = ET.fromstring(contents)
                edit(tree)
                contents = ET.tostring(tree)
            target.writestr(name, contents)
    return output.getvalue()


def clear_formula_caches(data):
    def clear(tree):
        for cell in tree.findall('.//m:c', NS):
            if cell.find('m:f', NS) is not None:
                value = cell.find('m:v', NS)
                if value is not None:
                    cell.remove(value)
    return rewrite_sheet(data, clear)


def workbook_cells(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        cells = {}
        for cell in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//m:c', NS):
            text = cell.findtext('m:v', None, NS)
            if cell.get('t') == 's':
                text = shared[int(text)]
            elif cell.get('t') == 'inlineStr':
                text = ''.join(cell.find('m:is', NS).itertext())
            cells[cell.get('r')] = (text, cell.get('t'))
    return cells


def computed_amounts(data, rows, rate):
    """Bind each independently calculated amount to its own labelled row."""
    cells = workbook_cells(data)

    def amount(column, label, expected):
        indices = [ref[len(column):] for ref, (text, _) in cells.items()
                   if ref.startswith(column) and ref[len(column):].isdigit() and text == label]
        assert len(indices) == 1, 'Missing or ambiguous recalculated row'
        text, kind = cells['E' + indices[0]]
        assert kind not in ('s', 'inlineStr', 'str', 'e') and text is not None, 'Missing recalculated numeric amount'
        assert Decimal(text) == expected, 'Recalculated amount differs in its own row: E' + indices[0]

    money = lambda number: number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    subtotal = Decimal(0)
    for row in rows:
        value = Decimal(0) if row['price_mode'] == 'Included' else money(Decimal(str(row['quantity'])) * Decimal(str(row['unit_price'])))
        amount('C', row['description'], value)
        subtotal += value
    tax = money(subtotal * Decimal(str(rate)))
    amount('D', 'Total', subtotal)
    amount('D', 'GST ' + format((Decimal(str(rate)) * 100).normalize(), 'f') + '%', tax)
    amount('D', 'Total including GST', subtotal + tax)


def conversion_output(receipt, output):
    assert receipt is not None and receipt.returncode == 0, 'Missing successful native conversion receipt'
    assert output.is_file() and output.stat().st_size > 0, 'Missing native conversion output'
    return output.read_bytes()


class RecalculationGuardTest(unittest.TestCase):
    setUpClass = classmethod(fixture.CurrentWorkbookReceiptTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.CurrentWorkbookReceiptTest.tearDownClass.__func__)

    def test_row_binding_rejects_changed_amount_with_unchanged_totals(self):
        computed_amounts(self.data, self.rows, self.payload['tax']['rate'])
        description_ref = next(ref for ref, (text, _) in workbook_cells(self.data).items()
                               if ref.startswith('C') and text == self.rows[0]['description'])
        def corrupt(tree):
            next(cell for cell in tree.findall('.//m:c', NS)
                 if cell.get('r') == 'E' + description_ref[1:]).find('m:v', NS).text = '76'
        with self.assertRaisesRegex(AssertionError, 'amount differs in its own row'):
            computed_amounts(rewrite_sheet(self.data, corrupt), self.rows, self.payload['tax']['rate'])
        with self.assertRaisesRegex(AssertionError, 'Missing recalculated numeric amount'):
            computed_amounts(clear_formula_caches(self.data), self.rows, self.payload['tax']['rate'])

    def test_bypassed_conversion_requires_receipt_and_output(self):
        with tempfile.TemporaryDirectory(prefix='lo-receipt-') as temporary:
            output = Path(temporary) / 'absent.xlsx'
            with self.assertRaisesRegex(AssertionError, 'conversion receipt'):
                conversion_output(None, output)
            with self.assertRaisesRegex(AssertionError, 'conversion output'):
                conversion_output(subprocess.CompletedProcess([], 0), output)


class LibreOfficeWorkbookValuesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.converter = shutil.which('soffice') or shutil.which('libreoffice')
        windows = Path('C:/Program Files/LibreOffice/program/soffice.exe')
        if cls.converter is None and windows.is_file():
            cls.converter = str(windows)
        if cls.converter is None:
            if os.environ.get('CI'):
                raise AssertionError('LibreOffice is required for CI recalculated workbook values')
            raise unittest.SkipTest('LibreOffice unavailable locally; native recalculation requires CI')
        fixture.CurrentWorkbookReceiptTest.setUpClass.__func__(cls)

    tearDownClass = classmethod(fixture.CurrentWorkbookReceiptTest.tearDownClass.__func__)

    def test_native_recalculation_binds_priced_included_and_all_totals(self):
        original = self.data
        with tempfile.TemporaryDirectory(prefix='lo-values-') as temporary:
            directory = Path(temporary)
            source = directory / 'input' / 'quotation.xlsx'
            source.parent.mkdir()
            source.write_bytes(clear_formula_caches(original))
            output_dir = directory / 'converted'
            output_dir.mkdir()
            output = output_dir / source.name
            self.assertFalse(output.exists())
            receipt = subprocess.run([self.converter, '-env:UserInstallation=' + (directory / 'profile').resolve().as_uri(),
                                      '--headless', '--convert-to', 'xlsx', '--outdir', str(output_dir), str(source)],
                                     capture_output=True, text=True, timeout=60)
            converted = conversion_output(receipt, output)
            computed_amounts(converted, self.rows, self.payload['tax']['rate'])
            # Keep totals intact while poisoning only the converted priced row.
            priced_ref = next(ref for ref, (text, _) in workbook_cells(converted).items()
                              if ref.startswith('C') and text == self.rows[0]['description'])
            def corrupt_row(tree):
                cell = next(cell for cell in tree.findall('.//m:c', NS) if cell.get('r') == 'E' + priced_ref[1:])
                cell.find('m:v', NS).text = '76'
            with self.assertRaisesRegex(AssertionError, 'amount differs in its own row'):
                computed_amounts(rewrite_sheet(converted, corrupt_row), self.rows, self.payload['tax']['rate'])

            # Mutate the actual emitter in an isolated module, retaining its
            # correct tax cache. Only native recalculation reveals the bad tax.
            generator_path = fixture.ROOT / 'scripts/generate_quote.py'
            generator_source = generator_path.read_text(encoding='utf-8')
            expression = 'f"ROUND(E{total_row}*{tax_rate:.6f},2)"'
            self.assertEqual(generator_source.count(expression), 1)
            mutant = types.ModuleType('synthetic_tax_mutant')
            mutant.__file__ = str(generator_path)
            sys.modules[mutant.__name__] = mutant
            try:
                exec(compile(generator_source.replace(expression, 'f"ROUND(E{total_row}*0,2)"'),
                             str(generator_path), 'exec'), mutant.__dict__)
                poisoned = directory / 'poisoned.xlsx'
                mutant.write_quote_layout_xlsx(fixture.ROOT / 'templates/quote-layout/quotation-layout.xlsx',
                                               poisoned, self.payload, self.lines)
            finally:
                sys.modules.pop(mutant.__name__, None)
            # Cached values alone still look correct before conversion.
            computed_amounts(poisoned.read_bytes(), self.rows, self.payload['tax']['rate'])
            poisoned.write_bytes(clear_formula_caches(poisoned.read_bytes()))
            bad_receipt = subprocess.run([self.converter, '-env:UserInstallation=' + (directory / 'mutant-profile').resolve().as_uri(),
                                          '--headless', '--convert-to', 'xlsx', '--outdir', str(output_dir), str(poisoned)],
                                         capture_output=True, text=True, timeout=60)
            bad_values = conversion_output(bad_receipt, output_dir / poisoned.name)
            with self.assertRaisesRegex(AssertionError, 'amount differs in its own row'):
                computed_amounts(bad_values, self.rows, self.payload['tax']['rate'])
            self.assertEqual(self.data, original, 'Native conversion changed the master fixture')
