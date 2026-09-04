#!/usr/bin/env python3
"""Compare a stored basket against the response it was parsed from.

The app reports, per basket, whether the summed line items match the total the
retailer stated for that basket. When they disagree it cannot say which side is
wrong: the parse may have lost a line, or the retailer's own total may exclude
something it nonetheless itemised. Only the source document settles it.

**This tool reads a real response. Its output is built so it never has to
leave your machine as a liability.** It prints counts, booleans and arithmetic
differences. It never prints a product description, a UPC, an identifier, a
store, a date-with-anything-attached, or any raw text from the document. That
restriction is the reason the tool exists as a script rather than something a
person reads the document to do by hand: the comparison can be made
mechanically, so nothing has to be quoted to make it.

Usage:

    python tools/check_footing.py --report data/incoming/<file> --request 1
    python tools/check_footing.py --report data/incoming/<file> --request 1 \\
        --dates YYYY-MM-DD,YYYY-MM-DD --pattern 'BAG|TAX|FEE|BOTTLE|DEPOSIT'

With no --dates it examines the baskets the app has already flagged, most
divergent first, up to --limit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unbagged import db, views  # noqa: E402
from unbagged.adapters.kroger import reader  # noqa: E402
from unbagged.extraction import extract_pdf, extract_text_file, looks_like_pdf  # noqa: E402

# Line classes whose presence would explain a total that excludes them. Matched
# against descriptions inside this process only; never printed.
DEFAULT_PATTERN = r"BAG|TAX|FEE|DEPOSIT|BOTTLE|CRV|SURCHARGE|RECYCL"

TOLERANCE = 0.01


def _number(value) -> float | None:
    """Amounts arrive as strings, and sometimes as junk."""
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def source_baskets(path: Path) -> dict[str, list[dict]]:
    """Every basket in the response, grouped by its date, straight from the JSON.

    Deliberately re-reads the document rather than trusting the adapter: a tool
    that checks the parse cannot share the parse's assumptions.
    """
    pages = extract_pdf(path) if looks_like_pdf(path) else extract_text_file(path)
    text, _ = reader.strip_page_markers("\n".join(pages))
    blob = reader.blob_with_keys(reader.find_blobs(text), "customer")
    if blob is None:
        raise SystemExit("No purchase blob found in that document.")

    by_date: dict[str, list[dict]] = {}
    for customer in blob.data.get("customer") or []:
        for basket in customer.get("basket") or []:
            raw = basket.get("date")
            if not isinstance(raw, str):
                continue
            # "MM/DD/YYYY 00:00:00" — the real clock lives in a separate field.
            match = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw.strip())
            if not match:
                continue
            month, day, year = match.groups()
            by_date.setdefault(f"{year}-{month}-{day}", []).append(basket)
    return by_date


def compare(basket: dict, stored: dict, pattern: re.Pattern) -> dict:
    items = basket.get("items")
    items = items if isinstance(items, list) else []
    amounts = [_number(i.get("retailamt")) for i in items if isinstance(i, dict)]
    priced = [a for a in amounts if a is not None]

    flagged = [
        _number(i.get("retailamt")) or 0.0
        for i in items
        if isinstance(i, dict) and pattern.search(str(i.get("purchasedescription") or ""))
    ]

    source_sum = round(sum(priced), 2)
    stated = _number(basket.get("total_amount_prior_to_discounts"))
    delta = None if stated is None else round(source_sum - stated, 2)
    fee_sum = round(sum(flagged), 2)

    return {
        "source_lines": len(items),
        "stored_lines": stored["item_count"],
        "lines_match": len(items) == stored["item_count"],
        "unparseable_amounts": len(amounts) - len(priced),
        "source_shelf_sum": source_sum,
        "stored_shelf_total": stored["shelf_total"],
        "shelf_matches": abs(source_sum - stored["shelf_total"]) < TOLERANCE,
        "stated_total_present": stated is not None,
        "stated_matches_stored": (
            stated is not None
            and stored["total_pre_discount"] is not None
            and abs(stated - stored["total_pre_discount"]) < TOLERANCE
        ),
        "delta": delta,
        "fee_like_lines": len(flagged),
        "fee_like_sum": fee_sum,
        # Only meaningful when there is a gap AND something to explain it with.
        # Without both clauses this reads True for every basket that foots, and
        # a flag that is true when nothing happened is worse than no flag.
        "delta_is_the_fee": (
            delta is not None
            and abs(delta) >= TOLERANCE
            and bool(flagged)
            and abs(delta - fee_sum) < TOLERANCE
        ),
    }


def verdict(c: dict) -> str:
    if not c["lines_match"]:
        return "PARSE LOST LINES — the source has more items than the app stored"
    if not c["shelf_matches"]:
        return "PARSE READ AMOUNTS DIFFERENTLY — same line count, different sum"
    if c["delta"] is None:
        return "no stated total in the source to check against"
    if abs(c["delta"]) < TOLERANCE:
        return "foots exactly"
    if c["delta_is_the_fee"] and c["fee_like_lines"]:
        return "RETAILER EXCLUDED AN ITEMISED CHARGE — gap equals the fee lines exactly"
    if c["delta"] > 0:
        return "RETAILER'S TOTAL IS SHORT of its own lines, not explained by fee lines"
    return "STATED TOTAL EXCEEDS THE LINES — source itself carries no line for the difference"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True, type=Path, help="the response to read")
    ap.add_argument("--request", required=True, type=int, help="request id in the database")
    ap.add_argument("--dates", help="comma-separated YYYY-MM-DD; default: the flagged ones")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--pattern", default=DEFAULT_PATTERN)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not args.report.exists():
        raise SystemExit(f"No such file: {args.report}")
    pattern = re.compile(args.pattern, re.IGNORECASE)

    conn = db.connect()
    stored_baskets = views.timeline(conn, args.request)["baskets"]
    if not stored_baskets:
        raise SystemExit(f"Request {args.request} has no baskets.")

    if args.dates:
        wanted = [d.strip() for d in args.dates.split(",") if d.strip()]
        chosen = [b for b in stored_baskets if b["occurred_at"][:10] in wanted]
    else:
        flagged = [
            b for b in stored_baskets
            if b["stated_pre_discount_delta"] is not None
            and abs(b["stated_pre_discount_delta"]) >= TOLERANCE
        ]
        flagged.sort(key=lambda b: -abs(b["stated_pre_discount_delta"]))
        chosen = flagged[: args.limit]

    if not chosen:
        print("Nothing to check: no basket matched.")
        return 0

    from_source = source_baskets(args.report)
    report = []
    for stored in chosen[: args.limit]:
        date = stored["occurred_at"][:10]
        candidates = from_source.get(date, [])
        if len(candidates) != 1:
            report.append({"date": date, "error":
                           f"{len(candidates)} baskets on this date in the source; "
                           "cannot pair them one to one"})
            continue
        entry = {"date": date}
        entry.update(compare(candidates[0], stored, pattern))
        entry["verdict"] = verdict(entry)
        report.append(entry)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"\nChecked {len(report)} basket(s) against {args.report.name}")
    print("Counts, booleans and differences only. No document content is printed.\n")
    for entry in report:
        print(f"  {entry['date']}")
        if "error" in entry:
            print(f"    {entry['error']}\n")
            continue
        print(f"    lines   source {entry['source_lines']:>3}  stored {entry['stored_lines']:>3}"
              f"   match={entry['lines_match']}")
        print(f"    shelf   source-vs-stored match={entry['shelf_matches']}"
              f"   unparseable amounts={entry['unparseable_amounts']}")
        print(f"    stated  present={entry['stated_total_present']}"
              f"  agrees with stored={entry['stated_matches_stored']}")
        print(f"    gap     {entry['delta']:+.2f} (source lines minus source stated total)"
              if entry["delta"] is not None else "    gap     n/a")
        # The pattern is a net, not a classifier: an ordinary product whose name
        # happens to contain one of these words is caught too. A count here is a
        # hint to look, and only "gap==fee" being true is evidence.
        print(f"    fee-like lines {entry['fee_like_lines']}"
              f"  summing {entry['fee_like_sum']:.2f}"
              f"  gap==fee: {entry['delta_is_the_fee']}")
        print(f"    -> {entry['verdict']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
