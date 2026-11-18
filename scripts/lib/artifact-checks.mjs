// Downloaded quote artifact checks shared by the alpha smoke and the rehearsal check.
// They read local files only and report error types, never file contents.
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const defaultPython = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

export function safeErrorText(value, exceptionTail = false) {
  const text = String(value?.message || value || "unknown error");
  if (exceptionTail) return text.trim().split(/\r?\n/).at(-1).slice(-400);
  return text.replace(/[\r\n]+/g, " ").slice(0, 500);
}

export function spawnCollect(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, ...options });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr?.on("data", (chunk) => { stderr += chunk.toString(); });
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
}

export async function validateXlsx(filePath, expectedStrings, expectedNumbers, includedDescription, { python = defaultPython, cwd = root } = {}) {
  const validation = `import json, math, sys, zipfile, xml.etree.ElementTree as ET
path, strings_json, numbers_json, included_description = sys.argv[1:5]
included_description = json.loads(included_description)
needles = json.loads(strings_json)
expected = [float(value) for value in json.loads(numbers_json)]
ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
with zipfile.ZipFile(path) as archive:
    bad = archive.testzip()
    if bad:
        raise SystemExit("corrupt XLSX member: " + bad)
    names = set(archive.namelist())
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise SystemExit("XLSX package structure is incomplete")
    shared = []
    if "xl/sharedStrings.xml" in names:
        tree = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(node.itertext()) for node in tree.findall("m:si", ns)]
    texts, numbers = [], []
    sheets = sorted(name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    if not sheets:
        raise SystemExit("XLSX contains no worksheets")
    for name in sheets:
        sheet = ET.fromstring(archive.read(name))
        for cell in sheet.findall(".//m:c", ns):
            kind = cell.attrib.get("t", "")
            value = cell.find("m:v", ns)
            if kind == "s" and value is not None:
                texts.append(shared[int(value.text or "0")])
            elif kind == "inlineStr":
                inline = cell.find("m:is", ns)
                if inline is not None:
                    texts.append("".join(inline.itertext()))
            elif kind in {"str", "e"} and value is not None:
                texts.append(value.text or "")
            elif value is not None:
                try:
                    numbers.append(float(value.text or "nan"))
                except ValueError:
                    pass
    included_row_has_zero = False
    for name in sheets:
        sheet = ET.fromstring(archive.read(name))
        for row in sheet.findall(".//m:sheetData/m:row", ns):
            row_text, row_numbers = [], []
            for cell in row.findall("m:c", ns):
                kind = cell.attrib.get("t", "")
                value = cell.find("m:v", ns)
                text = ""
                if kind == "s" and value is not None:
                    text = shared[int(value.text or "0")]
                elif kind == "inlineStr":
                    inline = cell.find("m:is", ns)
                    if inline is not None:
                        text = "".join(inline.itertext())
                elif kind in {"str", "e"} and value is not None:
                    text = value.text or ""
                if text:
                    row_text.append(text)
                elif value is not None:
                    try:
                        row_numbers.append(float(value.text or "nan"))
                    except ValueError:
                        pass
            if included_description is not None and included_description in row_text and any(math.isfinite(number) and abs(number) <= 0.011 for number in row_numbers):
                included_row_has_zero = True
    package_xml = "".join(archive.read(name).decode("utf-8", errors="replace") for name in names if name.startswith("xl/") and name.endswith(".xml"))
all_text = "\\n".join(texts) + package_xml
missing = [value for value in needles if value not in all_text]
if missing:
    relevant = [value for value in texts if any(token in value.casefold() for token in ("alpha", "included", "wall rail", "carpet tile"))]
    raise SystemExit("XLSX is missing expected quote text: " + json.dumps({"missing": missing, "relevant_cells": relevant}))
if included_description is not None and not included_row_has_zero:
    raise SystemExit("XLSX does not show the Included row at zero amount: " + included_description)
for value in expected:
    if not any(math.isfinite(actual) and abs(actual - value) <= 0.011 for actual in numbers):
        raise SystemExit("XLSX is missing expected current total/value: " + str(value))
print(json.dumps({"status": "valid", "sheets": len(sheets), "text_cells": len(texts), "numeric_cells": len(numbers)}))
`;
  const result = await spawnCollect(python, ["-c", validation, filePath, JSON.stringify(expectedStrings), JSON.stringify(expectedNumbers), JSON.stringify(includedDescription)], { cwd });
  assert(result.code === 0, `XLSX validation failed: ${safeErrorText(result.stderr || result.stdout)}`);
  return JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1));
}

export async function validatePdf(filePath, { python = defaultPython, cwd = root } = {}) {
  const bytes = await fs.readFile(filePath);
  assert(bytes.subarray(0, 5).toString("ascii") === "%PDF-", "Generated PDF has no PDF signature.");
  assert(bytes.includes(Buffer.from("%%EOF")), "Generated PDF has no EOF marker.");
  assert(bytes.length > 512, "Generated PDF is unexpectedly small.");
  const validation = `import json, sys
try:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(sys.argv[1])
    page_count = len(document)
    if page_count < 1:
        raise ValueError("PDF has no pages")
    bitmap = document[0].render(scale=1.0, rotation=0)
    if bitmap.width <= 0 or bitmap.height <= 0:
        raise ValueError("PDF first page did not render")
    print(json.dumps({"status": "valid", "pages": page_count, "rendered_first_page": True}))
except Exception as error:
    sys.stderr.write(type(error).__name__)
    raise SystemExit(1)
`;
  const result = await spawnCollect(python, ["-c", validation, filePath], { cwd });
  assert(result.code === 0, `PDF parsing/rendering failed: ${safeErrorText(result.stderr || result.stdout)}`);
  return { ...JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1)), bytes: bytes.length };
}

// Font names embedded in a PDF: every /BaseFont entry, read from plain and
// Flate-compressed objects. Subset prefixes (ABCDEF+) are removed.
export async function pdfFontNames(filePath, { python = defaultPython, cwd = root } = {}) {
  const script = `import json, re, sys, zlib
data = open(sys.argv[1], "rb").read()
chunks = [data]
for match in re.finditer(rb"stream\\r?\\n", data):
    start = match.end()
    stop = data.find(b"endstream", start)
    if stop < 0:
        continue
    try:
        chunks.append(zlib.decompress(data[start:stop]))
    except zlib.error:
        pass
names = set()
for chunk in chunks:
    for raw in re.findall(rb"/BaseFont\\s*/([A-Za-z0-9+#_.,-]+)", chunk):
        name = raw.decode("ascii", "replace")
        names.add(name.split("+", 1)[1] if re.match(r"^[A-Z]{6}\\+", name) else name)
print(json.dumps(sorted(names)))
`;
  const result = await spawnCollect(python, ["-c", script, filePath], { cwd });
  assert(result.code === 0, `PDF font listing failed: ${safeErrorText(result.stderr || result.stdout, true)}`);
  return JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1));
}

// The release image renders Calibri and Arial as Carlito and Liberation Sans.
// Any other family means a font fell back and the quote layout may shift.
export const EXPECTED_PDF_FONT_FAMILIES = ["Carlito", "LiberationSans"];

export function unexpectedPdfFonts(names) {
  return names.filter((name) => !EXPECTED_PDF_FONT_FAMILIES.some((family) => name === family || name.startsWith(family + "-") || name.startsWith(family + ",")));
}
