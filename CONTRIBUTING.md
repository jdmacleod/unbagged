# Contributing

## First, before anything else

```bash
make setup
```

That creates the venv, installs dev dependencies, and — the part that matters — runs
`pre-commit install`. Without the hooks installed, nothing stands between your working
tree and a public commit of someone's home address.

## Never attach a real report to an issue or PR

Not a screenshot of one. Not "just the relevant page". Not in a private fork. The reports
this project reads contain home addresses, phone numbers, loyalty card numbers, email
addresses, and years of itemized purchases. Once a file is in git history it is there
permanently, short of a `git filter-repo` rewrite that breaks every clone.

If your report will not parse, send a skeleton instead:

```bash
unbagged sanitize path/to/report.json -o skeleton.json
```

The skeleton keeps object keys, replaces string values with `<str:len=N>`, buckets numbers
to their order of magnitude, and coarsens dates to the month. That is enough to debug a
parser and not enough to identify you. Read the output before you attach it.

## The safeguards, and why each one exists

| Layer | What it does |
|---|---|
| `.gitignore` | Denies `/data/`, `/output/`, and report formats wholesale. Re-inclusions exist only for `fixtures/` directories. |
| `.dockerignore` | Mirrors it, so real data is never baked into an image layer. `data/` is a bind mount at runtime, never a `COPY`. |
| `tools/no_data_dir.py` | Pre-commit hook that hard-fails any staged path under `data/` or `output/`. |
| `tools/scan_pii.py` | Scans for emails, phone numbers, addresses, ZIPs, Luhn-valid card numbers, SSNs, loyalty-length digit runs, and UUIDs. |
| `gitleaks` | Credentials, which are a different problem with the same blast radius. |
| `check-added-large-files` | A 5 MB PDF appearing in a diff is a red flag. |
| CI | Runs the scanner against the checkout *and* against the PR's commits, so an amended-away mistake is still caught. |

Run `make check-pii` before every commit. Do not bypass the hooks with `--no-verify`.

### Suppressing a false positive

The scanner is tuned to be noisy rather than silent. When it flags something genuinely
benign, annotate the line with a reason:

```python
UNKNOWN_PLACEHOLDER_UPC = "00010000080000"  # pii-scan: allow placeholder UPC, not a card
```

The marker works on the offending line or the line directly above it, so JSON and CSV
fixtures can be annotated from the preceding line. A reason is required — a bare marker
does not suppress anything. Never suppress a whole file, and never weaken a rule to make
CI green.

### The local denylist

The denylist holds literal strings that must never appear in this repository — your
street, your phone number, your loyalty card number, your report reference. Build it
from your own report:

```bash
python tools/build_denylist.py data/incoming/your-report.pdf
make check-pii
```

**It never prints what it finds.** Output is counts and categories only, because a
tool that echoes your address into a terminal has defeated its own purpose: scrollback
gets screenshotted and transcripts get pasted into issues. Read
`tools/denylist.txt` yourself if you want to check it.

Two behaviours worth knowing:

- It asks the adapters for your identifiers first, and only falls back to a regex
  sweep if none returns any. An adapter that knows the format returns exactly the
  values the retailer holds about you; a digit-run sweep over a purchase history
  mostly returns product codes, and a denylist full of UPCs fires on innocent files.
  A scanner people learn to bypass protects nobody.
- Candidates that **already appear in committed files** are dropped, and reported.
  A value sitting in the repository is a format constant or an ordinary word, not
  your secret. If you think one of them really is your data, it is already in git
  history — stop and read the top of this file.

The denylist is gitignored and `build_denylist.py` refuses to write anywhere that is
not. Keep it local, and know that it protects only your machine — the pattern rules
are what protect everyone else's.

## Test data

All test data comes from `tools/make_fixtures.py` (landing at M2). If you need a new
shape, extend the generator rather than hand-writing a fixture from a real report. The
generator is deterministic under a fixed seed, uses reserved-for-fiction values (`555`
phone prefixes, `example.com` domains, addresses that do not resolve), and its output is
verified by the same scanner that guards the rest of the tree.

Screenshots in the README and docs come from synthetic fixtures only.

## Validating against your own report

You will want to. Do it locally:

1. Put the file in `data/incoming/`. It is gitignored, outside the Docker build context,
   and blocked by a pre-commit hook.
2. Never `git add -f` it. Never move it elsewhere in the tree "just to try something".
3. Add its identifying values to `tools/denylist.txt` first, so a copy-paste into a test
   or a commit message gets caught.

If you believe real data has entered the working tree, stop and say so in an issue
without quoting the data. Do not try to rewrite history yourself.

## Adding a retailer adapter

See **`docs/writing-an-adapter.md`**. The short version: implement the
`RetailerAdapter` protocol, emit provenance on every record, never mutate raw
values, treat a missing disclosure section as an explicit `ABSENT` finding rather
than a missing row, degrade with warnings instead of raising, and ship a synthetic
fixture plus a `NOTES.md` documenting the format's quirks. The notes are as
valuable as the code.

Adding a retailer should not require editing anything outside its own package,
apart from one import line in `src/unbagged/adapters/__init__.py`. If it does,
that is a bug in the abstraction — please open an issue.

## Style

`ruff` for lint and format, 100-column lines, Python 3.12. `make lint` and `make test`
before you push.
