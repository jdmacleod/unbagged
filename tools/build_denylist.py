#!/usr/bin/env python3
"""Build tools/denylist.txt from your own real report.

The denylist is the last line of the PII scanner's defence: literal strings that
must never appear anywhere in this repository. Populating it by hand means
reading your own report and typing your address into a terminal, which is exactly
the moment the address ends up somewhere it should not.

So this does it for you, and **never prints a value it found**. Output is counts
and categories only. Run it, then read `tools/denylist.txt` yourself if you want
to check it — that file is gitignored, and this tool refuses to write to any path
that is not.

    python tools/build_denylist.py data/incoming/report.pdf
    make check-pii

Values are merged with whatever is already in the file, so running it against a
second retailer's response adds to the list rather than replacing it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unbagged.adapters  # noqa: E402,F401  (registers the adapters)
from unbagged.adapters.registry import registry  # noqa: E402
from unbagged.extraction import ExtractionError, extract  # noqa: E402
from unbagged.jsonscan import iter_json, walk  # noqa: E402
from unbagged.models import SourceBundle, SourceDocument  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "tools" / "denylist.txt"

# Short strings match half the repository. A denylist that fires on "Lee" trains
# people to bypass the scanner, which is worse than not having one.
MIN_LENGTH = 6
MIN_DIGITS = 9

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\d)")
DIGIT_RUN = re.compile(r"(?<![\d.])\d{9,20}(?![\d.])")
STREET = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][A-Za-z.'-]*\s+){0,4}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|"
    r"Place|Ter|Terrace|Cir|Circle|Hwy|Highway|Pkwy|Parkway|Trl|Trail|Loop|Row|Walk|"
    r"Mews|Crescent|Alley|Grove|Bend|Rise|Close|Reach|Bank)\b\.?"
)
ZIP_PLUS_FOUR = re.compile(r"(?<!\d)\d{5}-\d{4}(?!\d)")
# An ID-looking token in the source filename: retailers put the report reference
# there, and it is as identifying as anything inside.
FILENAME_TOKEN = re.compile(r"\b[A-Z0-9]{6,}\b")

# JSON keys whose values identify a person, whatever the retailer calls them.
IDENTIFYING_KEY = re.compile(
    r"(name|email|phone|address|city|zip|postal|loyalt|card|account|member|"
    r"household|person|subscriber|customer|alternate|ehhn|epsn|cgperson)",
    re.IGNORECASE,
)
# Keys that look identifying but hold format constants rather than your data.
SKIP_KEY = re.compile(r"(campaign|status|type|description|purchasedescription)", re.IGNORECASE)

# Keys that are *exactly* these hold a field label, not a value. Reports use a
# `{"Name": "SubscriberKey", "Value": "..."}` shape, and the label half matched
# IDENTIFYING_KEY on "name" — which put the retailer's own field names on the
# denylist and made the scanner fire on the adapter that reads them.
# firstName and lastName still match, because they are not exact.
LABEL_KEYS = {"name", "key", "label", "field", "attribute", "property"}

# Never worth denylisting: they appear in ordinary code and prose.
STOPLIST = {
    "unknown", "customer", "california", "united states", "privacy", "kroger",
    "safeway", "albertsons", "hmart", "h mart", "true", "false", "null", "none",
}


TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".md", ".txt", ".sql", ".css", ".json", ".yml", ".yaml",
    ".html", ".toml", ".cfg", ".sh",
}


def repo_corpus() -> str:
    """Everything already committed, lowercased, as one string."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.split()
    chunks = []
    for name in listed:
        path = REPO_ROOT / name
        if path.suffix.lower() in TEXT_SUFFIXES and path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace").lower())
            except OSError:
                continue
    return "\n".join(chunks)


def split_known_values(values: set[str], corpus: str) -> tuple[set[str], set[str]]:
    """Separate candidates that already appear in the repository.

    A value sitting in committed code is not one of your secrets. It is a format
    constant, a field name or an ordinary English word — Kroger's placeholder UPC
    and the word "amount" both turn up in a purchase history, and denylisting
    them makes the scanner fire on 150 innocent lines. A scanner people learn to
    ignore protects nobody, so these are dropped.

    They are counted and reported rather than silently discarded: if one of them
    really is your personal data, it is already committed, and you need to know
    that far more urgently than you need it on a denylist.
    """
    known = {v for v in values if v.lower() in corpus}
    return values - known, known


def is_gitignored(path: Path) -> bool:
    """A denylist that is not gitignored is a PII file waiting to be committed."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)], cwd=REPO_ROOT, capture_output=True
    )
    return result.returncode == 0


def _worth_keeping(value: str) -> bool:
    value = value.strip()
    if len(value) < MIN_LENGTH or value.lower() in STOPLIST:
        return False
    digits = sum(c.isdigit() for c in value)
    if value.replace("-", "").replace(" ", "").isdigit() and digits < MIN_DIGITS:
        return False
    # A value that is mostly punctuation or a single repeated character is noise.
    return len(set(value.strip("0 -"))) > 1


BARE_NUMBER_LINE = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.M)


def from_json_values(text: str, found: dict[str, set[str]]) -> None:
    """Pull identifying values out of any JSON embedded in the report.

    Retailers put the interesting things in structured blobs, and a key named
    `loyaltyno` is a better signal than any regex over prose.

    Tried twice: once on the text as extracted, and once with bare-number lines
    removed, because reports that print page numbers interleave them into the
    middle of their JSON. The permissive strip is fine here — the worst case is
    losing one array element while building a local denylist, which costs
    nothing. An adapter, whose output people rely on, must be stricter.
    """
    seen_spans: set[tuple[int, int]] = set()
    for candidate in (text, BARE_NUMBER_LINE.sub("", text)):
        for start, end, data in iter_json(candidate):
            if (start, end) in seen_spans:
                continue
            seen_spans.add((start, end))
            for key, value in walk(data):
                if (
                    IDENTIFYING_KEY.search(key)
                    and not SKIP_KEY.search(key)
                    and key.strip().lower() not in LABEL_KEYS
                    and _worth_keeping(str(value))
                ):
                    found["identifying JSON fields"].add(str(value).strip())


def from_adapter(report: Path, found: dict[str, set[str]]) -> bool:
    """Ask the adapters for the identifiers, rather than guessing at them.

    This is far better than a regex sweep. An adapter that knows the format
    returns exactly the values the retailer holds about you, and nothing else —
    where a digit-run regex over a purchase history mostly returns product
    codes. A denylist full of UPCs fires on innocent files, and a scanner people
    learn to bypass protects nobody.

    Returns whether an adapter actually produced identifiers — not merely whether
    one ran. An adapter can parse a report's purchases perfectly and still find no
    identity graph, because the retailer changed that part of the format. Trusting
    "it parsed" over "it found something" produced an empty denylist the first time
    this met a real report.
    """
    document = SourceDocument(report.name, sha256="", path=str(report))
    match = registry.select(SourceBundle(documents=(document,)))
    if match is None or match.is_fallback:
        return False
    try:
        result = match.adapter.parse(SourceBundle(documents=(document,)))
    except Exception:
        return False

    for identity in result.identities:
        if _worth_keeping(identity.value):
            found["identifiers the adapter found"].add(identity.value.strip())
        # An address is worth denylisting in pieces too: it is quoted in bug
        # reports one line at a time.
        for part in re.split(r",\s*", identity.value):
            if _worth_keeping(part):
                found["identifiers the adapter found"].add(part.strip())
    if result.request.report_reference and _worth_keeping(result.request.report_reference):
        found["report references"].add(result.request.report_reference.strip())
    return bool(found["identifiers the adapter found"])


def harvest(text: str, filename: str, *, sweep: bool = True) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {
        "email addresses": set(),
        "phone numbers": set(),
        "street addresses": set(),
        "ZIP+4 codes": set(),
        "long digit runs": set(),
        "identifying JSON fields": set(),
        "identifiers the adapter found": set(),
        "report references": set(),
    }
    for token in FILENAME_TOKEN.findall(Path(filename).stem):
        if _worth_keeping(token):
            found["report references"].add(token)
    if not sweep:
        # An adapter already supplied the identifiers precisely. Sweeping as
        # well would bury them under every product code in the report.
        return found

    for pattern, bucket in (
        (EMAIL, "email addresses"),
        (PHONE, "phone numbers"),
        (STREET, "street addresses"),
        (ZIP_PLUS_FOUR, "ZIP+4 codes"),
        (DIGIT_RUN, "long digit runs"),
    ):
        for match in pattern.finditer(text):
            if _worth_keeping(match.group(0)):
                found[bucket].add(match.group(0).strip())

    from_json_values(text, found)
    return found


def merge(output: Path, values: set[str]) -> tuple[int, int]:
    existing: set[str] = set()
    header = [
        "# Literal strings that must never appear in this repository.",
        "# Generated by tools/build_denylist.py; add your own below.",
        "# This file is gitignored, and must stay that way: it is a PII file.",
        "",
    ]
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                existing.add(stripped)
    combined = existing | values
    output.write_text(
        "\n".join(header + sorted(combined, key=lambda v: (-len(v), v))) + "\n",
        encoding="utf-8",
    )
    return len(existing), len(combined)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if not is_gitignored(args.output):
        print(
            f"Refusing to write {args.output}: it is not gitignored.\n"
            "The denylist holds your real personal data. Add it to .gitignore first.",
            file=sys.stderr,
        )
        return 1

    totals: Counter[str] = Counter()
    values: set[str] = set()
    for report in args.reports:
        document = SourceDocument(report.name, sha256="", path=str(report))
        try:
            text = extract(document).text
        except ExtractionError as exc:
            print(f"  skipped {report.name}: {exc}", file=sys.stderr)
            continue
        found: dict[str, set[str]] = {}
        parsed = from_adapter(report, found := {
            "email addresses": set(), "phone numbers": set(),
            "street addresses": set(), "ZIP+4 codes": set(),
            "long digit runs": set(), "identifying JSON fields": set(),
            "identifiers the adapter found": set(), "report references": set(),
        })
        swept = harvest(text, report.name, sweep=not parsed)
        for bucket, items in swept.items():
            found.setdefault(bucket, set()).update(items)
        if parsed:
            print(f"  {report.name}: read by an adapter, identifiers taken directly")
        else:
            print(
                f"  {report.name}: no adapter returned identifiers, sweeping the "
                "text instead (expect product codes among the results)"
            )
        # Only ever the count. The whole point of this tool is that the values
        # go to a gitignored file and nowhere a terminal or a transcript keeps.
        print(f"  {report.name}: {len(text):,} characters of text")
        for bucket, items in sorted(found.items()):
            if items:
                print(f"      {len(items):>4}  {bucket}")
            totals[bucket] += len(items)
            values |= items

    if not values:
        print("\nNothing found to denylist.", file=sys.stderr)
        return 1

    values, already_present = split_known_values(values, repo_corpus())
    if already_present:
        print(
            f"\n  {len(already_present)} candidate(s) already appear in committed "
            "files, so they are format constants rather than your data. Dropped."
        )
        print(
            "  If you believe one of them really is personal data, it is already "
            "in git history. Stop and read CONTRIBUTING.md."
        )
    if not values:
        print("\nNothing left to denylist after that.", file=sys.stderr)
        return 1

    before, after = merge(args.output, values)
    try:
        where = args.output.relative_to(REPO_ROOT)
    except ValueError:
        where = args.output
    print(f"\n  {after - before} new entries, {after} total in {where}")
    print("  No values were printed. Read that file yourself if you want to check it.")
    print("\nNow run:  make check-pii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
