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

Keys are kept because keys are the retailer's schema, with one exception: a key that is
*itself* an identifier is masked to `<key:len=N>`. That is not a hypothetical. A Kroger
identity blob keys `loyaltyCards` by the card number, so the old skeleton published the
numbers while faithfully masking everything they pointed at. Long digit runs, UUIDs and
email addresses are masked when they appear as keys; `loyaltyCards`, `productupc`, `2024`
and every other field name survives.

## The CHANGELOG is public and permanent

`CHANGELOG.md` describes what changed in the software. It never describes what
was in a report. No dates lifted from a shopping history, no product
descriptions, no UPCs, no amounts, no store numbers.

**Aggregates are allowed, and they are the point.** A ratio or a count taken
over a whole response describes the *shape* of a format, not a person: *0 of 762
product-days carried a repeated UPC*, *68% of products were bought exactly
once*, *20 of 54 baskets did not foot*. Every one of those is a fact about
Kroger's export that a reader can weigh, and none of them narrows down who
shopped. This rule used to ban "counts drawn from one person's response"
outright, which forbade the only evidence that makes a claim about a format
checkable — and which the adapter notes and this changelog had already, rightly,
been relying on.

The line is identifiability, not arithmetic. A count over hundreds of rows is
safe. A count small enough to be about one visit is not: *the basket on the 14th
had 3 items* names a trip. If a number could be paired with a date, a shop or a
product to single out an event, leave it out.

Worth stating separately because a changelog is exactly where the urge to be
concrete bites hardest. "Corrected a misread field" wants an example, and the
nearest example is always the report on your disk. Reach for the shape of the
problem instead: *an item bought at its ordinary price came out free* says
everything a reader needs and names nothing.

The same rule covers commit messages, PR descriptions, code comments, test
names, and docstrings. `make check-pii` will catch an address or a card number;
it will not catch a date you happened to shop on, so this one is on you.

## The safeguards, and why each one exists

| Layer | What it does |
|---|---|
| `.gitignore` | Denies `/data/`, `/output/`, `data.bak-*/`, and sixteen report formats wholesale. Exactly one re-inclusion: `src/**/fixtures/**`. |
| `.dockerignore` | Same bytes, different route: keeps real data out of the build context, not just out of an image layer. Every wildcard is `**/`-prefixed — see below. |
| `tools/no_data_dir.py` | Pre-commit hook that hard-fails any staged path under `data/` or `output/`. |
| `tools/scan_pii.py` | Scans for emails, phone numbers, addresses, ZIPs, Luhn-valid card numbers, SSNs, loyalty-length digit runs, and UUIDs. Also fails on any committed file in a `fixtures/` directory it cannot read. |
| `tools/make_fixtures.py --check` | Regenerates every fixture from a fixed seed and fails on any difference **and on any committed file no generator produces**. |
| `tools/build_brand.py --check` | Rebuilds the served brand assets from `resources/` in memory and compares, writing nothing. Fails on drift, a missing or stray file, a symlink beneath `frontend/public/`, and on bytes that carry anything but pixels — a C2PA manifest, an ICC profile, or a served SVG holding a `<script>` or an off-origin `href`. The odd one out here: it guards provenance metadata and the shape of a served document, not anything about a person's shopping. |
| `tools/check_icon_sync.py` | Refuses a pull request that edits a source SVG in `resources/` and leaves the icons generated from it behind, matched per source: touching a raster belonging to the *other* SVG does not count. Proves they moved together, not that they are correct — nothing re-runs `build_icons.py`, so a wrong regeneration passes. An edit that renders identically leaves nothing to commit; say `icons-unchanged: <source.svg> <reason>` in a commit message. The name and the reason are both required, and the name scopes it to that one file. |
| `docker/requirements.txt` | The shipped image's runtime lock: every package pinned and hashed, installed with `--require-hashes`. `tools/check_lock.py` fails when it stops covering what `pyproject.toml` declares. |
| `gitleaks` | Credentials, which are a different problem with the same blast radius. |
| `tools/make_screenshots.py` | Published screenshots come from a throwaway container seeded only with the synthetic fixture. The scanner cannot read a PNG; this is what stands in for it. |
| `check-added-large-files` | A 5 MB PDF appearing in a diff is a red flag. |
| CI | Runs all of the above against the checkout *and* against the PR's commits, so an amended-away mistake is still caught. Actions are pinned to commit SHAs. |

Run `make check-pii` before every commit. Do not bypass the hooks with `--no-verify`.

### Two things about these layers that are easy to get wrong

**A `fixtures/` directory is the one hole in `.gitignore`, so it is the most
policed place in the repository.** Report formats are denied everywhere except
there, and `scan_pii.py` stands its address-shaped rules down inside a generated
fixtures directory — because byte-identical regeneration from a fixed seed is a
stronger guarantee than any regex. That trade only works if regeneration covers
*everything* committed in the directory. It once compared only the filenames the
generator named, which meant a real report added alongside them reproduced no
check, tripped no rule, and was reported clean by every safeguard here. `--check`
now compares both directions. If you need sample data, add it to
`fixtures/generate.py` and regenerate; do not hand-place a file.

**`.gitignore` and `.dockerignore` look identical and match differently.** A
`.gitignore` pattern with no slash matches at every depth. A `.dockerignore`
pattern is matched against the whole relative path, so a bare `*.pdf` excludes
`./report.pdf` and nothing beneath it. Copying lines across verbatim therefore
protects the root and leaks every subdirectory. Every wildcard in
`.dockerignore` carries a `**/` prefix for that reason, and
`tests/test_packaging.py` fails if one does not.

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

All test data comes from `tools/make_fixtures.py`. If you need a new
shape, extend the generator rather than hand-writing a fixture from a real report. The
generator is deterministic under a fixed seed, uses reserved-for-fiction values (`555`
phone prefixes, `example.com` domains, addresses that do not resolve), and its output is
verified by the same scanner that guards the rest of the tree.

Screenshots in the README and docs come from synthetic fixtures only, and
`make screenshots` is the only way they are made. It starts its own container on a
scratch directory, ingests the fixture and deletes the directory, so it cannot
photograph a real database even by accident. Your own instance on :8420 is
bind-mounted to `./data` and is exactly what must not appear in a screenshot.
The PII scanner cannot read a PNG, so this is the control that replaces it.

It rewrites all five images every run, and the PNG bytes are not stable across
runs even when nothing about the view changed. Compare the pixels before you
commit — `ImageChops.difference(a, b).getbbox()` returning `None` means the only
thing that moved was zlib, and the file should be left alone. Committing those
is churn that makes a real screenshot change hard to spot in a diff.

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

## Cutting a release

Distribution is `git clone` and `docker compose up`, so a release is a tag and a
GitHub release page. Nothing is published to PyPI or a container registry: the
compatibility contract is read against your database rather than a code API, and
a prebuilt image would ask people to trust a binary in a project whose whole
pitch is that they can read every line first.

    git tag -s v0.13.0 -m "unbagged 0.13.0"
    git push origin v0.13.0

That is the whole procedure. `.github/workflows/release.yml` fires on the tag,
runs `tools/release_notes.py`, and creates the release from the CHANGELOG
section for that version.

The notes are **extracted, never retyped**. Release notes written again at tag
time are a second description of the same release, and the two drift — usually
toward the notes being cheerier than the record, because one is written to
announce and the other to remember.

Three ways the workflow refuses, each of which would otherwise publish quietly:

| Refusal | Why it matters |
|---|---|
| the tag names a version `CHANGELOG.md` does not describe | a release nobody can read |
| `VERSION` disagrees with the tag | the app reports `__version__` from that file, so the running software would name a version that was never released |
| the section exists but is empty | same as the first, with a heading |

So bump `VERSION` and write the CHANGELOG section **before** tagging. The tag
ruleset blocks deletion and non-fast-forward updates, so a tag cut from a bad
commit cannot be quietly moved — it has to be superseded by a new version.

### Signatures

Tags are signed, and `.github/allowed_signers` is what lets you check one:

    git config gpg.ssh.allowedSignersFile .github/allowed_signers
    git verify-tag v0.12.0
    # Good "git" signature for ... with ED25519 key SHA256:...

GitHub verifies independently and shows a Verified badge either way. The file
matters for the case where someone has cloned the repository and wants to check
a release without asking GitHub to vouch for it — which is the whole claim this
project makes, so it should be checkable without a third party.

`tools/check_signers.py` runs in CI and asserts every tag still verifies against
that file. It **fails when there are no tags**, rather than verifying an empty
set and reporting success; `actions/checkout` fetches no tags unless asked, and
a check that passes on nothing is the shape this repository has shipped four
times (issue #32).

Rotating a key means **adding** a line, never replacing one. Tags already signed
have to keep verifying, and they can never be re-signed: the tag ruleset blocks
deletion and non-fast-forward. Remove a key and you orphan every tag it signed,
which is exactly what `check_signers.py` will tell you.

To sign as a new maintainer: set `gpg.format ssh` and `user.signingkey` locally,
register the public half on GitHub as a **Signing** key (a separate entry from
an authentication key), and add yourself to `.github/allowed_signers` with
`namespaces="git"`.

## Style

`ruff` for lint and format, 100-column lines, Python 3.12. `make lint` and `make test`
before you push.

Editing the logo or an icon is the one change with a step that is easy to miss.
`resources/README.md` covers it: the sources live there, `frontend/public/` holds
what the app serves, and both have to move together. Running `build_icons.py`
without then running `make brand` leaves the two out of step and turns CI red.
