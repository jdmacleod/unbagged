"""Generate a structurally faithful, entirely fabricated Albertsons response.

Seeded from the *structure* of one real response, never from its values. Every
value here is invented; nothing in this file was copied from a real report, and
the instructions prose is a paraphrase written to carry the same three kinds of
sentence the adapter quotes, not the retailer's wording.

The response is two files and this produces both:

* ``synthetic_report.txt`` — the report PDF **as pdfplumber extracts it**, which
  is the form the adapter reads. Committed, because it is text.
* the workbook, built on demand by ``build_workbook(seed)`` for tests and never
  committed. An ``.xlsx`` is a zip the PII scanner cannot read, and
  ``tests/test_scan_pii.py`` already names this directory's ``response.xlsx`` as
  a finding. A binary fixture nobody reviews is the accident this repository
  exists to prevent, so the bytes are produced where they are used.

What the report reproduces, because the reader has to survive it:

* **bold text doubled character by character** — every heading and every
  request label — while values beside them are not
* the request block's **two label/value pairs per line**
* a ``Key value`` row per field, and ``I/ss`` printed for three of the name
  fields
* the profile table **breaking across the page footer** ``1 / 2``
* the preference cell's four ``name:value`` lines **split around its row
  label**, twice
* section headings scoped ``[cc:<card>]`` and ``[hh:<household>]``, one with a
  space after the colon
* to exercise the two follow-ups the adapter raises: a requester whose first
  name is not the profile holder's, and a points event and preferences dated
  after the last purchase

What the workbook reproduces:

* a merged title row, a blank row, then the header — so the header is row 3
* dates as **Excel serials**, identifiers and amounts as numeric cells, text in
  the shared-string table
* amounts carrying **binary-float artefacts** (``3.4499999999999997``),
  **scientific notation** for small ones (``-4.1E-3``) and **fractions of a
  cent** where a basket-level markdown was spread across its lines
* weighed lines with ``LBS``, markdown lines with ``QTY`` 0 and a negative
  ``NET``, coupon lines at zero, department-key rings with a one-digit UPC
* a demographic sheet of label/value rows for one card, sorted by label

Scale differs from the reference response on purpose, as the other generators'
does, so the fixture cannot be mistaken for a reproduction of it.

Output is deterministic: the same seed produces byte-identical text, and the
same workbook bytes on the same zlib.
"""

from __future__ import annotations

import io
import random
import re
import uuid
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from tools.receiptimage import luhn_ok

DEFAULT_SEED = 20261020
REPORT = "synthetic_report.txt"

TRANSACTION_SHEET = "Albertsons Transactions"
DEMOGRAPHIC_SHEET = "Demographic Data"
TITLE = "SALES/TRANSACTION HISTORY"
HEADERS = (
    "TXN_DTE",
    "TXN_ID",
    "STORE_ID",
    "CARD_NBR",
    "CATEGORY_NM",
    "UPC_ID",
    "UPC_DSC",
    "QTY",
    "LBS",
    "NET",
)

# Stores that do not exist, by a made-up four-digit number. A few are used
# often and the rest rarely, which is what gives a store view something to rank.
STORES = ("4417", "4302", "3981", "4120", "3655", "4588")
STORE_WEIGHTS = (0.5, 0.3, 0.1, 0.05, 0.03, 0.02)

# Invented brands and items, crossed into a catalogue large enough for the
# basket sizes below: a basket draws products without replacement, and the
# longest baskets run to dozens of lines. Every name is made up.
BRANDS = (
    "OAKVALE",
    "BRAMBLEFORD",
    "KESTREL",
    "HOLLOWAY",
    "SUNWARD",
    "TIDEMARK",
    "PEBBLEWORTH",
    "MARLOWE",
    "CINDERLY",
    "FERNHAVEN",
    "QUILLON",
    "ASHCOMBE",
)
ITEMS = (
    # (item, category, base price)
    ("MILK 2% REDUCED FAT GAL", 30, 4.29),
    ("SOURDOUGH ROUND", 2, 4.99),
    ("PASTA PENNE RIGATE", 1, 1.15),
    ("YOGURT GREEK VANILLA", 30, 1.25),
    ("SPARKLING WATER LIME", 20, 0.89),
    ("CEREAL OAT CLUSTERS", 1, 4.69),
    ("PAPER TOWELS 6 ROLL", 15, 8.99),
    ("SUNSCREEN SPF 50", 58, 9.49),
    ("SALSA MEDIUM", 10, 3.99),
    ("COFFEE MEDIUM ROAST", 1, 8.79),
)


def _catalogue() -> tuple[tuple[str, str, int, float], ...]:
    """Every (brand, item) pair as (description, UPC, category, price).

    Deterministic without a seed: the catalogue is structure, and the seed
    decides only what is bought from it. UPC widths vary between 10 and 11
    digits, as a numeric cell that dropped its leading zero leaves them.
    """
    products = []
    for b, brand in enumerate(BRANDS):
        for i, (item, category, price) in enumerate(ITEMS):
            width = 11 if (b + i) % 3 == 0 else 10
            upc = str(10000 + (b + 1) * 1000 + i * 37).ljust(width, str((b + i) % 10))
            # Brands sit a little above or below each other, so one item is
            # not one price across the store.
            products.append((f"{brand} {item}", upc, category, round(price * (0.85 + b * 0.03), 2)))
    return tuple(products)


# Invented products: (description, UPC as the sheet holds it, category, price).
PRODUCTS = _catalogue()

# Sold by weight: (description, UPC, category, price per pound). Produce codes
# are four digits, as a price-lookup ring is.
WEIGHED = (
    ("BANANAS YELLOW", "4011", 10, 0.69),
    ("APPLES HONEYCRISP", "3283", 10, 2.49),
    ("PEARS BARTLETT", "4409", 10, 1.79),
    ("ONIONS YELLOW", "4093", 10, 1.19),
    ("TOMATOES ON THE VINE", "4664", 10, 2.99),
    ("CHICKEN THIGHS BONELESS", "21183000000", 59, 3.99),
    ("GROUND BEEF 85% LEAN", "21290000000", 59, 5.49),
    ("SALMON FILLET ATLANTIC", "26150000000", 57, 9.99),
)

#: The proportions the composer is tuned to. Round, invented figures for a
#: plausible grocery history — a handful of lines a trip, most bought singly,
#: weighed produce common, markdowns rare — and not any household's.
#: `tests/test_fixture_shape.py` holds the fixture to these, so a change to the
#: composer that drifts from them is a failing test rather than a quietly
#: unrepresentative fixture.
TARGETS = {
    "lines_per_basket_median": 8,
    "lines_per_basket_p90": 20,
    "quantity_share": {1: 0.7, 2: 0.2, 3: 0.05, 4: 0.03},
    "weighed_line_share": 0.1,
    "baskets_with_weighed_share": 0.5,
    "allocated_markdown_basket_share": 0.03,
    "negative_line_share": 0.01,
    "zero_line_share": 0.005,
    "top_store_line_shares": (0.5, 0.3, 0.1),
}

DEPARTMENTS = ("PRODUCE", "DELI", "MEAT", "SNACKS", "BAKERY PKG")

#: Container deposits as the register rings them (#140): the charge word, the
#: container, a pack size, then a redemption-rate code, a tax flag and the
#: food-stamp flag. (description, UPC, category, amount, share of baskets.) The
#: first is common enough to reach the top size tier, which is where a deposit
#: listed as a product did the most damage.
DEPOSITS = (
    ("CRV SOFT DRINKS  6 PACK       R1 TAX  FS", "8810000601", 20, 0.30, 0.16),
    ("CRV BEER 12 PACK              R1 TAX NFS", "8810001201", 20, 0.60, 0.05),
    ("CRV GROCERY  SINGLE           R2 NTX  FS", "8810000102", 20, 0.10, 0.04),
    ("DEPOSIT OR SOFT DRINKS  SINGLE        FS", "8810000199", 20, 0.10, 0.02),
)

# Words that print in place of a name the profile holds nothing for.
PLACEHOLDER = "I/ss"

FAMILY = "Thistlewaite"
REQUESTER = "Morgan"
HOLDER = "Avery"
MIDDLE = "R"
STREET = "Larkspur Hollow Way"
STATE = "CA"
ZIP = "99999-0101"
PHONE = "3105550142"

# Fabricated attributes for the demographic sheet: the retailer's label shapes,
# invented values. Sorted at write time, as the observed sheet was.
DEMOGRAPHICS = (
    ("Age", "47"),
    ("Year of Birth", "1978"),
    ("Gender", "Male"),
    ("Family Position", "Adult"),
    ("Marital Status", "Married"),
    ("Education Level", "Completed College"),
    ("Occupation", "Professional/Technical"),
    ("Household Income", "$100,000 - $124,999"),
    ("Net Worth", "$250,000 - $499,999"),
    ("Wealth Rating Relative to Other Households (10-lowest to 1-highest)", "3rd decile"),
    ("Number of Persons in Household", "Four persons"),
    ("Consumer Num Of Adults", "2"),
    ("Home Ownership Status", "Home Owner"),
    ("Length of Residence (in years)", "11"),
    ("Median Home Value (in thousands)", "512"),
    ("Census Tract", "1207"),
    ("County Code", "019"),
    ("Consumer Dwelling Type", "Single Family"),
    ("Heavy Buyer of Fresh Products", "Yes"),
    ("Heavy Buyer of Sunscreen / Tanning Products", "Yes"),
    ("Health and Beauty Product Purchase", "Yes"),
    ("Consumer Internet Buyer", "Yes"),
    ("Interest in Gourmet Cooking", "Yes"),
    ("Purchased Books Or Magazines Via Mail", "Yes"),
    ("Premium Visa Or MasterCard Card", "Yes"),
    ("Owns Investments", "Yes"),
)

#: The scanner's card rule, restated so the generator can refuse its own
#: output. Every 13-19 digit run, dashes and spaces allowed between digits.
CARD_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------


def _digits(rng: random.Random, width: int, lead: str = "") -> str:
    return lead + "".join(rng.choice("0123456789") for _ in range(width - len(lead)))


def _failing(rng: random.Random, width: int, lead: str = "") -> str:
    """A digit run of `width` that FAILS a Luhn check, by construction."""
    while True:
        candidate = _digits(rng, width, lead)
        if not luhn_ok(candidate):
            return candidate


def _household(rng: random.Random) -> str:
    """Twelve digits, chosen so the two ids derived from it also fail Luhn.

    The format builds its alternate id and its preference customer id out of
    the household id with a prefix, so all three have to be checked together.
    """
    while True:
        household = _digits(rng, 12, "7")
        derived = (f"7300000{household}", f"7300000{household}")
        if not any(luhn_ok(value) for value in derived):
            return household


def _excel(value: float) -> str:
    """A double as Excel writes it into a numeric cell.

    The shortest round-trip form, no trailing `.0` on a whole number, and
    E-notation below a tenth — which is how `-4.1E-3` and
    `6.0000000000000005E-2` reach a reader that has to parse them.
    """
    text = repr(value)
    if text.endswith(".0"):
        text = text[:-2]
    if value and abs(value) < 0.1 and "e" not in text:
        sign, digits, exponent = Decimal(text).as_tuple()
        head, rest = str(digits[0]), "".join(str(d) for d in digits[1:]).rstrip("0")
        power = exponent + len(digits) - 1
        text = f"{'-' if sign else ''}{head}{'.' + rest if rest else ''}E{power}"
    return text.replace("e-0", "E-").replace("e-", "E-")


def _serial(day: date) -> int:
    return (day - date(1899, 12, 30)).days


def _double(text: str) -> str:
    """Bold as the text layer reads it: every character twice."""
    return " ".join("".join(ch * 2 for ch in token) for token in text.split())


# --------------------------------------------------------------------------
# The model both files are written from
# --------------------------------------------------------------------------


def build(seed: int = DEFAULT_SEED) -> dict:
    rng = random.Random(seed)
    card = _failing(rng, 14, "4")
    household = _household(rng)
    customer = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    reference = "R" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(9))

    # Invented. A fabricated date that happens to match one in a maintainer's
    # own response is caught by the denylist, which lists every visit date.
    start = date(2017, 5, 8)
    end = date(2021, 11, 15)
    span = (end - start).days
    days = sorted({start + timedelta(days=rng.randrange(span + 1)) for _ in range(175)})
    days[0], days[-1] = start, end

    baskets = []
    for day in days:
        store = rng.choices(STORES, STORE_WEIGHTS)[0]
        txn_id = _failing(rng, 13, store[:2])
        lines = _basket(rng)
        baskets.append({"day": day, "store": store, "txn_id": txn_id, "lines": lines})
    _add_deposits(baskets, random.Random(f"deposits-{seed}"))

    return {
        "card": card,
        "household": household,
        "customer": customer,
        "reference": reference,
        "baskets": baskets,
        "street_number": str(rng.randrange(100, 9900)),
        "points_txn": _failing(rng, 17, baskets[-1]["store"]),
        "points_day": date(2024, 6, 14),
        "preference_day": date(2023, 8, 3),
    }


def _add_deposits(baskets: list[dict], rng: random.Random) -> None:
    """Ring container deposits into some baskets.

    From a stream of their own, so adding them moved nothing the main stream
    draws: every other line, total and identifier is as it was before #140.
    """
    for basket in baskets:
        for description, upc, category, amount, share in DEPOSITS:
            if rng.random() < share:
                basket["lines"].append((description, upc, category, 1, 0, amount))


def _basket(rng: random.Random) -> list[tuple[str, str, int, float, float, float]]:
    """One basket's lines: (description, upc, category, qty, lbs, net).

    Drawn without replacement, because the format lists a product once per
    basket with its quantity, rather than once per item. Amounts are computed
    in binary floating point and written as computed, which is where the
    artefacts come from.

    Sizes are heavy-tailed: most trips are a handful of lines and a few are
    dozens. A log-normal gets both.
    """
    count = max(1, min(45, round(rng.lognormvariate(2.05, 0.75))))
    weighed = 0
    if rng.random() < 0.5:
        weighed = min(count, rng.choices((1, 2, 3, 4, 5), (0.35, 0.25, 0.2, 0.12, 0.08))[0])
    lines = []
    for description, upc, category, price in rng.sample(PRODUCTS, k=count - weighed):
        quantity = rng.choices((1, 2, 3, 4, 5), (0.7, 0.2, 0.05, 0.03, 0.02))[0]
        lines.append([description, upc, category, quantity, 0, price * quantity])
    for description, upc, category, per_pound in rng.sample(WEIGHED, k=min(weighed, len(WEIGHED))):
        pounds = round(rng.uniform(0.4, 3.2), 2)
        # The register rounds a weighed line to the cent; only an allocated
        # markdown leaves a fraction of one.
        lines.append([description, upc, category, 1, pounds, round(per_pound * pounds, 2)])

    if rng.random() < 0.03 and lines:
        # A basket-level markdown spread across its lines: each line's NET
        # carries its share to four places, which is fractions of a cent.
        total = sum(line[5] for line in lines)
        markdown = round(rng.uniform(0.5, 3.0), 2)
        for line in lines:
            line[5] = round(line[5] - markdown * line[5] / total, 4)
    if rng.random() < 0.08:
        department = rng.choice(DEPARTMENTS)
        lines.append(
            [f"GROC DEPT KEY MKDN REALLOC {department}", "9", 90, 0, 0, -rng.uniform(0.001, 0.09)]
        )
    if rng.random() < 0.05:
        lines.append(["50% OFF MEAT INSTANT SAVINGS", "0", 59, 0, 0, -round(rng.uniform(2, 6), 2)])
    if rng.random() < 0.03:
        lines.append(["CPN KESTREL PASTA ANY 2", "8810099901", 1, 1, 0, 0.0])
    if rng.random() < 0.03:
        lines.append(["DEPARTMENT KEY SALES GROCERY MAIN MEAL", "8", 92, 1, 0, 2.5])
    return [tuple(line) for line in lines]


# --------------------------------------------------------------------------
# The report, as extracted text
# --------------------------------------------------------------------------


def report_text(model: dict) -> str:
    baskets = model["baskets"]
    first, last = baskets[0], baskets[-1]
    counts: dict[str, int] = {}
    spend: dict[str, float] = {}
    for basket in baskets:
        counts[basket["store"]] = counts.get(basket["store"], 0) + 1
        spend[basket["store"]] = spend.get(basket["store"], 0) + sum(x[5] for x in basket["lines"])
    most_used = max(counts, key=counts.get)
    most_spend = max(spend, key=spend.get)
    card, household = model["card"], model["household"]
    alternate = f"730-000-0{household}"
    os_customer = f"7300000{household}"
    points = model["points_day"].isoformat()
    preference = model["preference_day"].isoformat()

    page_one = [
        _double("Request details"),
        f"{_double('Request ref ID')} {model['reference']} {_double('Request type')} "
        "Request for personal information",
        f"{_double('Date submitted')} 03/02/2026 09:15 AM {_double('Subject type')} Consumer",
        f"{_double('First name')} {REQUESTER} {_double('Last name')} {FAMILY}",
        _double("Instructions"),
        _double("A Guide to Your Data"),
        "This file sets out the personal data we hold about you under several headings, each of",
        "which stands for one of the categories of data collected (for instance, contact details",
        "and purchases).",
        "Some information is exempt from access requests and is not included here. Exempt",
        "information includes health information held by our pharmacy. We also do not include",
        "information that would reveal another person's data, such as support conversations or",
        "email exchanges with us.",
        _double("What if I Have Questions"),
        "• By email via this address: privacy@example.com",
        "• By phone at (800) 555-0100 (select option 2)",
        "• Online through our Contact Form at this address: https://example.com/privacy",
        "• Via Customer Support Center for general inquiries at (800) 555-0199",
        _double("Detailed Report"),
        _double(f"Loyalty Store Visits Details [cc:{card}]"),
        f"Card_first_store_used_id {first['store']}",
        f"Card_first_visit_dt {first['day'].isoformat()}",
        f"Card_last_store_used_id {last['store']}",
        f"Card_last_visit_dt {last['day'].isoformat()}",
        f"Card_nbr {card}",
        f"Customer_id {model['customer']}",
        # A space after the colon, as one of the two observed headings has.
        _double("Loyalty Profile Details [cc:") + " " + _double(f"{card}]"),
        f"Address {model['street_number']} {STREET.upper()}",
        f"Alternate_id_txt {alternate}",
        f"Customer_first_used_store_nbr {first['store']}",
        f"Customer_first_visit_dt {first['day'].isoformat()}",
        f"Customer_id {model['customer']}",
        f"Customer_last_used_store_nbr {last['store']}",
        "1 / 2",
    ]
    page_two = [
        f"Customer_last_visit_dt {last['day'].isoformat()}",
        f"Customer_most_spend_store_nbr {most_spend}",
        f"Customer_most_used_store_nbr {most_used}",
        f"First_nm {HOLDER.upper()}",
        f"Formatted_nm {PLACEHOLDER}",
        "Head_household_ind True",
        f"Household_id {household}",
        f"Last_nm {FAMILY.upper()}",
        f"Loyalty_program_card_nbr {card}",
        f"Maiden_nm {PLACEHOLDER}",
        "Mail_allowed_ind True",
        f"Middle_nm {MIDDLE}",
        f"Nick_nm {PLACEHOLDER}",
        f"Phone {PHONE}",
        f"Postal_zone_cd {ZIP}",
        f"State_cd {STATE}",
        _double("Preference Details"),
    ]
    for number, code in enumerate(("MAIL-OFFERS", "PROMO-ALL"), start=1):
        # Four lines in one cell, with the row label landing between the
        # second and third, as the vertically centred label does.
        page_two += [
            f"preference_cd:{code}",
            f"os_cust_id:{os_customer}",
            f"Customer Preference {number}",
            f"first_eff_ts:{preference}T10:22:41.518000+00:00",
            "last_eff_ts:9999-12-31T08:00:00+00:00",
        ]
    page_two += [
        _double(f"Loyalty Earn Burn Details [hh:{household}]"),
        "Burn_points 0",
        "Earn_points 3",
        f"Household_id {household}",
        f"Last_update_ts {points}T17:40:09.311000",
        "Loyalty_program_cd SUMMER_GAME_2024_01",
        "Reward_origin_cd GROCERY",
        f"Transaction_id {model['points_txn']}",
        "2 / 2",
    ]
    return "\n".join(page_one + page_two) + "\n"


# --------------------------------------------------------------------------
# The workbook
# --------------------------------------------------------------------------


class _Strings:
    """The shared-string table, in order of first use, as Excel writes it."""

    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.uses = 0

    def __call__(self, text: str) -> int:
        self.uses += 1
        return self.index.setdefault(text, len(self.index))

    def xml(self) -> str:
        items = "".join(f"<si><t>{escape(text)}</t></si>" for text in self.index)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<sst xmlns="{MAIN}" count="{self.uses}" uniqueCount="{len(self.index)}">'
            f"{items}</sst>"
        )


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
LETTERS = "ABCDEFGHIJ"


def _string_cell(ref: str, strings: _Strings, text: str, style: int = 0) -> str:
    return f'<c r="{ref}" s="{style}" t="s"><v>{strings(text)}</v></c>'


def _number_cell(ref: str, value: str, style: int = 0) -> str:
    return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'


def _transactions_xml(model: dict, strings: _Strings) -> str:
    rows = [
        '<row r="1">'
        + _string_cell("A1", strings, TITLE, 3)
        + "".join(f'<c r="{col}1" s="3"/>' for col in LETTERS[1:])
        + "</row>",
        '<row r="2">' + "".join(f'<c r="{col}2" s="1"/>' for col in LETTERS) + "</row>",
        '<row r="3">'
        + "".join(
            _string_cell(f"{col}3", strings, h, 2) for col, h in zip(LETTERS, HEADERS, strict=True)
        )
        + "</row>",
    ]
    number = 3
    for basket in model["baskets"]:
        for description, upc, category, quantity, pounds, net in basket["lines"]:
            number += 1
            cells = (
                _number_cell(f"A{number}", str(_serial(basket["day"])), 4),
                _number_cell(f"B{number}", basket["txn_id"], 5),
                _number_cell(f"C{number}", basket["store"], 5),
                _number_cell(f"D{number}", model["card"], 5),
                _number_cell(f"E{number}", str(category)),
                _number_cell(f"F{number}", upc, 5),
                _string_cell(f"G{number}", strings, description),
                _number_cell(f"H{number}", str(quantity), 6),
                _number_cell(f"I{number}", _excel(float(pounds)), 7),
                _number_cell(f"J{number}", _excel(float(net)), 8),
            )
            rows.append(f'<row r="{number}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}">'
        f'<dimension ref="A1:J{number}"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="14.5"/>'
        f"<sheetData>{''.join(rows)}</sheetData>"
        '<mergeCells count="1"><mergeCell ref="A1:J1"/></mergeCells>'
        "</worksheet>"
    )


def _demographics_xml(model: dict, strings: _Strings) -> str:
    rows = [
        '<row r="1">'
        + "".join(
            _string_cell(f"{col}1", strings, h, 2)
            for col, h in zip("ABC", ("Related Card", "Inferred Description", "Value"), strict=True)
        )
        + "</row>"
    ]
    for number, (label, value) in enumerate(sorted(DEMOGRAPHICS), start=2):
        rows.append(
            f'<row r="{number}">'
            + _string_cell(f"A{number}", strings, model["card"])
            + _string_cell(f"B{number}", strings, label)
            + _string_cell(f"C{number}", strings, value)
            + "</row>"
        )
    last = len(DEMOGRAPHICS) + 1
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}">'
        f'<dimension ref="A1:C{last}"/>'
        f"<sheetData>{''.join(rows)}</sheetData>"
        "</worksheet>"
    )


def _package(model: dict) -> dict[str, str]:
    strings = _Strings()
    transactions = _transactions_xml(model, strings)
    demographics = _demographics_xml(model, strings)
    prolog = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    ct = "application/vnd.openxmlformats-officedocument"
    return {
        "[Content_Types].xml": prolog
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{ct}.spreadsheetml.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{ct}.spreadsheetml.'
        'worksheet+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="{ct}.spreadsheetml.'
        'worksheet+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{ct}.spreadsheetml.styles+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="{ct}.spreadsheetml.'
        'sharedStrings+xml"/></Types>',
        "_rels/.rels": prolog + f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>",
        # The demographic sheet's sheetId is 3, not 2, as observed: ids are not
        # positions, and a reader that assumed so would look for a sheet that
        # is not there.
        "xl/workbook.xml": prolog + f'<workbook xmlns="{MAIN}" xmlns:r="{REL}">'
        '<workbookPr defaultThemeVersion="166925"/><sheets>'
        f'<sheet name="{TRANSACTION_SHEET}" sheetId="1" r:id="rId1"/>'
        f'<sheet name="{DEMOGRAPHIC_SHEET}" sheetId="3" r:id="rId2"/>'
        "</sheets></workbook>",
        # Relationship ids out of order, as observed.
        "xl/_rels/workbook.xml.rels": prolog + f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/>'
        f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId5" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>'
        f'<Relationship Id="rId4" Type="{REL}/styles" Target="styles.xml"/>'
        "</Relationships>",
        "xl/worksheets/sheet1.xml": transactions,
        "xl/worksheets/sheet2.xml": demographics,
        "xl/styles.xml": prolog + f'<styleSheet xmlns="{MAIN}"><numFmts count="2">'
        '<numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/>'
        '<numFmt numFmtId="166" formatCode="mm/dd/yyyy"/></numFmts>'
        '<fonts count="1"><font><sz val="11"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0"/></cellStyleXfs><cellXfs count="9">'
        + '<xf numFmtId="0"/>'
        * 4
        + '<xf numFmtId="166"/><xf numFmtId="1"/><xf numFmtId="1"/>'
        '<xf numFmtId="2"/><xf numFmtId="164"/></cellXfs></styleSheet>',
        "xl/sharedStrings.xml": strings.xml(),
    }


def build_workbook(seed: int = DEFAULT_SEED) -> bytes:
    """The workbook half of the response, as `.xlsx` bytes. Not committed."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in _package(build(seed)).items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def _refuse_card_shaped(text: str) -> None:
    """Fail the generator rather than the scanner.

    Every long digit run here is fabricated to fail Luhn, and this proves it
    over the whole output — including runs nobody meant to make, such as two
    numbers meeting across a dash.
    """
    for match in CARD_RUN.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            raise ValueError(f"generated a Luhn-valid digit run: {match.group(0)!r}")


def generate(seed: int = DEFAULT_SEED) -> dict[str, str]:
    text = report_text(build(seed))
    _refuse_card_shaped(text)
    return {REPORT: text}


if __name__ == "__main__":  # pragma: no cover
    print(generate()[REPORT])
