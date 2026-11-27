# Release UAT checklist

Every agent UAT before a live deploy runs every line of this checklist on the
copy of live (release runbook [step 8.2](internal-uat-coolify-deploy.md)).
The owner's own UAT is then only the lines marked for a human eye, any line
the agent could not run, and a fix-before-live-or-later decision on each
failure that does not block (see Results). The list covers the app as it is
on `main`: every screen, button, menu, setting and message staff use, in the
order they use them, with the expected result.

When a change adds, removes or renames a control or a message, the same change
updates its line here. CI checks the controls: the Release UAT checklist
controls job (`scripts/check_uat_checklist_controls.py`) fails when a button,
link, field or editable area of the app has no line naming it in `Controls`,
or a line names one the app no longer has. Name a control by its most specific
name: its id (`#id`, or `#prefix-*` for an id built at run time), else its
action attribute with its value (`[data-x-action="value"]`), else another data
attribute, link path, form or class; a shared name it merely carries does not
count. Controls the check cannot name (made with `document.createElement`, with
attributes built by string joining, or with no id, data attribute or own class)
are listed by kind, with their exact count and a reason, in
[internal-uat-controls-allowlist.json](internal-uat-controls-allowlist.json):
a new one of any kind fails until it gets a name or the count is raised. Other
deliberate exceptions go there too, each with its reason. Messages and the
testing plan are not checked: keep quoted messages current by hand.

## How to run it

- **Where:** on the copy of live, signed in as the runbook says, after Pass A
  passes (runbook step 8.1). Lines that need a fresh workspace or a second
  sign-in (staff role, another browser) may also run on a local synthetic
  server; say which in the result.
- **Order:** top to bottom. Earlier sections make the quotes, profiles and
  references that later sections use.
- **Role:** `A` admin, `S` staff (the operator role), `B` both. A `B` line runs
  once per role. Staff lines need a staff sign-in; where the copy has none,
  run them on a local server in staff password mode
  ([internal-password-auth-mode.md](internal-password-auth-mode.md)) or
  record them NOT RUN with the reason. That server needs database storage
  for QC-02 to QC-04: with local file storage a password-mode session lists
  no saved profiles and cannot save, delete or export them, so those lines
  are N/A there.
- **Width:** `D` desktop (1440 px wide), `M` 375 px wide, `DM` both. The agent
  checks `M` in a resized browser window: nothing cut off, no sideways page
  scroll, nothing overlapping, every control reachable and usable.
- **Eye:** `H` marks a line that needs a human eye: how it looks and feels,
  the file in desktop Excel, or a real phone. The agent still runs its
  working part and records what it saw; the owner judges the rest.
- **AI:** lines marked `$` make paid AI calls. Run them under the release's
  AI ledger and its recorded cap; High Quality also needs the owner's OK on
  its cost first. A paid line not run for cost is NOT RUN, with the reason.

## Data and privacy

- Make every quote, profile, template and pricing reference for this run with
  synthetic names, for example `UAT <date> Quote A`, `UAT Profile`,
  `UAT Template`, `UAT Catalogue`. Synthetic images, PDFs and CSV files only.
- The copy holds real customer quotes. Open them only where a line says so
  (reopen, view only, downloads). Never change a quote the run did not make,
  except where a line asks for it on a quote the login owns.
- Evidence holds ids, counts, hashes, sizes, timings and screenshots of
  synthetic quotes only. Never copy customer names, prices, files or quote
  text into evidence, chat or issues.
- Do not commit, paste into GitHub, or include in PR evidence: real customer
  data, real company or bank details, real quote or profile exports, files
  with `logo_data_url` or embedded Base64 logos, runtime quote-session
  folders, private pricing or profile imports, or any XLSX or PDF from a real
  quote.

## Results

Record one row per line in the run's evidence:

| Line | Role | Width | Result | Saw (ids, counts, short text) |
| --- | --- | --- | --- | --- |
| DB-01 | A | D | PASS | 12 rows, counts 12 / 7 / 7 |

- **Result:** `PASS`, `KNOWN, PLANNED` (see below), `FAIL` (what was seen
  instead, and how to repeat it), `NOT RUN` (why), or `N/A` (the control is
  not shown in this setup, and why).
- **Before marking a line `FAIL`,** check whether a planned change already
  covers it: the open tasks under the programme's parent issue (named in
  `.github/toolkit-programme.json`), their task pages and recorded decisions,
  and their G0 plans in the evidence store. If one does and the failure is
  not a safety or pricing failure (next bullet), the result is
  `KNOWN, PLANNED`, naming the task and slice (for example "task <number>,
  slice R2"). It does not block the release and stays off the owner's list.
- **A safety or pricing failure** is never settled by a plan alone. It is
  one where customer output shows internal cost, markup, GST workings or
  supplier notes; a price, quantity or total is wrong; someone sees or
  changes a quote, profile or setting their role should not reach; text
  becomes a live Excel formula; or saved data is lost or silently dropped.
  The lines that check these are SI-08, DB-02, DB-15, DB-17, DB-18, QC-04,
  QB-06, QB-09, OUT-03 to OUT-10, XP-03, PR-02, PR-08, QT-07, RO-04, RO-05,
  ER-03 and RU-01 to RU-08. When a plan covers one, the result is `FAIL`
  with that task and slice named, and it goes on the owner's list; only the
  owner may defer it.
- **A `FAIL` blocks the release** when no plan covers it and it is either new
  in this release or a safety or pricing failure. It goes back to the
  builder before the owner is asked.
- **Any other `FAIL`** is recorded on its area's open task (or a new task, if
  none fits).
- **The owner's list** is every `H` line and every `NOT RUN` line, each in
  one plain sentence with where to click, then every `FAIL` that does not
  block (planned safety or pricing failures included, with their task and
  slice), each with a fix-before-live-or-later decision for the owner.
  Nothing else goes to the owner.

## Lines

In each table, `Who` is the role, `Width` the widths and `Eye` the human-eye
mark; `Controls` names the ids or attributes of the controls the line uses.

### 1. Sign-in, top bar and sign-out

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| SI-01 | Open the app's address while signed out. | "Staff sign in": "Enter your approved staff email and the password for your role." with Email, Password, Sign in, Terms and Privacy Notice. It lists no allowed emails. | B | DM | | `/login` |
| SI-02 | Sign in with a wrong password, then with an email that is not allowed. | The same page both times: "Could not sign in", "Try again. If this keeps happening, contact support with reference ..." and Try again back to the form. | B | D | | `/login` |
| SI-03 | Sign in with the right email and password. | The Dashboard opens. The top bar shows "Signed in as <email> (Admin)", or "(Operator)" for staff, and Logout. | B | DM | | `#topbarAuthState`, `#topbarLogoutLink` |
| SI-04 | Refresh the browser on any screen. | Still signed in, on the same screen. | B | D | | |
| SI-05 | Use the top bar: the Swooshz Quote Generator name, Dashboard, New Quote, Settings. | The name and Dashboard open the Dashboard (Dashboard shows only inside a quote). New Quote starts an empty quote at Upload. Settings opens Settings for an admin; for a staff member it is off and says "Only admins can open Settings." | B | DM | | `#topbarBrandButton`, `#backToDashboardButton`, `#newQuoteButton`, `#settingsButton` |
| SI-06 | Open Terms and Privacy Notice in the top bar. | Each opens in a new tab and shows its page. | B | DM | | `/terms`, `/privacy` |
| SI-07 | Press Logout, then open the app's address again. | "Signed out": "Your session has ended. Sign in again to open the quote generator." with Sign in again. The app's address shows the sign-in form until a new sign-in. | B | DM | | `#topbarLogoutLink`, `/logout`, `/signed-out` |
| SI-08 | Sign in with the same login in a second browser window and log out there. In the first window, change a field on a run quote, then go to the Dashboard and press Delete session on another run quote. | The first window goes to the sign-in form, which says "Your session has ended. Sign in again to continue." After signing in, the Dashboard opens, both quotes are still there and the changed field was saved. | B | D | | `/login` |

### 2. Dashboard

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| DB-01 | Open the Dashboard. | "Loading sessions..." then "Showing 1-N of M sessions." The Total sessions, Generated and Exports ready counts match the rows. Record how long the list took. | B | DM | | `#dashboardSessionCount`, `#dashboardTotalSessions`, `#dashboardGeneratedSessions`, `#dashboardExportedSessions` |
| DB-02 | Compare the rows each role sees. | A staff member sees only their own quotes. An admin sees every quote in the workspace, and a selected quote says who made it ("Created by", or Unknown). | B | D | | `#dashboardSessionsList` |
| DB-03 | Search for a run quote's synthetic customer name, then for text no quote has, then clear the box. | Only matching rows and "Showing 1-N of N matching sessions."; then "No matching sessions."; then every row again. | B | DM | | `#dashboardSearchInput` |
| DB-04 | Sort by Date Created, then Modified Date. | The order follows the chosen date, newest first. | B | D | | `#dashboardSortSelect` |
| DB-05 | Date range: Today, Last 7 days, Last 30 days, All dates, then Custom range with From and To. | Only quotes in the range. Custom range shows From, To and the "Date filter" summary. | B | DM | | `#dashboardDateFilter`, `#dashboardDateStartInput`, `#dashboardDateEndInput`, `#dashboardDateFilterSummary` |
| DB-06 | Status: All, Draft, Draft Modified, Generated. | Only quotes with that status. | B | D | | `#dashboardStatusFilter` |
| DB-07 | With more quotes than one page: Show 5, 10, 20 and All sessions; pick each Range. | The rows and the "Showing" line follow the page size and range. | B | DM | | `#dashboardPageSizeSelect`, `#dashboardRangeSelect` |
| DB-08 | Click a run quote's row; then use Enter, Space, the arrow keys and Delete on the list. | Each row shows customer, project, Show name, Project number, Ref, Created, Total, Quote Company, Modified, Subtotal and its status, and no AI spend. The keys select, open (Enter on the selected row), move and ask to delete. The Selected session panel shows customer, project, Created and Modified, Created by, status, Ref, Grand Total, Subtotal, Currency / FX, Pricing Reference, Tax / Rate, Show Name, Project Number, Quote Company, the XLSX and PDF links, Duplicate Quote, Modify quote, Delete session and Clear selection. The figures match the quote's Output. | B | DM | | `[data-quote-session-id]`, `#dashboardSelectedSessionPanel` |
| DB-09 | The XLSX and PDF links of a quote with current files, one with stale files and one with none. | Current: a Download link; hovering says "XLSX ready" (or "PDF ready") and the file downloads. Stale with the old file kept: the link reads "XLSX (stale)" and hovering says "XLSX stale - needs regeneration". Stale with no file, or none: plain "XLSX" or "PDF", not a link; hovering says why ("XLSX needs regeneration", "PDF unavailable" or "Missing PDF"). | B | D | | `.dashboard-export-link` |
| DB-10 | Modify quote. | "Restoring workspace" shows, then the quote opens at the step it was saved at, with its inputs, rows and totals. | B | DM | | `[data-dashboard-panel-action="modify-session"]`, `#dashboardLoadingModal` |
| DB-11 | Duplicate Quote on a run quote (one with Excel), then open the copy; Download Excel on it. Also Duplicate a quote left at Quote Basis and open that copy. | The copy of the run quote opens at Output with the same customer, company, Quote Basis decisions and prices, and no Excel or PDF until you press Generate; then its Excel builds. The copy of the Quote Basis quote opens at Quote Basis with the same decisions. The originals are unchanged and keep their files; all show on the Dashboard. | B | D | | `[data-dashboard-panel-action="duplicate-session"]` |
| DB-12 | Delete session on a run quote: Cancel; then again, Delete session. | "Delete quote session?" says "This deletes this quote session and its saved exports from the workspace. This cannot be undone." Cancel keeps the quote. Delete session removes the row, and its files no longer download. | B | DM | | `[data-dashboard-panel-action="delete-session"]`, `#cancelQuoteSessionDeleteButton`, `#confirmQuoteSessionDeleteButton` |
| DB-13 | Select; tick two run quotes; Select all visible; untick back to the two; Delete selected. | Bulk selection says "2 quote sessions selected" and lists them, each with an x that takes it off the selection. Select all visible ticks every shown row. Delete selected asks once, then removes both. | B | D | | `#dashboardSelectModeButton`, `[data-dashboard-select]`, `[data-dashboard-panel-action="delete-selected"]`, `[data-dashboard-remove-selected]` |
| DB-14 | Clear selection, and the panel's close button. | The panel goes back to "No session selected". | B | D | | `[data-dashboard-panel-action="clear-selection"]` |
| DB-15 | Open a quote another staff member made (Modify quote). | The note "View only: this quote was made by another staff member. You can read every step and download its files, but not change it." Every step with content opens; existing Excel and PDF files download, and where a file is missing, hovering Download Excel or View PDF says "Only an existing file can be opened in a view-only quote."; no field, tick or button changes anything and no save is sent; nothing new is generated. | B | DM | | `#sideNextButton`, `#sideDownloadButton`, `#sideViewPdfButton` |
| DB-16 | Sign in with a login that has no quotes (local server). | "No quote sessions yet" with a New Quote button that starts a quote. | S | DM | | `#dashboardEmptyState`, `#dashboardEmptyNewQuoteButton` |
| DB-17 | As admin, with other staff members' quotes in the list: Select, then Select all visible. Do not press Delete selected. | Every shown row is ticked, other staff members' quotes included, and the panel lists every ticked quote before Delete selected would ask. Clear selection unticks them all. | A | D | | `#dashboardSelectModeButton`, `[data-dashboard-select]`, `[data-dashboard-panel-action="clear-selection"]` |
| DB-18 | As admin, select a quote another staff member made and press Duplicate Quote in the Selected session panel. | The original is unchanged and still opens view only (DB-15). Duplicate Quote makes a new quote of your own with the same inputs that you can change; it opens at Customer until you press Next there, as the Customer step was confirmed by the other staff member. | A | D | | `[data-dashboard-panel-action="duplicate-session"]` |

### 3. New quote: Upload

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| UP-01 | New Quote. | Upload opens empty. The rail shows Upload, Customer, Quote Company, Quote Basis and Output; the later steps are locked and say "Add reference files before opening this step." on hover. | B | DM | | `#newQuoteButton`, `button[data-side-panel]`, `#customerDetailsButton`, `#quoteCompanyButton`, `#quoteBasisButton`, `#outputButton` |
| UP-02 | Choose Files with a JPG, a PNG, a WebP and a PDF. | A status says they were added; each is listed with a remove button. | B | DM | | `#imageInput`, `#fileList` |
| UP-03 | Drag and drop two images onto the drop area. | Both are added. | B | D | | `#imageInput` |
| UP-04 | Remove one file. | It leaves the list; the others stay. | B | DM | | `[data-remove-image]` |
| UP-05 | Add a 12 MP photo and three photos between 6 and 16 MiB. | All accepted. | B | D | | `#imageInput` |
| UP-06 | Try a 9th file, a file over 48 MiB, files over 128 MiB in total, a GIF or TXT file, the same file twice, and PDFs over 96 pages in total. | "Maximum 8 reference files reached. Remove one before adding more.", "Choose files up to 48 MiB each.", "Choose up to 128 MiB of reference files in total.", "Add JPG, PNG, WebP or PDF files only." (by picker or drag and drop) and "1 duplicate file skipped."; PDFs over 96 pages are refused when analysis starts, naming the limit. The files already listed stay. | B | D | | `#imageUploadStatus` |
| UP-07 | Type in Known details, including more than 2,000 characters. | The text stays; it stops at 2,000 characters. | B | DM | | `#knownDetails` |
| UP-08 | Reset Draft on Upload, then Cancel; again, then Reset Draft in the dialog. | It asks "Reset this step? Your changes on this step will be lost." Cancel (or Escape, or a click beside the dialog) changes nothing. Reset Draft in the dialog clears the files and the later steps lock again. | B | D | | `#resetImagesButton`, `#resetDraftConfirmModal`, `#cancelResetDraftButton`, `#confirmResetDraftButton` |
| UP-09 | Next: Customer; Back on each later step. | Next moves one step on, Back one step back; Back is off on Upload. | B | DM | | `#sideNextButton`, `#sideBackButton` |

### 4. Customer

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| CU-01 | Open Quote Pricing Reference; pick one. | It lists the workspace's pricing references; picking says "Selected. Continue to apply this pricing reference." and Reference basis shows its currency and tax. With no references at all (local server) it is off and says "No pricing references found. Please contact an admin or import a pricing reference in Settings before generating a quote." | B | DM | | `#profileSelect` |
| CU-02 | On a quote that already has Quote Basis or Output, pick another pricing reference and press Next: Cancel; then again, Continue and Clear. Repeat after adding a reference file on Upload. | "Apply changed pricing reference?" (or "Apply changed reference files?"): "This will clear the current Quote Basis and Output." Cancel keeps the old inputs and results. Continue and Clear applies them and clears Quote Basis and Output. | B | D | | `#quoteDependencyConfirmModal`, `#cancelQuoteDependencyConfirmButton`, `#confirmQuoteDependencyChangeButton` |
| CU-03 | Currency: each listed one, then Custom with `jpy`, then Custom with `12`. Exchange rate 1.35, then 0, -1, blank and `abc`. | The currency and the rate (as FX) show on Output. Custom becomes `JPY`; digits are dropped. A rate of 0 or less, blank or text is refused: the field says "Enter an exchange rate above 0." and Next: Quote Company is off, its hover text naming "Exchange rate above 0". The reference currency keeps a rate of 1. | B | D | | `#quoteCurrency`, `#quoteCurrencyCustom`, `#quoteExchangeRate` |
| CU-04 | Tax GST, then VAT; Rate 9, then 101, then -1. | The label and rate show on Output; a rate above 100 or below 0 is held at 100 or 0. | B | D | | `#quoteTaxLabel`, `#quoteTaxRate` |
| CU-05 | Fill Client name, Client address, Attention person, Attention title, Quote date, Quotation Title, Show name and Project number. Use B, I and U (and Ctrl+B, Ctrl+I, Ctrl+U) on some text and on the date. | Each value is kept. The formatting shows in the box and in the Excel. | B | DM | | `[data-rich-command="bold"]`, `[data-rich-command="italic"]`, `[data-rich-command="underline"]`, `[data-date-format-command="bold"]`, `[data-date-format-command="italic"]`, `[data-date-format-command="underline"]`, `#quoteDate`, `#showName`, `#clientNameEditor`, `#clientAddressEditor`, `#clientAttentionEditor`, `#clientTitleEditor`, `#projectTitleEditor`, `#projectNumberEditor` |
| CU-06 | Leave each required field empty in turn: pricing reference, Client name, Client address, Attention person, Attention title, Quotation Title, Show name, Quote date, Project number. | Next: Quote Company is off and its hover text names the missing field. | B | DM | | `#sideNextButton` |
| CU-07 | Fill everything; Next: Quote Company. | Quote Company opens; the Customer step is saved. | B | DM | | `#sideNextButton` |
| CU-08 | On a quote with Quote Basis, Reset Draft on Customer, then Cancel; again, then Reset Draft in the dialog. | It asks "Reset this step? Your changes on this step will be lost." It adds "This also clears Quote Basis and Output." Cancel (or Escape, or a click beside the dialog) changes nothing. Reset Draft in the dialog clears the Customer fields, Quote Basis and Output. | B | D | | `#clearCustomerButton`, `#resetDraftConfirmModal`, `#cancelResetDraftButton`, `#confirmResetDraftButton` |

### 5. Quote Company and company profiles

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| QC-01 | Open Current Profile; pick one; Load: Cancel; then Load. | It lists Profile Templates and Saved Profiles (your own and the shared ones only). Picking says `Selected "<name>". Click Load to apply it.` Load asks `Load "<name>"?` with "This will replace the current quote company fields." and `Unsaved edits in this section will be overwritten by "<name>" defaults.`; Cancel changes nothing; Load fills them and says `Loaded "<name>".` | B | DM | | `#presetSelect`, `#loadPresetButton`, `#cancelProfileLoadButton`, `#confirmProfileLoadButton` |
| QC-02 | Save New with an empty name, then `UAT Profile`; Save New again with the same name: Cancel; then Overwrite. | "Enter a reusable profile name before saving." Then `Saved "UAT Profile".` and it is listed. The same name asks `Overwrite "UAT Profile"?`; Overwrite replaces it. | B | D | | `#savePresetButton`, `#profileNameInput`, `#cancelProfileNameButton`, `#confirmProfileNameButton`, `#cancelProfileOverwriteButton`, `#confirmProfileOverwriteButton` |
| QC-03 | More Actions: Export Profile; Import Profile with the exported file, then with a JSON file that is not a profile; Delete Profile on `UAT Profile`: Cancel; then Delete; Delete Profile on a profile template. Open and close the menu with the keyboard too. | Export downloads `<id>.quote-company-profile.json`. Import offers it to save as your own copy; with the name of a profile you can change, it asks `Overwrite "<name>"?` first and Cancel keeps that profile; the wrong file says "The selected file does not contain importable company profile defaults." `Delete "UAT Profile"?` then Delete says `Deleted "UAT Profile".` and the quote's filled fields stay. A profile template says `Cannot delete "<name>"`: "Profile templates are read-only." | B | DM | | `#profileActionsMenuButton`, `#exportPresetButton`, `#importPresetButton`, `#importPresetFile`, `#deletePresetButton`, `#cancelProfileDeleteButton`, `#confirmProfileDeleteButton` |
| QC-04 | As staff, save and delete a profile of your own, then try to change a shared profile; as admin, look for another user's personal profile. | Staff manage their own profiles but cannot change a shared one (its template note says "Read-only profile"). Nobody, admin included, sees another user's personal profile. | B | D | | `#presetSelect` |
| QC-05 | Header logo: click the box, and press Enter on it; choose a PNG, then a JPEG; then try a PNG of about 3 MB and a GIF renamed to `.png`. | The logo shows in the box. The picker offers PNG and JPEG only; the 3 MB PNG is refused at once with "Use a PNG or JPEG logo of 2 MB or less."; the renamed file is refused ("The selected logo could not be securely verified. Please try adding it again.", or on save "Profile logo must be a PNG or JPEG image."). | B | D | | `#headerLogoPreview`, `#headerLogoInput` |
| QC-06 | Fill Header details, Terms heading, Payment terms, Notes heading, Standard notes and every Signature field, with B, I or U on some. | Each is kept and shows in the Excel and PDF. | B | DM | | `[data-rich-command="bold"]`, `[data-rich-command="italic"]`, `[data-rich-command="underline"]`, `#headerDetailsEditor`, `#termsHeadingEditor`, `#paymentTermsEditor`, `#notesHeadingEditor`, `#standardNotesEditor`, `#quoteCompanyNameEditor`, `#companySignatoryEditor`, `#companyTitleEditor`, `#companyDateLabelEditor`, `#acceptanceTextEditor`, `#personLabelEditor`, `#stampLabelEditor`, `#dateLabelEditor` |
| QC-07 | Quote template, on a saved profile you can change: Workspace default, a saved template, and Profile's own layout when the profile has one. | Each pick saves at once and says `"<profile>" will use <layout> for new quotes.`; the note reads, for example, "Workspace default: Standard". The Excel follows it (XP-05). | B | D | | `#quoteTemplateSelect`, `#quoteTemplateNote` |
| QC-08 | Reset Draft on Quote Company, then Cancel; again, then Reset Draft in the dialog. | It asks "Reset this step? Your changes on this step will be lost." Cancel (or Escape, or a click beside the dialog) changes nothing. Reset Draft in the dialog clears the Quote Company fields. | B | D | | `#clearQuoteCompanyButton`, `#resetDraftConfirmModal`, `#cancelResetDraftButton`, `#confirmResetDraftButton` |
| QC-09 | Look at the Next button. | "Start Analysis" on a new quote; "Next: Quote Basis" once the quote has a Quote Basis. | B | DM | | `#sideNextButton` |
| QC-10 | Leave each required Quote Company field empty in turn: Header logo, Header details, Quotation Company, Acceptance text, Company signatory, Signatory title, Person label, Stamp label; press Start Analysis. | Refused: "Complete Quote Company details before starting analysis: <field>." | B | D | | `#sideNextButton` |

### 6. Analysis

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| AN-01 | On a quote at Quote Company, Reset Draft on Upload (and Reset Draft in its dialog), then try to open Quote Company and Start Analysis. | Customer and every later step lock with "Add reference files before opening this step.", so analysis cannot start (the app's own refusal is "Add at least one reference file before starting analysis."). No AI call is made. | B | D | | `#sideNextButton` |
| AN-02 | Start Analysis, then Cancel. | The "Start Analysis" dialog says analysis cannot be stopped once it starts and offers Run High Quality (3 credits), Cancel and Run Analysis (1 credit). Cancel closes it with no AI call. | B | DM | | `#analysisConfirmModal`, `#analysisConfirmCancelButton` |
| AN-03 | `$` Run Analysis on synthetic booth images with Known details "Booth is 8 m x 9 m, 2 open sides". | "AI analysis running." with "This will take about 10 to 15 mins." and the elapsed time, then the Quote Basis draft, with no AI spend line on Quote Basis or Output. The stated size is used. | B | D | | `#analysisConfirmStartButton` |
| AN-04 | `$` Run High Quality on the same quote, after the owner's OK on its cost. | As AN-03, with the High Quality cost. | A | D | | `#analysisConfirmHighQualityButton` |
| AN-05 | `$` Re-Analyse on Quote Basis after deciding some lines. | The Start Analysis dialog opens; afterwards the decided lines keep their decisions and no line is doubled. | B | D | | `#analyseAgainButton` |
| AN-06 | `$` Answer the clarification questions, if the draft asks any, and Generate final Quote Basis (a new paid analysis). | Quote Basis and Output stay locked until every question is answered; then the final draft shows. | B | D | | `#clarificationForm`, `[data-clarification-index]` |

### 7. Quote Basis

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| QB-01 | Look at the draft. | "Quote Basis" with the review and output line counts, the legend, the Detected booth card when a size is known, and the Matched, Review and Unmatched groups of blocks. Each line has its text, a quantity with its unit, a tick, an X and Re; each block has its own tick and X. Estimated sizes carry "Est.". Areas say `sqm`. The booth profile is information, never a quote line. | B | DM | H | `#basisReviewSurface`, `[data-basis-tag]`, `[data-revise-line-index]`, `[data-basis-section-action]` |
| QB-02 | Tick a line, X another, then tick the X'd one. | Each changes at once (record the time per click). | B | DM | | `[data-basis-tag="Include"]`, `[data-basis-tag="Exclude"]` |
| QB-03 | A block's tick, then its X. | Every review line in that block changes together. | B | DM | | `[data-basis-section-action="Include"]`, `[data-basis-section-action="Exclude"]` |
| QB-04 | Tick an AI proposal line (one not in the pricing reference). | It is accepted as a custom line. | B | D | | `[data-basis-tag="Custom"]` |
| QB-05 | Use a line's possible pricing-reference match. | The line takes that match. | B | D | | `[data-basis-possible-match-index]` |
| QB-06 | Click a quantity, type a new one, press Enter; another, press Escape; a third, click away mid-edit. Reopen the quote. | Enter and clicking away keep the new value; Escape restores the old one; the kept values are there after reopening. | B | DM | | `[data-basis-quantity-edit]`, `[data-basis-line-index]`, `.basis-quantity-input` |
| QB-07 | On a new quote, find Project management fee and Waste disposal: tick one, X the other; Dashboard; Modify quote; Reset Draft (and Reset Draft in its dialog). | Both start at Confirm. The tick includes it at no charge (Included); X leaves it out. Both are kept on reopen; Reset Draft returns both to Confirm. | B | D | | `[data-basis-tag]`, `#resetQuoteBasisButton` |
| QB-08 | `$` With a booth size in Known details: on the "Booth size update" card, Discard; Re-Analyse (a paid analysis, as AN-05); then Apply. | Discard keeps the current size. Apply sets the booth and floor lines to the Known details size, which stays after Reset Draft and reopening. | B | D | | `[data-known-details-size="discard"]`, `[data-known-details-size="apply"]` |
| QB-09 | Confirm Quotation Basis with a line still at Confirm; with a quantity left blank that cannot be filled; with a clarification question unanswered. | Refused with "Resolve all review lines before confirming quotation basis.", "Enter a quantity for every line before confirming quotation basis." and "Answer all clarification questions before confirming quotation basis." | B | D | | `#sideNextButton` |
| QB-10 | Resolve every line; Confirm Quotation Basis. | "Preparing Output", then Output with one row per included line. | B | DM | | `#sideNextButton` |
| QB-11 | Reset Draft on Quote Basis, then Cancel; again, then Reset Draft in the dialog. | It asks "Reset this step? Your changes on this step will be lost." Cancel (or Escape, or a click beside the dialog) changes nothing. Reset Draft in the dialog takes every line back to the first analysis draft. | B | D | | `#resetQuoteBasisButton`, `#resetDraftConfirmModal`, `#cancelResetDraftButton`, `#confirmResetDraftButton` |
| QB-12 | On a generated quote: tick again lines that were X'd when it was saved, change a quantity, then Dashboard. | The changes save and the Dashboard opens; reopening shows them. | B | D | | `[data-basis-tag]`, `#backToDashboardButton` |
| QB-13 | `$` Analyse a new quote whose Known details say "Booth is 6 m x 6 m" (the same as the default size). | The 6 m x 6 m size is used as stated in Known details; no line says "Booth size defaults to 6m x 6m because no clear dimensions were found". | B | D | | `#basisReviewSurface` |

### 8. Revise a line (Re)

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| RE-01 | Re on a line; close with x; open again and click outside the panel. | "Revise basis line" opens with the line as context; both close it with no change. | B | DM | | `[data-revise-line-index]`, `#basisChatCloseButton`, `[data-basis-chat-close]` |
| RE-02 | `$` Ask about the line (for example "what does this line cover?"), then something unclear (for example "size not correct"), then a change (for example "make this 4 m wide"); Discard the proposal. Then press Send with an empty box, and paste more than 1,000 characters. | Every message waits for the AI. The question gets an answer and the unclear one a question back, both with Apply off; the change shows a preview of the whole row with Discard and Apply. Discard leaves the line as it was. An empty box sends nothing; the box stops at 1,000 characters. If a reply cannot be used: "The AI reply could not be used, so the line was not changed. Please say the change another way." | B | D | | `#basisChatPrompt`, `#basisChatSendButton`, `#basisChatKeepButton`, `[data-basis-chat-action="discard"]` |
| RE-03 | `$` Ask again and Apply; Confirm Quotation Basis. | The line changes in Quote Basis and its Output row follows. A price the row already held is kept. | B | D | | `#basisChatApplyButton`, `[data-basis-chat-action="apply"]` |
| RE-04 | `$` On a generated quote, Re on a line and Apply, then open Output. | The Output row follows the revised line; a line ticked back keeps its tick. | B | D | | `#basisChatApplyButton` |

### 9. Output

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| OUT-01 | Look at the header. | The status pill (Draft, Confirm Quote Basis while Quote Basis has edits not yet confirmed, Needs pricing or Ready), the pricing reference name, currency, tax, FX and the number of approved lines match the Customer step and the rows. | B | DM | | `#outputStatusPill`, `#outputSourceLabel`, `#outputTotalLines` |
| OUT-02 | Look at the table; Sort by each of Pricing Reference, Category A-Z, Name A-Z, Category + Name. | Section, Description, Quantity, Unit, Reference price, FX, Quoted unit price, Amount and Actions; each sort reorders the rows only. At 375 px the table scrolls inside its box, not the page. | B | DM | | `#outputSortMode`, `#pricingMatchesBody` |
| OUT-03 | Click a quoted unit price, type a new one, press Enter; another, press Escape. Then click a row's Description and its Quantity, change each and press Enter. | Enter keeps the price and the amount becomes quantity times price, with no FX on a custom price. Escape restores the old one. The new description and quantity are kept and the amount follows the quantity. | B | D | | `[data-output-edit-field]`, `.output-unit-price-editor` |
| OUT-04 | In the price editor, Included. | The row reads Included and adds nothing to the total. | B | D | | `[data-output-included-action="true"]` |
| OUT-05 | In the price editor, Use reference price, on a row with a catalogue price and on one without. | The catalogue price returns; without one the button is off and says "Catalogue price unavailable". | B | D | | `[data-output-reference-action="true"]` |
| OUT-06 | Del on a row: Cancel; then Delete. | "Delete output row?" Cancel keeps it; Delete removes it and the totals follow. | B | D | | `[data-output-delete-row]`, `#cancelOutputDeleteButton`, `#confirmOutputDeleteButton` |
| OUT-07 | Type `abc` as a price and press Enter; type `-5` and press Enter; then leave a row with no price. | The price editor refuses `abc` and `-5` with "Enter a nonnegative price with exact cent precision, or cancel with Escape." and keeps the old price. A row with no price: Download Excel and View PDF stay off, hovering either says "Price every row first.", and the review messages name the row ("Row N: Unit price is required.", or the row's pricing review message). | B | D | | `#pricingReviewMessages`, `#sideDownloadButton` |
| OUT-08 | Type a quantity on Output; Reset Draft, then Cancel; again, then Reset Draft in the dialog. | It asks "Reset this step? Your changes on this step will be lost." Cancel (or Escape, or a click beside the dialog) changes nothing. Reset Draft in the dialog takes the rows back to the first Output. | B | D | | `#resetOutputButton`, `#resetDraftConfirmModal`, `#cancelResetDraftButton`, `#confirmResetDraftButton` |
| OUT-09 | Add up the amounts. | Subtotal is the sum of the amounts, tax is the rate times the subtotal, and the grand total is their sum, to the cent. | B | D | | `#pricingMatchesBody` |
| OUT-10 | Back to Quote Basis, change a line, Confirm Quotation Basis again. | Output follows the change; prices already set on other rows stay. | B | D | | `#sideBackButton`, `#sideNextButton` |

### 10. Excel and PDF

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| XP-01 | Download Excel. | "Regenerating Excel": "Building the workbook from the current reviewed rows.", then "Excel ready": "Your workbook is ready. Click Download Excel to save it." Download Excel saves `quotation.xlsx`; Close closes it. Record the time. | B | DM | | `#sideDownloadButton`, `#excelGeneratingModal`, `#excelGeneratingActionButton`, `#excelGeneratingCloseButton` |
| XP-02 | View PDF. | "Generating PDF": "Building the workbook first, then preparing its PDF export.", then "PDF ready": "Your PDF is ready. Click Open PDF to view it in a new tab." Close comes before Open PDF; Open PDF opens it in a new tab. Record the time. | B | DM | | `#sideViewPdfButton`, `#excelGeneratingActionButton`, `#excelGeneratingCloseButton` |
| XP-03 | Read the Excel as a file. | Header logo and details sit inside the print area; table headers bold; quantities centred; amounts with thousands separators and two decimals; totals are live formulas equal to the app's totals; Included rows say Included; X'd rows are absent; exclusions are listed before the terms; notes and signature follow. | B | D | | |
| XP-04 | Open the Excel in desktop Excel. | It opens with no repair prompt, fits A4 when printed, and looks right. | A | D | H | |
| XP-05 | Generate with Workspace default, a saved template, and Profile's own layout. | Each Excel follows its layout; the totals and rows are the same in all three. | B | D | H | `#quoteTemplateSelect` |
| XP-06 | Read the PDF. | The same rows and totals as the app; no blank page; the signature follows the notes. | B | D | | |
| XP-07 | Open the PDF on a real phone. | It opens and reads well. | B | M | H | |
| XP-08 | Generate the quote without pressing View PDF. | No PDF is made; the Dashboard's PDF link says there is none. | B | D | | `.dashboard-export-link` |

### 11. Settings: pricing references

Settings is for admins. Run these as admin, with `UAT Catalogue` made in this
run; never change or delete a pricing reference the run did not make.

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| PR-01 | Settings as admin; close with x, Cancel and a click outside; open again. | "Settings" opens on Pricing Reference, Manage. Each way closes it. | A | DM | | `#settingsButton`, `#settingsPricingTab`, `#pricingReferenceManageTab`, `#pricingReferenceCloseButton`, `#pricingReferenceCancelButton`, `[data-pricing-reference-close]` |
| PR-02 | Settings as staff. | The button is off with "Only admins can open Settings."; if Settings is reached, "No access" says the role cannot create, edit, import, delete or save pricing references, and Quote Templates is off with "Only admins can change quote templates." | S | D | | `#settingsButton`, `#pricingReferenceNoAccess` |
| PR-03 | `$` Import: Choose File with a synthetic 4-row CSV (section, description, unit, cost, markup), then a synthetic .xlsx and a .md. | "Preparing import preview" with the elapsed time, then the preview: "No missing required info detected" or "Needs attention" with each problem (missing columns, rows that need edit, skipped rows with the reason, name already exists), and Review N Rows. Name, tax label, tax rate and currency appear only after every row that needs an edit is fixed in Review N Rows (PR-05), not with the first preview. | A | D | | `#pricingReferenceImportTab`, `#pricingReferenceFile`, `#pricingReferencePreview`, `#pricingReferenceMetadataSetup` |
| PR-04 | Fill name `UAT Catalogue`, tax GST 9, currency SGD (and Custom with a 3-letter code); Save Reference. Then import again with the same name. | Save Reference stays off, and says why on hover, until the rows and fields are valid; then "Saved successfully." and the reference is listed in Manage and on Customer. The same name is refused: `A pricing reference named "UAT Catalogue" already exists. ...` | A | D | | `#pricingReferenceName`, `#pricingReferenceTaxLabel`, `#pricingReferenceTaxRate`, `#pricingReferenceCurrency`, `#pricingReferenceCurrencyCustom`, `#pricingReferenceSaveButton` |
| PR-05 | Review the imported rows: edit a row, Add Row, Remove a row, Undo, Done. | "Review Imported Rows" lists Section, Description, Unit, Cost, Markup, Remarks, Status and Actions. Each change shows at once; Undo reverses the last; Done returns to Settings. Status reads OK, or what is missing (for example "cost required", "markup must be above 0"). | A | DM | | `[data-pricing-reference-table-open]`, `#pricingReferenceAddRowButton`, `[data-pricing-reference-remove-row]`, `[data-preview-field]`, `#pricingReferenceUndoButton`, `#pricingReferenceTableCloseButton`, `[data-pricing-reference-table-close]` |
| PR-06 | Manage: pick `UAT Catalogue`, Review its rows, change a cost, Save Changes; reopen. | The rows load ("N rows loaded ..."); the button reads Save Changes; the change is kept after reopening. | A | D | | `#deletePricingReferenceSelect`, `[data-pricing-reference-table-open]`, `#pricingReferenceSaveButton` |
| PR-07 | Manage: Export Selected. Import: Download Template. | Export downloads `UAT Catalogue` as an Excel workbook that imports again; Download Template downloads `pricing-reference-template.xlsx`. Each button shows only on its own tab. | A | D | | `#exportPricingReferenceButton`, `#pricingReferenceTemplateButton` |
| PR-08 | `$` Use `UAT Catalogue` on a new quote (Customer, CU-01) and analyse. | Reference prices on Output come from `UAT Catalogue`'s rows. | A | D | | `#profileSelect` |
| PR-09 | Manage: Delete on `UAT Catalogue`: Cancel; then Delete. Then pick a built-in or default reference. | `Delete "UAT Catalogue"?`: "This removes the saved pricing reference from this workspace. Existing quotes already generated from it are not changed." Cancel keeps it; Delete removes it from Manage and from Customer. Delete is off for built-in and default references, saying why. | A | D | | `#deletePricingReferenceButton`, `#cancelPricingReferenceDeleteButton`, `#confirmPricingReferenceDeleteButton` |
| PR-10 | Import a file of the wrong type, a file over 10 MB, an empty (0-byte) CSV, and a CSV with only the template's header row (from Download Template) and no rows. Do not use a file with other columns: that goes to the paid AI import. | "Upload a .xlsx, .csv, or .md pricing reference file.", "Pricing reference file is larger than 10 MB.", "This file has no rows to import." for the empty file and, for the header-only CSV, "No valid pricing rows were found." (or "No pricing rows"), each with no AI call; nothing is saved. | A | D | | `#pricingReferenceFile` |

### 12. Settings: quote templates

Run as admin with templates made in this run. Make the workspace default
`Standard` again before the run ends.

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| QT-01 | Settings, Quote Templates. | "Quote templates" lists Standard (Built in) and the saved templates, each with how many profiles use it ("Used by N profiles" or "Not used by any profile yet"), the Default badge on the workspace default, and "N of 10 saved". Loading shows "Loading templates..."; a failed load shows "Templates could not be loaded." with Try again. | A | DM | | `#settingsTemplatesTab`, `#qtList`, `#qtCounter`, `#qtListRetry` |
| QT-02 | Make a copy on Standard; clear the name, then try to type 61 characters, then the name of another template, then `UAT Template`; Save template. | The editor opens as `Copy of Standard`. "Give the template a name."; the name box stops at 60 characters; "Another template already has this name." Save template then says `"UAT Template" is ready to pick in Company Profile.` and it is listed. | A | DM | | `#qtNameInput`, `#qtSaveButton` |
| QT-03 | New template; make a change; Cancel; All templates. | After a change the footer says "Unsaved changes" and Cancel becomes Discard changes with "Your unsaved changes will be lost."; nothing is saved. | A | D | | `#qtNewButton`, `#qtCancelButton`, `#qtBackButton` |
| QT-04 | In the editor: Page style (font, text size, text and heading colour, column widths, Hidden, Add column, Remove column); click each block (Logo, Company details, Client details, Quote details, Item table, Totals, Free text, Terms, Notes, Signature, Acceptance) and change its position, order, size, logo width and the item table's columns, headings and alignment; drag a column edge on the ruler; Whole page, zoom in and out. | The side panel shows the selected item's settings; changes show on the page at once; the column width follows the drag; zoom runs from 30% to 150% and changes the view only. At 375 px, tapping a block or a column letter edits it and the page scrolls sideways inside its box. | A | DM | H | `#qtPaperWrap`, `#qtSide`, `#qtZoomFit`, `#qtZoomIn`, `#qtZoomOut`, `[data-fk]`, `[data-col]`, `[data-block]`, `.qt-col-letter`, `.qt-chip` |
| QT-05 | Make the logo wider than its space, the columns too wide for the page, Totals above the item table, and a block only on hidden columns; Save template; press the status button. | Each problem is marked in red with a plain message (for example "The columns are too wide to fit across the page. Make some narrower." and "Totals must come after the item table. Move it down."). The status button reads "N things to fix" and jumps to the first; Save says "Fix the N items marked in red, then save." and saves nothing. | A | D | | `#qtStatus`, `#qtSaveError`, `.qt-problem` |
| QT-06 | Preview Excel and Preview PDF. | "The sample Excel file was downloaded." and "The sample PDF was downloaded." (`quote-template-preview.xlsx` and `.pdf`), each the template filled with sample data. | A | D | H | `#qtPreviewExcel`, `#qtPreviewPdf` |
| QT-07 | Edit `UAT Template`, change a label to `=1+1`, Save template. | It saves; the label shows as text in previews and quotes (RU-04). | A | D | | `#qtSaveButton` |
| QT-08 | On `UAT Template` (picked by `UAT Profile` in QC-07): Edit; the more menu's Make a copy, Make workspace default, then Delete (Cancel, then Delete template). | Edit opens it; the copy opens in the editor. Make workspace default says `"UAT Template" is now the workspace default. ...`, moves the Default badge, and Quote Company's note follows. `Delete "UAT Template"?` names the profiles that use it and says they switch to the workspace default; as the default, Standard becomes the default again. Delete template removes it; quotes already made keep their layout, and the profile's next quote uses the workspace default with the note "The template chosen for this profile was deleted, so the default template was used." | A | DM | | `#qtMore-*`, `#qtDeleteCancel`, `#qtDeleteConfirm` |
| QT-09 | Save templates up to the limit (10 unless the workspace sets another). | "Limit reached." with "You have 10 saved templates, the most a workspace can keep. Delete one to add or copy another." New template turns off; Make a copy turns off and hovering says "You have 10 saved templates. Delete one first." Delete the extra ones afterwards. | A | D | | `#qtNewButton`, `#qtLimitMessage` |
| QT-10 | Make `Standard` the workspace default again. | The Default badge is back on Standard. | A | D | | |

### 13. Reopen, reload, another browser and the recovery copy

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| RO-01 | Reload the browser on each step of a run quote, with an unsaved edit on the step. | The same step opens with every input, decision and price, the edit included. | B | D | | |
| RO-02 | Dashboard, then Modify quote, on quotes left at Customer, Quote Basis and Output. | Each opens at the step it was left at, with the same rows and totals. A generated quote opens at Output with its rows and Download available. | B | DM | | `[data-dashboard-panel-action="modify-session"]` |
| RO-03 | Sign in with the same login in another browser (or a private window) and open a run quote. | The saved quote opens with what the server holds: same steps, rows and totals. | B | D | | |
| RO-04 | With the same run quote open in two browsers, change a pricing choice in one, then in the other. | The second shows "Quote not saved": "This quote changed in another tab or session. Reopen it to see the latest version, then select the match again." The server keeps the first change. | B | D | | `#excelGeneratingModal` |
| RO-05 | Make an edit, close the tab before it saves, sign in again and Modify the quote. | The edit is there, or the "Quote not saved" dialog says to reopen the quote; the saved quote is never overwritten by an older copy. | B | D | | |
| RO-06 | On the copy only: Modify a quote of your own saved on live, then Next in Customer. | Quote Basis is locked ("Click Next in Customer to save this step before continuing.") until Next in Customer; then every step opens. Only this copy lock is expected (runbook step 8.2). | A | D | | `#sideNextButton` |
| RO-07 | Open a quote saved by the release now on live, made before the standard rows existed. | It opens where it was left; no standard rows are added; its totals are unchanged. | A | D | | |
| RO-08 | Go to a later step through the rail while a step is locked. | The rail button is off and its hover text says what to do first (for example "Confirm Quotation Basis before opening Output."). | B | DM | | `button[data-side-panel]` |

### 14. Feedback

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| FB-01 | Feedback in the top bar, where shown (it is hidden for signed-in staff, then N/A): Cancel; then fill Report type, Short title, Description, Impact, the optional fields and Manual reference, untick the diagnostic link, and Submit feedback. | "Report a quote issue" opens. Cancel closes it. Without a title and description it says "Add a short title and description before submitting." Submit shows "Feedback submitted. Support reference: ..." and clears the text fields. Use synthetic text only. | B | DM | | `#feedbackButton`, `#feedbackForm`, `#feedbackCategory`, `#feedbackShortTitle`, `#feedbackMessage`, `#feedbackExpectedResult`, `#feedbackActualResult`, `#feedbackReproductionSteps`, `#feedbackImpact`, `#feedbackManualReference`, `#cancelFeedbackButton`, `#submitFeedbackButton`, `#feedbackIncludeLink` |

### 15. Errors and refusals

Force each where it can be done safely (offline browser, a second sign-in, a
synthetic file); otherwise record NOT RUN.

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| ER-01 | Make a change with the browser offline, then online again. Also press Next on a filled Customer step while offline, and Start Analysis on Quote Company while offline. | "Quote not saved": "Quote could not be saved because the connection was interrupted. Please try again." The change saves once online. Next on Customer shows the same dialog. Start Analysis shows "AI analysis failed." in the red popup at the bottom right, which stays until you press its X. | B | D | | `#excelGeneratingModal`, `#stepMessagePopupClose` |
| ER-02 | Save after the session ended (as SI-08). | "Quote could not be saved. Please sign in again and try again." or the sign-in form with the ended note; nothing is lost. | B | D | | |
| ER-03 | Any failure message. | It is plain, generic, and carries a support reference where the server gave one. It shows no internal names, stack traces, keys or file paths. Step messages show in one popup at the bottom right, above Back and Next: an error is red and stays until you press its X (or Escape), its cause is fixed, or you open the Dashboard; a closed error does not come back for the same cause. A warning is amber and fades by itself. | B | DM | | `#stepMessagePopupClose` |
| ER-04 | Analysis fails (when it happens in the run). | The red popup at the bottom right: "AI analysis failed. Please try again. Contact support if this keeps happening." with a support reference; it stays until you press its X, and goes when the next analysis starts. The quote stays as it was. | B | D | | `#stepMessagePopupClose` |
| ER-05 | The Dashboard list cannot load (offline). | "Quote sessions unavailable" with the generic message and a support reference, and the count line reads "Sessions could not be loaded."; both stay until the list loads again. | B | D | | `#dashboardErrorState` |
| ER-06 | Act while the app is busy (press Next twice fast, or Download during a save). | The second press does nothing; nothing runs twice. | B | D | | |
| ER-07 | `$` Run Analysis on a PDF that is not a booth render (for example a quote template preview). | The red popup: "Please upload the booth render images first so I can analyze the design and prepare the quote. This analysis was still charged." with a support reference, not "Failed"; it stays until you press its X. | A | D | | `#stepMessagePopupClose` |
| ER-08 | Delete the company profile a test quote used (QC-03), open that quote at Output and press Download Excel. | The red popup: "Select a valid company profile before generating a quote." It stays until you press its X; no file downloads. | A | D | | `#sideDownloadButton`, `#stepMessagePopupClose` |

### 16. Quoting rules

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| RU-01 | Try to analyse or generate without reference images (AN-01, and the analysis request with no images). | Refused in the app ("Add at least one reference file before starting analysis." or "Please drop at least one reference file before analysis."); the server's answer is "Please upload reference images first so I can analyze the design and prepare the quote." No quote comes from text alone. | B | D | | `#sideNextButton` |
| RU-02 | Search the app, the Excel and the PDF of a run quote for `m2`, m with a raised 2, and `sq m`. | None; square metres read `sqm`. | B | D | | |
| RU-03 | Search the Excel and PDF for cost, markup, supplier notes or internal remarks. | None of them; only quoted prices, tax and totals. | B | D | | |
| RU-04 | Put `=1+1`, `+1`, `-1` and `@SUM(1)` in Client name, Quotation Title, Payment terms, Standard notes, a template label and (`$`, through Re) a line's text. | Each shows as plain text in the app, the Excel and the PDF; no Excel cell holds them as a formula. | B | D | | |
| RU-05 | Generate a quote. | Only the Excel is made unless View PDF is pressed (XP-08). | B | D | | |
| RU-06 | Try to reach Output before Confirm Quotation Basis. | Refused until the basis is confirmed. | B | D | | `button[data-side-panel]` |
| RU-07 | Leave a line with no price. | It shows in the pricing review messages, not in the customer file (OUT-07). | B | D | | `#pricingReviewMessages` |
| RU-08 | Check prices come from the quote's pricing reference. | Reference prices on Output match that reference's rows (PR-03). | B | D | | |

### 17. Look and feel

| Line | Do | Expect | Who | Width | Eye | Controls |
| --- | --- | --- | --- | --- | --- | --- |
| LF-01 | Every screen and dialog above at 1440 px and 375 px. | Nothing cut off, overlapping or scrolling sideways; one main action per screen; footers never cover content. Screenshots of each. | B | DM | H | |
| LF-02 | Tab through each screen. | Every action is reachable and the focus outline shows. | B | D | | |
| LF-03 | Sign in, open a quote, review Output and open the PDF on a real phone (iPhone Safari and Android Chrome). | Everything works and reads well. | B | M | H | |
| LF-04 | Read every label, note and message met in this run. | Plain words staff use; no internal names, units or ids. | B | DM | H | |

## Bug report format

```text
Date and time:
Browser and width:
Role:
Line:
Steps:
Expected result:
Actual result:
Screenshot (synthetic data only):
Error reference shown, if any:
```
