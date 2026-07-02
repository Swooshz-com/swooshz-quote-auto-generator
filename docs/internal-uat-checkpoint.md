# Internal UAT Checkpoint

## Status

Date: 2026-07-02

Status: Local UAT passed for the current Platform-to-KQAG local baseline.

This checkpoint records the local manual baseline after the recent KQAG fixes
through PR #82. It is a documentation-only UAT note, not a deployment approval
or production readiness sign-off.

## Scope

The passed local UAT scope covered Platform local login and launch into the
KQAG quote generation and dashboard flow.

Repos involved:

- KQAG: `Swooshz-com/koncept-quote-auto-generator`
- Platform: `Swooshz-com/swooshz-platform`

Current local URLs:

- Platform: `http://127.0.0.1:4317`
- KQAG: `http://127.0.0.1:8765`

Recent fixes included in this baseline:

- PR #75: dashboard quote duplication, Show name, Project number, quote
  commercial defaults, and same-workspace admin visibility.
- PR #76: quote commercial, FX, and XLSX follow-up fixes.
- PR #77: initial quote commercial touched/default guard.
- PR #79: robust quote commercial preservation through AI analysis and stale
  JS/static cache protection.
- PR #81: explicit zero-tax row in generated quote totals.
- PR #82: mobile UI polish for header order, Quote Basis legend, and Output row
  cards.

## Tested Flow

- Platform Google login.
- Launch KQAG from Platform.
- Seeded local reference fixture flow, now superseded by the PR #86 direction:
  real operator UAT should use workspace-shaped setup with private profile,
  pricing, layout, and booth/render image imports kept outside Git.
- Customer details.
- Quote commercial fields: Currency, Exchange rate, Tax, and Rate.
- AI analysis.
- Quote Basis review.
- Output review.
- Generate quote.
- XLSX download.
- Dashboard saved session.
- Duplicate Quote.
- Modify duplicated quote.
- Mobile header, Quote Basis legend, and Output cards.

## Accepted Behavior

- Quote commercial values survive AI analysis.
- FX applies to output totals and XLSX output.
- VAT/GST 0% still shows an explicit tax row.
- Duplicate Quote creates a new selected quote and does not mutate the original
  session.
- Mobile UAT is usable for the header, Quote Basis legend, and Output row cards.

## Known Future Work

- Shared hosted/internal deployment is not required yet.
- Proper hosted staging or self-hosting comes later when other staff need
  access from their own devices.
- Full multi-user/admin visibility UAT can be expanded later with seeded
  same-workspace users.
- Production backup, restore, monitoring, and operational runbooks are future
  deployment work.

## Safety Note

Do not include secrets, database URLs, OAuth values, tokens, cookies, callback
URLs with query parameters, customer data, generated quote contents, private
pricing/profile files, private local paths, or staff emails in docs, PR text,
issues, screenshots, logs, or chat.
