# Design System — unbagged

Read this before making any visual or UI decision. If a change contradicts
something here, change this file first and say why in the decisions log.

## Product context

- **What this is:** a local-first reader for the response a retailer sends when
  you file a legal right-to-know request. It parses the report, shows you what
  they hold, what they inferred, and what they never answered.
- **Who it's for:** one person, on their own machine, reading a document about
  themselves. Not a team, not a dashboard, no accounts.
- **Space:** privacy and data-rights tooling. The nearest useful analogues are
  personal-finance archives, not compliance dashboards.
- **Project type:** local web app, dense and numeric, run at `localhost`.

## The one thing to remember

**This is your archive.** Two years of your own life, handed back by a company
that had to be compelled to hand it over. The product serves recognition and
browsing first. The compliance material stays honest and stays present, but it
is a supporting act, not the voice.

Every decision below is checked against that sentence.

## Aesthetic direction

- **Direction:** archival document. A quiet reading room: warm paper, iron-gall
  ink, ruled and margined like a bound ledger.
- **Decoration level:** minimal. Typography, rules and whitespace do all the
  work. No shadows, no elevation, no gradients, no illustration.
- **Mood:** still, dense, unhurried, and entirely without alarm.
- **First three seconds:** *"Oh — that's mine."* Recognition first. The unease
  arrives later, on its own, when the reader hits the appended-attributes block
  and reads *source not disclosed* for the sixteenth time.

An app that manufactures alarm in second one is a compliance report wearing a
costume. This one earns the reaction by being an accurate archive and letting
the accuracy do the work.

### Why a document and not a dashboard

The app was already citing a source document by page number on every row —
`p.5`, `p.12`, `p.47` — and nobody had noticed that is a footnote apparatus.
Books have been solving "what matters on a dense page" for five hundred years.
Dashboards have been failing at it for fifteen. The tile metaphor also lies
about the data: your March baskets are not a separate concern from your April
baskets, and putting them in separate floating cards says they are.

## Typography

Three voices. Two come from the system stack and ship no bytes.

- **Display and prose:** `ui-serif, Georgia, "Iowan Old Style", "Palatino
  Linotype", serif`. Carries view titles, header figures, the hanging count
  numerals, the follow-up letter, and any sentence longer than about eight
  words. A serif is what makes a $43.11 grocery trip read as a page rather than
  a row in a table.
- **Interface:** the existing system sans stack. Labels, column headers,
  buttons, tabs. No personality that competes with the serif.
- **Data:** the system monospace stack — `ui-monospace, SFMono-Regular, "SF
  Mono", Menlo, Consolas, "Liberation Mono", monospace`. Every numeral that sits
  in a column: prices, dates, counts, UPCs, identifiers, locators.
  **No font file ships.** See the decisions log: vendoring Iosevka was planned,
  written up here as though done, and never carried out.

**The numeral rule.** Numerals in running prose stay in the serif or sans with
`font-variant-numeric: tabular-nums`. Numerals in any aligned context are set in
the mono stack, via the `.num` class. That is the whole rule.

**Why no font file.** Shipping none is the cheapest way to keep "no external
requests" true forever: there is no file to get wrong, no licence to track, and
nothing to load. A narrower face would let more numeric columns fit, which is a
real gain and the reason vendoring Iosevka was considered — but it is a gain
measured against a constraint the app has not yet hit.

**If a face is ever vendored**, it needs its own licence notice in the repo and
its own family name if it is a custom build, and the no-font-files test below
becomes a one-font allowlist rather than being deleted.

**Scale.** 11px is retired — it is why the app read as a spreadsheet.

| Role | Size / line height |
|---|---|
| Marginalia, captions | 11.5px |
| UI labels, column headers | 13px, +0.015em tracking |
| Data in columns | 12.5–14px mono, tabular |
| Prose | 15px / 1.55 serif |
| Section heads | 17–20px serif |
| Hanging display figures | 32–56px serif |

Killing card padding, borders and shadows recovers more vertical space than an
11px floor ever saved. Raise the type and take the density back elsewhere.

## Color

Warm-neutral in both modes. Dark mode is a lit reading room, not a blue-black
terminal: no hue shift toward blue anywhere.

### Light

| Token | Hex | Use |
|---|---|---|
| `--paper-desk` | `#E9E6DE` | the surface the page sits on |
| `--paper-page` | `#FBFAF7` | the page |
| `--paper-sunken` | `#F2EFE9` | zebra rows, inset panels |
| `--ink` | `#1E1C19` | ink, ~16:1 |
| `--ink-muted` | `#5C574F` | ~7:1 |
| `--ink-faint` | `#78716A` | ~4.9:1, still AA |
| `--ink-rule` | `#D8D2C7` | hairlines |
| `--ink-line` | `#8A8378` | ~3.1:1, carets, chart axes, control borders |
| `--ink-accent` | `#0F5257` | ~9:1, links, selection, focus |
| `--foreign-ink` | `#8A5A1E` | on `#F5EEE1`, ~5.2:1 |
| `--foreign-paper` | `#F5EEE1` | the other paper |
| `--ink-danger` | `#8C2F22` | exactly one call site |

### Dark

| Token | Hex |
|---|---|
| `--paper-desk` | `#0E0D0C` |
| `--paper-page` | `#1E1D1B` |
| `--paper-sunken` | `#121110` |
| `--ink` | `#EDE8DF` |
| `--ink-muted` | `#A29B90` |
| `--ink-faint` | `#8B8478` |
| `--ink-rule` | `#302E2B` |
| `--ink-line` | `#6E675D` |
| `--ink-accent` | `#4FB3A6` |
| `--foreign-ink` | `#D9A25A` on `#241F17` |
| `--ink-danger` | `#D9705F` |

### What color is allowed to mean

Three things, and nothing else.

1. **Provenance — where a fact came from.** Ink means derived from your own
   baskets. Ochre means purchased from a third party the report does not name.
   Hatched and unfilled means they were asked and did not answer. This is a
   *source* system, not a *severity* system.
2. **Interaction.** What you can act on, what is selected, where focus is.
   Accent only.
3. **Quantity.** Bar length, and one sequential ramp derived from the accent,
   in charts only.
4. **Identity.** A categorical palette of six hues (`--cat-1` … `--cat-6`), used
   so a reader can recognise a recurring thing across views: which store a trip
   was at, which product a series belongs to. The hue is hashed from a stable
   key, never assigned by position, so a store keeps its colour when the list is
   filtered or reordered. Identity colour never encodes rank, quality or
   severity, and a reader must never need it to understand a screen — it is
   recognition, so every use also carries the label in text.

**Forbidden meanings:** good/bad, pass/fail, severity, sentiment, up/down. The
categorical palette does not loosen this: adding hues for identity is not the
same as adding hues for judgement, and the second is what made red meaningless.

**Red is retired** to two call sites, and it means *this is not recoverable*:
confirming deletion of an imported report, and a request that failed. Nothing
else, ever. It previously fired thirty times in a single column, which is how it
stopped meaning anything. The fix was never a different hue — it was deleting
the severity. Grocery prices rising is the subject matter, not an alarm.

Count them before adding a third. Two is already a claim about scarcity that the
next person will test.

**Green is retired** entirely. A loyalty saving is a leading minus sign in ink,
in its own right-aligned column. The minus sign has done that job for four
hundred years, and the column carries the meaning.

**Compliance status is carried by typography and layout**, not by color: a
filled row versus a hatched one, an em-rule where an answer should be, weight,
and words. "Not answered" is a phrase, not a pill.

## Layout

- **Approach:** grid-disciplined within a reading measure.
- **Measure:** the spine's content track caps at **92ch**, and the app does not
  expand past it to fill a 27-inch display. Prose carries its own **62ch** cap
  independently, so the spine only ever has to hold tabular rows. Set at a 76ch
  prose measure first, which truncated the timeline's store column.
- **The margin has a job.** Surplus width becomes a fixed outer margin (11rem)
  carrying marginalia in the mono stack: page references, `source_document_id`,
  locators, section counts as large hanging serif numerals. Footnotes belong in
  the margin.
- **No cards.** Every container is defined by a hairline rule and whitespace.
  Nothing has a border on all four sides.
- **Border radius:** capped at 2px. **Box shadow:** none, anywhere.
- **Change of stock.** When material changes source, change the paper: a
  full-bleed tinted panel that breaks the measure, visibly pasted in.

## Motion

- **Approach:** minimal-functional, to the point of near-absence.
- The only motion in the app is a row unfurling inline: 120ms, height and
  opacity. No entrance animations, no chart draw-ins, no page transitions.
- Under `prefers-reduced-motion` the unfurl is instantaneous. Nothing else
  moves, so there is nothing else to suppress.

## Spacing

- **Base unit:** 4px.
- **Density:** comfortable, not compact. Rows breathe; the space comes from
  deleted chrome, not from smaller type.
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64).

## Constraints that outrank aesthetics

These are enforced by `tests/test_frontend_build.py`. A redesign that loses one
is a regression.

- **No absolute URLs** in `src`/`href`. Zero third-party requests, permanently:
  a request to a third party tells that third party you are reading a report
  about yourself right now.
- **No remote `url()`** in CSS.
- **No font file ships at all**, asserted by
  `test_no_font_files_are_shipped_at_all`. If that ever changes it becomes a
  one-font allowlist; it does not get deleted.
- **Coarse-pointer targets** stay at a 44px minimum.
- **`prefers-reduced-motion`** stays honoured.
- **WCAG AA** on text; **3:1** on UI components such as carets, icons and chart
  axes. Every token above is chosen to clear it.

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-03 | Archival document direction adopted | Memorable thing chosen as "this is your archive". The app already cited its source by page; leaning into the footnote apparatus fixes the hierarchy failures at the root |
| 2026-09-03 | Cards removed in favour of rules and measure | Everything was a white box on off-white, so nothing read as more important than anything else. The tile metaphor also misrepresents continuous data |
| 2026-09-03 | Color restricted to provenance, interaction, quantity | Red had inflated to 30 uses in one column and lost its meaning. Severity theatre removed rather than recoloured |
| 2026-09-03 | Green retired; savings are a minus sign | Colour was doing work punctuation already does |
| 2026-09-03 | One vendored font (Iosevka), serif and sans from the system | Narrowness is a measurable functional gain for numeric columns; nothing else justified adding a file to a zero-file build |
| 2026-09-03 | 11px type floor retired | It was the main reason the app read as a spreadsheet; the space is recovered from deleted chrome |
| 2026-09-03 | Spine measure 76ch → 92ch, prose capped separately at 62ch | A prose measure squeezed the timeline's tabular rows until the store column truncated. Prose was never relying on the spine for its measure |
| 2026-09-03 | Compliance matrix replaced by a per-retailer ruled list | A one-row matrix is a spreadsheet with nothing to compare, and it hid the evidence behind a click. Departs from HANDOFF section 8 |
| 2026-09-03 | Categorical palette added for identity | The app read as monochrome. Six hashed hues for stores and product series give the archive recognition value; severity stays banned, so red is untouched |
| 2026-09-03 | Prices separates priceable products from the rest | A line carries an amount with no quantity and no weight, so 11 of 47 real products were charting quantity buys and per-pound items as inflation |
| 2026-09-03 | Recharts removed | Its category axis drew irregular dates at equal spacing, which on a time series is a correctness bug. Hand-drawn SVG also satisfies "no frame, no gridlines, no axis box" and cut the bundle from 592KB to 208KB |
| 2026-09-04 | Margin 15rem → 11rem, so the 92ch measure is actually delivered | The declared measure was arithmetic that did not close: 92ch + gap-12 + 15rem asks 946px of a 928px shell, and `minmax(0, …)` absorbed the difference in silence. The spine was delivering 640px and basket rows 540px (~75ch) — the width the 76ch→92ch row above was written to escape. A declared measure is a claim about arithmetic, not a setting |
| 2026-09-04 | Display serif varies by operating system; accepted | `ui-serif, Georgia, …` is Georgia on macOS and Windows and the browser's generic serif on most Linux desktops. That face carries the product's voice, so a Linux reader gets a materially different document. Accepted rather than fixed: the only fix is a second font file, and zero-third-party-requests plus the one-font allowlist outrank it. Recorded because the file previously described a face some readers never see |
| 2026-09-04 | Product index: size is quantised, ordinal, and never colour | Five absolute tiers off the existing ladder. Continuous sizing put 28.3% of comparable pairs backwards, because a long name at a small size out-inks a short name at a large one; absolute tiers take that to 0% and remove the degenerate min==max case with it. Colour encodes nothing on that screen: 353 products against six identity hues is noise wearing a palette |
| 2026-09-04 | Iosevka is NOT vendored; the data face is the system mono stack | This file described a vendored Iosevka subset as "the one font file this project ships" and named it in six more places. No such file exists, `--font-mono` is the system stack, and `test_no_font_files_are_shipped_at_all` asserts zero font files. The plan was written up as though executed. Corrected to describe what ships; vendoring stays available and would need the allowlist change named in the constraints |
| 2026-09-04 | Colour tokens renamed to the ones the CSS declares | 11 of the 14 tokens named here did not exist: the tables said `--desk`, `--text`, `--rule`, `--accent`, `--danger`, while `index.css` declares `--paper-desk`, `--ink`, `--ink-rule`, `--ink-accent`, `--ink-danger`. A design system that names variables the code does not have cannot be checked against the code |
| 2026-09-04 | Red goes from one call site to two: delete confirmation, and a failed request | The file said "exactly one call site: confirming deletion of an imported report" and that control did not exist, so the one documented use of red was fiction while the only actual use was `ErrorBox`. Building the delete control made the count real. Both surviving uses mean the same thing — this is not recoverable — which is a meaning worth a colour; severity and sentiment stay banned |
| 2026-09-04 | Index entries are links in a list, never buttons | The coarse-pointer rule sets `button { min-height: 44px }`, with an exemption for `a` inside `p`/`li`/`td`. At 353 entries that choice is the difference between a ~4,300px page and a ~16,700px one on a phone, and links are the correct semantics anyway: they navigate, and they want a real href |
