# Quote template definition (`sqag.quote-template.v1`)

Admins will build quote templates in Settings > Quote Templates (#167). A template
is a JSON definition that says where each part of a quotation sits on the Excel
grid and how it looks. The PDF is converted from the Excel file, so one template
drives both.

This page is the contract for the definition. `scripts/quote_template.py` reads
and checks it; nothing else may accept a definition without going through
`read_quote_template`. The built-in read-only **Standard** template is
`templates/quote-template/standard.json`; it mirrors today's bundled layout
(`templates/quote-layout/quotation-layout.xlsx`) and is the final fallback.

Profiles without a template keep using their own layout workbook exactly as
today. Storage, permissions, the editor and choosing a template for a profile
come in later slices.

## Reading rules

- **Default-deny.** Every object lists its keys below. An unknown key, a missing
  required key, a wrong type or an out-of-range value is refused with
  `QuoteTemplateError`, whose `path` names the place (for example
  `blocks[4].columns[1].key`).
- **JSON text:** UTF-8 only, at most 64 KiB, no byte order mark, no duplicate
  keys, no `NaN` or `Infinity`, no nesting deep enough to exhaust the parser.
  An already-parsed object must hold only JSON values and obeys the same size cap.
- **Numbers:** whole numbers must be JSON integers (`true` and `61.0` are refused);
  every number must be finite and inside its range.
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
| `header` | object | `rows` 1 to 40 (rows reserved at the top of page 1 for header blocks; must be fewer than `page.first_page_rows`), `repeat_rows` 0 to `rows` (rows reprinted at the top of every page) |
| `grid` | object | `columns`: 4 to 12 Excel columns, each `{width: 4 to 60, hidden?: bool}`; at least one visible |
| `theme` | object | `font` (`Calibri` or `Arial`, the fonts installed for PDF rendering), `size_pt` 7 to 20, `color` and `heading_color` as `#RRGGBB` |
| `labels` | object | `total` (1 to 40), `attention` (0 to 40), `project_number` (0 to 40). Tax and currency labels come from the quote itself. |
| `blocks` | list | 2 to 30 blocks, below |

### `page`

`size` is `A4`; `orientation` is `portrait` or `landscape`. `margins_mm` holds
`left`, `right`, `top`, `bottom` (5 to 40) and `header`, `footer` (0 to 20, not
larger than the top or bottom margin). `row_height_pt` is 12 to 40.
`first_page_rows` and `page_rows` (20 to 150) are the printable rows on page 1
and on each later page; `continuation_top_rows` (0 to 10, default 2) is the gap
above the repeated item-table header on later pages.

## Blocks

Every block has `type`, `area` (`header` or `body`), `col` and `span` (grid
columns, inside the grid) and an optional `style` (`font`, `size_pt`, `bold`,
`italic`, `color`, `align`: `left`, `center` or `right`).

- **Header blocks** also have `row` and `rows` inside `header.rows`. Header
  blocks must not overlap.
- **Body blocks** flow down the page in list order. Optional `gap_before`
  (0 to 10 blank rows, default 1) and `beside` (default false): a block with
  `beside: true` shares the previous block's rows and must not overlap its
  columns. The item table and totals always stand on their own rows.

| Type | Area | Extra keys | Count |
| --- | --- | --- | --- |
| `logo` | header | `logo`: `width_mm` 5 to 120, `height_mm` 5 to 80, `align`, `valign` (`top`, `middle`, `bottom`), `keep_aspect` | at most 1 |
| `company_details` | header | `fields` from `header_lines`, `project_number`, `blank` | at most 1 |
| `client_details` | header | `fields` from `client_name`, `client_address`, `attention`, `client_title`, `blank`; `address_rows` 1 to 6; `indent_cols` 0 to span-1 | at most 1 |
| `quote_details` | header | `fields` from `quote_date`, `project_title`, `project_number`, `blank` | at most 1 |
| `item_table` | body | `currency_row` (bool); `columns`: 2 to 8 of `{key, label (0 to 40), col, span, align}` | exactly 1 |
| `totals` | body | `label_col` and `currency_col` (0 for none) inside the block | exactly 1 |
| `terms`, `notes` | body | none; span at least 2 (number column and text) | at most 1 each |
| `signature`, `acceptance` | body | none | at most 1 each |
| `free_text` | either | `heading` (0 to 80), `lines` (0 to 20 lines of up to 200); needs some text | at most 10 |

`fields` lists 1 to 8 entries, each named once (`blank` may repeat), and at
least one entry other than `blank`.

Item-table column keys: `position`, `quantity`, `unit`, `description`,
`unit_price`, `amount`, each at most once; `description` and `amount` are
required. Columns sit inside the table block, must not overlap, and must not
start on a hidden grid column.

Totals rules: the totals block comes after the item table, covers the item
table's `amount` column (totals amounts are written there), and keeps its label
and currency on their own visible columns. Prices, quantities, tax and totals are
calculated exactly as for every other quote; a template only places them.

## Standard template

`templates/quote-template/standard.json` reproduces the bundled layout: the nine
columns A to I with G and H hidden, the same margins, 18.7 pt rows, 55 rows on
page 1 and 61 on later pages, the logo and company details on the right,
client details and quote details on the left, the `Pos.`, `Quantity`,
`Service`, `Estimate` table, totals in D to F, the red exclusions text, terms,
notes, and the signature and acceptance side by side. `tests/test_quote_template_schema.py`
checks it against the bundled workbook so the two cannot drift apart.
