# UI design standard

How new or changed screens in the quote generator look and behave. It describes the app's existing UI, taken from `webapp/static/styles.css`, `index.html` and `app.js`, so that new screens match it. It does not change any existing screen.

AGENTS.md still applies: the current UI is final. Any visible UI change needs the owner's explicit approval in the current turn, normally by approving a clickable mockup first. This document says how an approved change should look; it never approves one.

## 1. How UI work is done

1. **Start from what the user is doing.** For each screen, write one line: who uses it, what they are trying to get done, and its one main action. Everything else on the screen is secondary.
2. **Mockup before code.** New screens start as a clickable, self-contained HTML mockup with made-up data, built from this standard and the app's real styles. The owner approves it before any product code.
3. **Offer two or three options when the look is open.** Picking between options is easier than critiquing one.
4. **Look at it before anyone else does.** The builder opens the mockup or the built screen in a browser (Claude in Chrome, the built-in browser, or computer use), clicks through every state as the user, and takes screenshots at 1440 px and 375 px wide. It fixes anything on the checklist in section 4 first.
5. **Review checks it.** The G2 review checks UI changes against this standard and the checklist, with screenshots.
6. **Owner UAT comes last.** The agent runs the owner's UAT steps itself first; the owner tests only once that is green.

## 2. The existing system

Use the tokens in `:root` (`styles.css` lines 50-122). Do not add new hex colours, shadows or radii for new work; if a new value is really needed, add it as a token.

### Type

One family: Inter (`styles.css` line 121). No other display, serif or mono face.

| Role | Size / weight | Where it is today |
|---|---|---|
| Eyebrow above a title | 12 px / 850, uppercase, 0.03em, `--muted` | `.workspace-pane-eyebrow` ("Confirm Draft", "Workspace") |
| Pane or window title | 22 px / 850, `#071931` | `.workspace-pane-header h2` ("Quote Basis") |
| Card or form section title | 18 px / 850 | `.quote-form-section h3`, Quote Basis card title |
| Group heading inside a pane | 16 px / 700 | the "Matched" heading |
| Item title in a list row | 15 px / 800 | `.basis-review-item h4` |
| Body and control text | 15 px | buttons, inputs |
| Help text under a title | 14 px / 400, `--muted`, line-height 1.45 | `.workspace-pane-subtitle` |
| Small notes | 13 px, `--muted`, line-height 1.45 | `.settings-note` |
| Field label | 14 px / 780, above the field | `.field-control` |
| Compact field label | 12 px / 800 | `.compact-control` |
| Table header | 12 px / 850, uppercase, 0.03em, `#eef3f7` background | `th` |

New work uses only these roles. Nothing smaller than 12 px for text people read.

### Colour

- Brand and main actions: teal `--green` (`#0f766e`), soft fill `--green-soft`.
- Text: `--ink`, `--ink-secondary`, `--muted`, `--muted-light`.
- Lines: `--line` for cards and rows, `--line-strong` for inputs and secondary buttons.
- Focus and information: blue `--blue`, `--blue-soft`.
- Warning: amber `--amber`, `--amber-soft`, `--amber-border`.
- Danger and errors: red `--red`, `--red-soft`, `--red-border`.
- Page background `--bg`; cards `--surface` (white glass).

Colour never carries meaning alone: a warning or error also has a word or icon.

### Spacing and layout

- Gaps in use: 8, 10, 12, 14, 16 px; larger steps 24 and 28 px. New work uses 8 / 12 / 16 / 24.
- Working pane: 28 px side padding, header `22px 28px 20px`, footer `16px 28px`.
- Cards inside a pane: 14-17 px padding, `--line` border, `--radius-md`.
- Content width: `--workspace-content-width` (max 1120 px); dashboard `--dashboard-content-width`.
- Radii: `--radius-sm` 6, `--radius-md` 8 (controls, cards), `--radius-lg` 12 (panes, modals), `--radius-pill`.

### Buttons

| Kind | Look | Use |
|---|---|---|
| Primary | `.primary-button`: 44 px tall, teal gradient, white 15 px / 750 | The one main action of a screen or dialog |
| Secondary | `.secondary-button`: 44 px, white with `--line-strong` border | Other actions |
| Danger | `.secondary-button.danger-button` | Delete, remove, reset |
| Standard action group | 40 px tall, min-width 96 px, 14 px, `--radius-md`, 8 px gap | Profile card, PDF/Excel dialog; tests in `tests/test_button_standard.py` |
| Row icon buttons | 32 x 32 px | Per-row tick, X, edit in Quote Basis |
| Top bar pills | `.topbar-action`: 40 px, pill, 14 px / 760 with 17 px icon | Top bar only |
| Icon button | `.icon-button`: 34 x 34 px | Close X on windows |

- One primary button per screen or dialog. Several equal buttons in every row of a list is wrong: give each row one visible action (usually Edit or Open) and put the rest in a "More" menu or behind the selected row.
- At 640 px wide and below, buttons are at least 44 px tall.
- Focus: the approved groups use a 2 px solid teal outline offset 2 px; new buttons do the same.
- Disabled: opacity 0.55, with a reason nearby when it isn't obvious.

### Forms

- Inputs, selects and text areas: 44 px tall, 15 px, `--line-strong` border, `--radius-md`; focus is a blue border plus `--shadow-glow-blue`.
- Compact controls (dense panels only): 36 px, 14 px.
- Labels sit above the field. Help text goes under the field in 13 px muted. Errors show under the field in red text plus a summary message; Save stays usable or says why it can't be used.
- Colour fields show a swatch and the hex code, never a bare colour bar.
- Use the words staff use. "Width", "Starts at column", "Hidden", not internal units or model terms. Square metres are `sqm`.

### Containers

- **Working pane** (`.side-workspace`): eyebrow, title, one-line help text, actions top right; one scroll area; footer actions at the bottom.
- **Step bar** (`.command-rail`): Upload, Customer, Quote Company, Quote Basis, Output. New steps need owner approval.
- **Settings**: a full-window overlay (`.modal-overlay.pricing-reference-settings-page`, 630 px panel) with eyebrow "Settings", a title and a close X. Sections switch with the segmented tabs (38 px, 14 px / 850, active tab white). New settings go in as another section of this window, not a new window.
- **Dialogs** (`.modal-overlay`): panel up to 620 px, `--radius-lg`, header `18px 18px 14px`, actions in a footer, primary on the right.
- **List rows**: white card rows like `.basis-review-item` (`--line` border, `--radius-md`, light hover).
- **Tables**: `.table-wrap` with `--line` border; scroll sideways inside the wrapper, never the page.

### Status and feedback

- **Blocking problem**: banner like `.ai-failure-banner`: `--red-soft` fill, 5 px red left border, a bold lead-in ("AI analysis blocked.") then what to do.
- **Running or warning**: the same banner in amber.
- **Badges**: 24 px pills, 11-12 px / 800-850 (e.g. "Profile template").
- **Empty state**: dashed `#c4d1df` border, `#f8fbff` fill, one line saying what goes here and the action that fills it.
- **Loading**: the existing spinner or typing dots; anything over a second shows progress or elapsed time.
- Live regions (`aria-live`) for lists and banners that change.

### Icons

Inline SVG only, 24 x 24 viewBox, `stroke="currentColor"`, no fill, round caps and joins, stroke 1.8-2. Sizes: 17 px in the top bar, 20 px in the step bar, 48 px for the upload drop icon.

### Motion

Use `--duration-fast` / `--duration-base` / `--duration-slow` and `--ease-out`. Respect `prefers-reduced-motion` (`styles.css` line 154).

### Screen sizes

Existing breakpoints: 1260 px (panels stack), 880 px (rail and pane go full width), 640 px (44 px buttons), 379 px (buttons take full rows). New work reuses these. Check every new screen at 1440, 1024, 880 and 375 px wide: no sideways page scroll, nothing cut off, nothing overlapping.

## 3. Known inconsistencies in the existing UI

Existing screens have a few mixed values. New work uses the standard choice in the right-hand column. Existing screens are not changed to match unless the owner approves that change.

| Mixed today | Standard for new work |
|---|---|
| Button heights 28-48 px | 44 px main buttons, 40 px action groups, 32 px row icons |
| Three red border colours for danger | `--red-border` |
| Settings close X red, drawer close X grey | follow the window it sits in |
| Three eyebrow specs | 12 px / 850 / 0.03em |
| Card titles 18, 19 and 20 px | 18 px / 850 |
| Literal hex, shadows and radii next to tokens | tokens only |
| Focus: none, blue, or teal | 2 px teal outline on buttons, blue glow on inputs |
| Disabled opacity 0.46-0.68 | 0.55 |
| Amber and orange warning palettes | amber tokens |

## 4. Review checklist

Each new or changed screen passes all of these before the owner sees it:

- [ ] It has one clear main action, visibly stronger than everything else.
- [ ] Nothing scrolls inside something else that scrolls; footers never cover content.
- [ ] No dialog opens from a dialog unless it's a confirm.
- [ ] Previews and the thing being edited are big enough to read (12 px text or more).
- [ ] Editing works on the thing itself where that's cheap (click to select, then move or resize), not only through long forms of number boxes.
- [ ] Every word is one staff use; no internal names, units or IDs.
- [ ] Rows don't repeat the same row of equal buttons.
- [ ] It has an empty state, a loading state and an error state, each saying what to do.
- [ ] Sizes, colours and spacing come from section 2 only.
- [ ] Keyboard: every action is reachable and the focus is visible.
- [ ] It works at 1440 and 375 px wide, with screenshots.
- [ ] Destructive actions ask once, name what is lost, and say if it can't be undone.
- [ ] User and template text never becomes an active spreadsheet formula.
