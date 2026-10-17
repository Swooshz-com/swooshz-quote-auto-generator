import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import sys
import zipfile
import xml.etree.ElementTree as ET

root = Path(sys.argv[1])
trace = json.loads((root / 'trace-screened.json').read_text())
result = json.loads((root / 'result.json').read_text())
commercial = json.loads((root / 'commercial-save-proof.json').read_text())
jobs = [job for job in trace['jobs'] if job['request']['type'] == 'generate']
assert len(jobs) == 1
job = jobs[0]
terminal = [p for p in job['polls'] if p['body']['status'] == 'completed'][-1]
assert terminal['statusCode'] == 200
body = terminal['body']
assert job['accepted']['generation_run_id'] == body['generation_run_id'] == body['result']['generation_run_id']
metadata = body['result']['quote_session']['exports']['xlsx']
assert metadata['exists'] and not metadata['stale'] and metadata['publication_id']
payload = job['request']['payload']
rows = payload['line_items']
assert len(rows) == 2
assert rows[0]['price_mode'] == 'Priced' and rows[1]['price_mode'] == 'Included'
catalog = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/quote-generator/pricing-references/synthetic-exhibition-fixture-pricing/pricing-catalog.json').read_text())
catalog_by_id = {item['id']: item for item in catalog['items']}
normalized = trace['normalizations'][0]['body']['line_items']
for row in rows:
    catalog_row = catalog_by_id[row['pricing_keyword']]
    matched = next(item for item in normalized if item['pricing_keyword'] == row['pricing_keyword'])
    assert matched['pricing_authority']['variant'] == 'catalog'
    assert matched['pricing_authority']['catalog_item_id'] == catalog_row['id']
    assert Decimal(str(matched['catalog_unit_price'])) == Decimal(str(catalog_row['sale_unit_price']))

current = root / 'downloads/alpha1-current.xlsx'
protected = root / 'downloads/alpha1-protected.xlsx'
data = current.read_bytes()
digest = hashlib.sha256(data).hexdigest()
assert data == protected.read_bytes()
assert digest == result['digest'] == metadata['sha256']
assert len(data) == result['size'] == metadata['size_bytes']
z = zipfile.ZipFile(current)
assert z.testzip() is None
ns = {
    'm': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'p': 'http://schemas.openxmlformats.org/package/2006/relationships',
}
shared = []
if 'xl/sharedStrings.xml' in z.namelist():
    shared = [''.join(item.itertext()) for item in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns)]
sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
cells = {}
for cell in sheet.findall('.//m:sheetData/m:row/m:c', ns):
    value_node = cell.find('m:v', ns)
    value = value_node.text if value_node is not None else None
    kind = cell.get('t', '')
    if kind == 's':
        value = shared[int(value)]
    elif kind == 'inlineStr':
        value = ''.join(cell.find('m:is', ns).itertext())
    cells[cell.get('r')] = {'value': value, 'type': kind, 'formula': cell.findtext('m:f', '', ns), 'style': int(cell.get('s', '0'))}

def find_description(description):
    coordinates = [coord for coord, cell in cells.items() if cell['value'] == description]
    assert len(coordinates) == 1 and coordinates[0].startswith('C')
    return int(coordinates[0][1:])

money = lambda number: Decimal(str(number)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
priced_index, included_index = [find_description(row['description']) for row in rows]
price = Decimal(str(catalog_by_id[rows[0]['pricing_keyword']]['sale_unit_price']))
amount = money(Decimal(str(rows[0]['quantity'])) * price)
assert cells[f'B{priced_index}']['value'] == f"{rows[0]['quantity']} {rows[0]['unit']}"
assert cells[f'B{included_index}']['value'] == f"{rows[1]['quantity']} {rows[1]['unit']}"
assert Decimal(cells[f'E{priced_index}']['value']) == amount
assert Decimal(cells[f'E{included_index}']['value']) == 0
assert cells[f'E{priced_index}']['type'] not in ('s', 'inlineStr', 'str', 'e')
assert cells[f'E{included_index}']['type'] not in ('s', 'inlineStr', 'str', 'e')
tax_rate = Decimal(str(payload['tax']['rate']))
tax = money(amount * tax_rate)
total = amount + tax
totals = {}
for label, expected in [('Total', amount), ('GST 9%', tax), ('Total including GST', total)]:
    label_cells = [coord for coord, cell in cells.items() if cell['value'] == label]
    assert len(label_cells) == 1 and label_cells[0].startswith('D')
    index = int(label_cells[0][1:])
    assert Decimal(cells[f'E{index}']['value']) == expected
    assert cells[f'F{index}']['value'] == 'SGD'
    totals[label] = {'row': index, 'value': str(expected), 'formula': cells[f'E{index}']['formula']}
subtotal_row = totals['Total']['row']
tax_row = totals['GST 9%']['row']
total_row = totals['Total including GST']['row']
assert tax_row == subtotal_row + 1 and total_row == tax_row + 1
subtotal_formula = re.fullmatch(r'SUM\(E(\d+):E(\d+)\)', cells[f'E{subtotal_row}']['formula'])
assert subtotal_formula
assert int(subtotal_formula[1]) <= priced_index < included_index <= int(subtotal_formula[2]) < subtotal_row
assert cells[f'E{tax_row}']['formula'] == f'ROUND(E{subtotal_row}*{tax_rate:.6f},2)'
assert cells[f'E{total_row}']['formula'] == f'SUM(E{subtotal_row}:E{tax_row})'
assert cells['A6']['value'] == payload['client']['name']
assert cells['A18']['value'] == payload['project']['title']
assert any(coord.startswith('B') and cell['value'] == payload['company']['name'] for coord, cell in cells.items())

styles = ET.fromstring(z.read('xl/styles.xml'))
xfs = styles.find('m:cellXfs', ns)
fonts = styles.find('m:fonts', ns)
for coordinate in ['A20', 'B20', 'C20', 'E20']:
    xf = xfs[cells[coordinate]['style']]
    assert fonts[int(xf.get('fontId', '0'))].find('m:b', ns) is not None
assert xfs[cells['B20']['style']].find('m:alignment', ns).get('horizontal') == 'center'
assert xfs[cells['E20']['style']].find('m:alignment', ns).get('horizontal') == 'right'
for index in [priced_index, included_index, subtotal_row, tax_row, total_row]:
    xf = xfs[cells[f'E{index}']['style']]
    assert xf.get('numFmtId') == '4'
    assert xf.find('m:alignment', ns).get('horizontal') == 'right'
cols = sheet.find('m:cols', ns)
assert any(int(col.get('min')) <= 2 <= int(col.get('max')) and Decimal(col.get('width')) == Decimal('10.5') for col in cols)
assert all(any(int(col.get('min')) <= index <= int(col.get('max')) and col.get('hidden') == '1' for col in cols) for index in (7, 8))

drawing = ET.fromstring(z.read('xl/drawings/drawing1.xml'))
pictures = [anchor for anchor in drawing if anchor.find('xdr:pic/xdr:nvPicPr/xdr:cNvPr', ns) is not None and anchor.find('xdr:pic/xdr:nvPicPr/xdr:cNvPr', ns).get('name') == 'Header Logo']
assert len(pictures) == 1
picture = pictures[0]
relationship_id = picture.find('xdr:pic/xdr:blipFill/a:blip', ns).get('{'+ns['r']+'}embed')
relationships = ET.fromstring(z.read('xl/drawings/_rels/drawing1.xml.rels'))
relationship = next(rel for rel in relationships if rel.get('Id') == relationship_id)
assert relationship.get('Target') == '../media/header_logo.png'
logo = z.read('xl/media/header_logo.png')
original_logo = next(file for file in commercial['filesProof'] if file['file_role'] == 'quote_company_logo')
assert len(logo) == original_logo['bytes'] and hashlib.sha256(logo).hexdigest() == original_logo['sha256']
shapes = [anchor for anchor in drawing if anchor.find('xdr:sp/xdr:txBody', ns) is not None]
header_shapes = [anchor for anchor in shapes if payload['company']['header_details'] in ''.join(anchor.find('xdr:sp/xdr:txBody', ns).itertext())]
assert len(header_shapes) == 1
header = header_shapes[0]
assert 'Project No: '+payload['project_number'] in ''.join(header.find('xdr:sp/xdr:txBody', ns).itertext())
assert header.find('xdr:sp/xdr:txBody/a:bodyPr', ns).get('anchor') == 't'
assert all(run.get('sz') == '900' for run in header.findall('xdr:sp/xdr:txBody/a:p/a:r/a:rPr', ns))
assert int(header.findtext('xdr:from/xdr:row', namespaces=ns)) > int(picture.findtext('xdr:to/xdr:row', namespaces=ns))
assert header.findtext('xdr:from/xdr:col', namespaces=ns) == picture.findtext('xdr:from/xdr:col', namespaces=ns) == '8'
workbook = ET.fromstring(z.read('xl/workbook.xml'))
print_area = workbook.find("m:definedNames/m:definedName[@name='_xlnm.Print_Area']", ns).text
last_used = max(int(re.search(r'\d+', coord)[0]) for coord, cell in cells.items() if cell['value'] is not None)
assert print_area == f'Quotation!$A$1:$I${last_used+1}'

out = root / 'offline-workbook-readability'
out.mkdir(exist_ok=False)
office_binary=shutil.which('libreoffice')
assert office_binary, 'Required LibreOffice runtime is unavailable'
office_profile=Path(tempfile.mkdtemp(prefix='alpha1-native-workbook-'))
office = subprocess.run([office_binary, '-env:UserInstallation='+office_profile.as_uri(), '--headless', '--convert-to', 'csv:Text - txt - csv (StarCalc):44,34,76,1', '--outdir', str(out), str(current)], capture_output=True, text=True, timeout=90)
(out / 'libreoffice.stdout').write_text(office.stdout)
(out / 'libreoffice.stderr').write_text(office.stderr)
assert office.returncode == 0, office.stderr
csv_file = out / 'alpha1-current.csv'
assert csv_file.is_file()
csv_rows = list(csv.reader(csv_file.read_text(encoding='utf-8-sig').splitlines()))
for index, description, expected_amount in [(priced_index, rows[0]['description'], amount), (included_index, rows[1]['description'], Decimal('0'))]:
    assert csv_rows[index-1][2] == description
    assert Decimal(csv_rows[index-1][4].replace(',', '')) == expected_amount
for label, info in totals.items():
    assert csv_rows[info['row']-1][3] == label
    assert Decimal(csv_rows[info['row']-1][4].replace(',', '')) == Decimal(info['value'])
assert hashlib.sha256(current.read_bytes()).hexdigest() == digest and protected.read_bytes() == data
proof = {
    'status': 'NATIVE_WORKBOOK_OFFLINE_VERIFICATION_PASS', 'application_imports': 0, 'application_or_provider_execution': False,
    'input_workbook_unchanged': True, 'ui_and_protected_bytes_equal': True, 'sha256': digest, 'bytes': len(data),
    'generation_job_id': job['accepted']['job_id'], 'generation_run_id': job['accepted']['generation_run_id'], 'protected_export_metadata': metadata,
    'catalog_rows_bound_to_description_quantity_amount': True, 'priced_row': priced_index, 'included_row': included_index,
    'priced_unit_price': str(price), 'priced_quantity': str(rows[0]['quantity']), 'included_amount': '0', 'totals': totals,
    'header_client_project_company_and_project_number': True, 'header_details_below_logo_9pt_top_aligned': True,
    'embedded_logo_matches_original_upload': original_logo, 'logo_relationship': relationship.attrib,
    'bold_headers_centered_quantity_right_aligned_money_two_decimals': True, 'internal_cost_columns_hidden': True,
    'print_area': print_area, 'last_used_row': last_used, 'libreoffice_native_open_and_csv_recalculation': 'PASS',
    'libreoffice_exit': office.returncode, 'csv_sha256': hashlib.sha256(csv_file.read_bytes()).hexdigest(),
}
with (out / 'native-workbook-proof.json').open('x') as stream:
    json.dump(proof, stream, indent=2); stream.write('\n')
print(json.dumps(proof))
