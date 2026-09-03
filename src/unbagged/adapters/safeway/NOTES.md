# Safeway (Albertsons) response format

**No real response has been seen.** Everything below is expectation, not
observation, and should be deleted or corrected the moment one arrives. Nothing
here is evidence.

## What to expect

Albertsons has historically returned **a zip of CSVs plus a separate categories
letter**. If that holds, this adapter is shaped differently from Kroger's:

- **A bundle, not a document.** `SourceBundle.documents` will carry several
  files. The purchase data and the statutory disclosures live in different ones,
  so `parse()` has to work across the bundle rather than picking one report.
- **CSV, not embedded JSON.** No page-number interleaving, no brace scanning.
  Column headers become the format's contract, and they will need recording here
  the first time they are seen.
- **Disclosures as prose.** This is the interesting part. Kroger's omissions are
  structural — there is simply no Section 2. Albertsons is likely to *answer* the
  categories, in paragraphs, in a letter. Turning that into `PROVIDED` /
  `PARTIAL` / `ABSENT` is a judgment a keyword match cannot make honestly.

  The generic fallback's rule applies here and should be inherited: wording
  matching a category earns `PARTIAL` with the sentence quoted, never
  `PROVIDED`. If the compliance view needs better than that, the answer is
  operator confirmation — show the quoted sentence and let the user mark the cell
  — not a more confident regex.

## Open questions for whoever has a real response

- Does the zip contain one CSV per data category, or one wide file?
- Is there a loyalty-card identifier graph comparable to Kroger's eight keys?
- Are inferred or appended attributes disclosed at all? Kroger's demographic
  block is the most interesting thing in its response; if Albertsons omits an
  equivalent, that omission belongs in the matrix.
- Is the coverage window stated, and is a supplemental period offered?

## Before writing this adapter

1. Read `docs/writing-an-adapter.md`.
2. Build `fixtures/generate.py` from the *structure* of the real response and
   never from its values. Validate against your own report locally, in
   `data/incoming/`, which is gitignored.
3. Replace this file with what you actually saw.
