"""How an Albertsons response writes a date, read into ISO 8601.

Two places carry one: the workbook's date cells, usually Excel serials, and the
report's request block, a US-order stamp with a wall clock. Both are wall clocks
with no zone, and are stored as such. Split out of `adapter.py` for its size.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

#: The request block's submission stamp, in the orders the response prints it.
SUBMITTED = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")
SERIAL = re.compile(r"^\d+(?:\.\d+)?$")


def occurred_on(value: str | None, date1904: bool) -> str | None:
    """A date cell as `YYYY-MM-DD`, or with a clock when the cell has one.

    The observed sheet stores a day count — an Excel date serial — formatted
    to display as a date. A text date is also accepted, ISO or US order,
    because a re-saved workbook may carry either.

    **Date only, no clock.** The response gives a day and nothing else, and
    `T00:00:00` would claim a midnight it never said. The views take the first
    ten characters for a date and print no time when there is none.

    **Never a date that is not one.** A value shaped like a date is built into
    a real one before it is returned, so `2020-13-45` and a serial past the
    year 9999 are refused here rather than stored and crashing whatever reads
    them later. A fraction of a day is rounded to the second on the full
    timestamp, so a hair short of midnight is the next day, not 00:00:00 of
    this one.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        if SERIAL.match(text):
            serial = float(text)
            if serial < 1:
                return None
            # A spreadsheet serial counts days on a wall clock; no zone exists to attach.
            epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)  # noqa: DTZ001
            moment = epoch + timedelta(seconds=round(serial * 86400))
            if moment.time() == datetime.min.time():
                return moment.date().isoformat()
            return moment.isoformat(timespec="seconds")
        if match := ISO_DATE.match(text):
            year, month, day = (int(group) for group in match.groups())
        elif match := US_DATE.match(text):
            month, day, year = (int(group) for group in match.groups())
        else:
            return None
        return date(year, month, day).isoformat()
    except (ValueError, OverflowError):
        return None


def parse_submitted(value: str) -> str | None:
    """`03/02/2026 09:15 AM` → `2026-03-02T09:15:00`. A wall clock, no zone."""
    for pattern in SUBMITTED:
        try:
            moment = datetime.strptime(value.strip(), pattern)  # noqa: DTZ007 - a wall clock
            return moment.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None
