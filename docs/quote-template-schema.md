# Quote template definition (`sqag.quote-template.v1`)

Admins build quote templates in Settings > Quote Templates. A template
is a JSON definition that says where each part of a quotation sits on the Excel
grid and how it looks. The PDF is converted from the Excel file, so one template
drives both.

This page is the contract for the definition. `scripts/quote_template.py` reads
and checks it; nothing else may accept a definition without going through
`read_quote_template`. The built-in read-only **Standard** template is
`templates/quote-template/standard.json`; it mirrors today's bundled layout
(`templates/quote-layout/quotation-layout.xlsx`) and is the final fallback.

Profiles without a template keep using their own layout workbook exactly as
today. Admins build templates in Settings > Quote Templates, and the Company
Profile "Quote template" dropdown picks one for a profile. A profile with a
layout workbook of its own can be switched back to it from the same dropdown;
its new quotes are then laid out exactly as before any template was picked. A
profile created without a workbook has no layout of its own to go back to.

`scripts/generate_quote.py` renders a quote with a template through
`write_template_quote_xlsx`, or from the command line with
`--quote-template PATH`. It builds a new workbook with the standard library
only and writes all text as inline strings.

## Reading rules

- **Default-deny.** Every object lists its keys below. An unknown key, a missing
  required key, a wrong type or an out-of-range value is refused with
  `QuoteTemplateError`, whose `path` names the place (for example
  `blocks[4].columns[1].key`).
- **JSON text:** UTF-8 only, at most 64 KiB, no byte order mark, no duplicate
  keys, no `NaN` or `Infinity`, no nesting deep enough to exhaust the parser.
  An already-parsed object must hold only JSON values and obeys the same size cap.
- **Numbers:** whole numbers must be JSON integers (`true` and `61.0` are refused);
  every number must be finite and inside its range. Number text longer than 32
  characters is refused, so huge numbers fail with a path, not a crash.
- **Text:** trimmed and NFC-normalised. Any control, format (including bidi
  overrides and zero-width characters), surrogate, private-use, unassigned,
  line-separator or paragraph-separator character is refused. Text that starts
  with `=`, `+`, `-` or `@` is kept as plain text with a leading apostrophe
  (the same rule as profile text). Any renderer must also write template text
  as inline strings, so template text never becomes a live Excel or CSV formula.
- **Normalised output:** `read_quote_template` returns the definition with every
  optional key filled in. `quote_template_fingerprint` is the SHA-256 of its
  canonical JSON (sorted keys, no spaces), so key order and spelled-out defaults
  do not change it; each quote will record the template name, save number and
  this fingerprint.

## Top level

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | text | exactly `sqag.quote-template.v1` |
| `name` | text | 1 to 60 characters |
| `page` | object | below |
| `header` | object | `rows` 1 to 40: rows reserved at the top of page 1 for header blocks; must be fewer than `page.first_page_rows` |
| `grid` | object | `columns`: 4 to 12 Excel columns, each `{width: 4 to 60, hidden?: bool}`; at least one visible |
| `theme` | object | `font` (`Calibri` or `Arial`, the fonts installed for PDF rendering), `size_pt` 7 to 20 in half-point steps (such as 11 or 11.5), `color` and `heading_color` as `#RRGGBB` |
| `labels` | object | `total` (1 to 40), `attention` (0 to 40), `project_number` (0 to 40). Tax and currency labels come from the quote itself. |
| `blocks` | list | 2 to 30 blocks, below |

### `page`

`size` is `A4`; `orientation` is `portrait` or `landscape`. `margins_mm` holds
`left`, `right`, `top`, `bottom` (5 to 40) and `header`, `footer` (0 to 20, not
larger than the top or bottom margin). `row_height_pt` is 12 to 40.
`first_page_rows` and `page_rows` (20 to 150) are the printable rows on page 1
and on each later page; `continuation_top_rows` (0 to 10, default 2) is the gap
above the repeated item-table header on later pages.

The reader refuses a layout whose A4 pages cannot hold their rows in Excel or
LibreOffice (paths `page.first_page_rows`, `page.page_rows`). This is a model
with a measured margin, not an exact copy of Excel. The margin was measured in
Excel for Windows at 192 dpi (200 % display scaling) printing to Microsoft Print
to PDF; 96 dpi was measured only under the earlier rule (one row more a page),
on 15 to 20 pt rows. The sheet prints fit to one page wide: the scale is the
printable width over the visible columns' width (at most 1). Excel prints rows
taller than their set height, in proportion to it and by an amount that depends
on the theme font and the display dpi (at 192 dpi up to 4.8 %: Calibri 7.5;
28.2 pt Calibri 8 rows land 29.06 pt apart), so each row is counted at
`row_height_pt` plus 0.75 pt, times that scale, and every page keeps one row
spare. At 100 % print scale these allow rows at least 6.4 % taller than set at
any allowed row height and margin. Below 100 % one spare row is a smaller share
of the page (2.5 % at the 13 % floor); there Excel's print zoom comes out below
the modelled scale, and every layout measured at 192 dpi kept 1 to 18 rows spare.
Printable height is A4 height less the top and bottom margins, and the paper is
landscape-swapped when `orientation` is `landscape`. Excel rounds its zoom down
to a whole percent, which only shortens rows. It cannot print below 10 %, and its
digit can be a fifth wider than the reader's unit, so columns that would need
less than 13 % are refused at `grid.columns`. A width unit is the widest digit
of the sheet's Normal font, which the renderer sets to `theme.font` at
`theme.size_pt`, so a smaller or narrower font holds fewer rows
(`width_unit_points` in `scripts/quote_template.py`). The digit is 1038/2048 em
in Calibri (Carlito) and 1139/2048 em in Arial (Liberation Sans). LibreOffice
uses that exact width; Excel uses the digit hinted at a whole-pixel font size on
its display, which the reader approximates by the smallest of the exact width
and its whole-pixel width at each Windows display scaling from 96 to 336 dpi.
Standard (Calibri 13: 6.59 pt exact, 15 px at 168 dpi = 6.43 pt) holds 61 rows
of 18.7 pt, its own page length; at Calibri 11 (7 px at 96 dpi, 5.25 pt) it
would hold 49. The CI PDF smoke prints Standard, a custom layout, Standard at
Calibri 9 at its 42-row limit and Standard at Arial 16 at its 85-row limit (the
one whole size whose unit is the exact width LibreOffice prints with), and checks
the pages are A4. A printer that maps
A4 sheets onto Letter paper, Excel for Mac and other Windows printer drivers are
not covered. Rows left for items: at least 20 on the first page after the header
and table header (`header.rows`), and at least 20 on a later page after the
continuation rows and table header (`page.continuation_top_rows`). The
description column must span at least 30 visible width units (`blocks[i].columns[j]`).
A quantity column must hold three digits at the item table's font size
(`blocks[i].columns[j]`); the renderer measures each quote's quantities the same
way (`cell_text_width_points`) and refuses a longer one when the quote is made.
A line item taller than a later page is refused at render time with a
`QuoteTemplateError` at `page.page_rows`.

Item rows may align differently from the heading and section rows: an item-table
column's optional `item_align` (`left`, `center` or `right`, default `align`)
sets its item cells. Standard uses it to centre item numbers while section numbers
stay left.

A `company_details` block may take an optional `box`: `left_mm`, `top_mm` and
`right_mm` (0 to 20, default 0) move the company details text box in from the
block's top-left corner and stop it short of the block's right edge. The reader
refuses a box whose left and right margins fill the block's visible width, or
whose top margin reaches its last row (`blocks[i].box`).

Pictures and text boxes are placed by `drawing_width_emu`, which the reader and
the renderer share: each visible column counts the smaller of its Arial 10
placement width (7 pixels a unit plus 5) and 0.92 of its width in the theme
font's unit, in whole 96-dpi pixels. Excel's hinted digit is at least 0.926 of the
unit for both fonts at every half-point size from 7 to 20 and every Windows
display scaling, so a drawing placed inside that width stays inside its columns
in Excel. That margin is measured at half-point sizes only; between them the
hinted digit can be narrower (0.907 of the unit at Calibri 11.6 at 96 dpi, where
a logo would run 1.5 pt past its column), which is why font sizes must be whole
or half points. A logo's `width_mm` must fit its block's width
(`blocks[i].logo.width_mm`), or Excel would cut it at the print area; its
`height_mm` may run below the block's rows. Standard's 47.8 mm logo fits column I
at Calibri 13 (193 px, 51.1 mm) and needs a narrower logo or column at smaller
theme fonts (at most 39.1 mm at Calibri 9).

## Blocks

Every block has `type`, `area` (`header` or `body`), `col` and `span` (grid
columns, inside the grid) and an optional `style` (`font`, `size_pt` 7 to 20
in half-point steps, `bold`, `italic`, `color`, `align`: `left`, `center` or
`right`).

- **Header blocks** also have `row` and `rows` inside `header.rows`. Header
  blocks must not overlap.
- **Body blocks** flow down the page in list order. Optional `gap_before`
  (0 to 10 blank rows, default 1) and `beside` (default false): a block with
  `beside: true` shares the previous block's rows and must not overlap its
  columns. The item table and totals always stand on their own rows. A block
  with nothing to show (a quote without notes) takes no rows and its gap is
  skipped. Blocks that do not fit on a page move to the next one, below the
  repeated item-table header.

| Type | Area | Extra keys | Count |
| --- | --- | --- | --- |
| `logo` | header | `logo`: `width_mm` 5 to 120 and no wider than the block, `height_mm` 5 to 80 (the picture's box, which may extend below the block's rows), `align`, `valign` (`top`, `middle`, `bottom`: place the box inside the block's cells), `keep_aspect`, `repeat` (also show it at the same place on every later page) | at most 1 |
| `company_details` | header | `fields` from `header_lines`, `project_number`, `blank` | at most 1 |
| `client_details` | header | `fields` from `client_name`, `client_address`, `attention`, `client_title`, `blank`; `address_rows` 1 to 6; `indent_cols` 0 to span-1 | at most 1 |
| `quote_details` | header | `fields` from `quote_date`, `project_title`, `project_number`, `blank` | at most 1 |
| `item_table` | body | `currency_row` (bool); `columns`: 2 to 8 of `{key, label (0 to 40), col, span, align}` | exactly 1 |
| `totals` | body | `label_col` and `currency_col` (0 for none) inside the block | exactly 1 |
| `terms`, `notes` | body | none; span at least 2 (number column and text) | at most 1 each |
| `signature`, `acceptance` | body | `line_chars` 5 to 60: length of the signing line | at most 1 each |
| `free_text` | either | `heading` (0 to 80), `lines` (0 to 20 lines of up to 200); needs some text | at most 10 |

`fields` lists 1 to 8 entries, each named once (`blank` may repeat), and at
least one entry other than `blank`. Client and quote details are written in
cells, one row per field (the address takes `address_rows`, attention takes a
label row and a name row), so the block's `rows` must fit them.

Item-table column keys: `position`, `quantity`, `unit`, `description`,
`unit_price`, `amount`, each at most once; `description` and `amount` are
required. Columns sit inside the table block, must not overlap, and must not
start on a hidden grid column.

Every block must cover at least one visible grid column.

Totals rules: the totals block comes after the item table, covers the item
table's `amount` column (totals amounts are written there), and keeps its label
(and currency, if any) on separate visible columns, neither of them a cell of
the amount column (including the second cell of a two-column amount). Prices,
quantities, tax and totals are calculated exactly as for every other quote; a template only places them.

## Standard template

`templates/quote-template/standard.json` reproduces the bundled layout: the nine
columns A to I with G and H hidden, the same margins, 18.7 pt rows, 55 rows on
page 1 and 61 on later pages, the logo (repeated on later pages) and company details on the right,
client details and quote details on the left, the `Pos.`, `Quantity`,
`Service`, `Estimate` table, totals in D to F, the red exclusions text, terms,
notes, and the signature and acceptance side by side. `tests/test_quote_template_schema.py`
checks it against the bundled workbook so the two cannot drift apart.
`tests/test_quote_template_render.py` compares the rendered Standard quote with
the bundled one: cell text, alignment, row heights and the text box.

One difference is accepted. The bundled workbook sets 18.7 pt only on rows 1 to
89; its later rows keep stray heights (the sheet default 17 pt, and 17.35 pt and
17.7 pt on a few rows) that no layout asks for. Standard keeps 18.7 pt on every
row, so on quotes longer than 89 rows the later pages have a slightly taller row
pitch. Matching the stray heights would need per-row data, and it is recorded
here until an owner decides otherwise.
