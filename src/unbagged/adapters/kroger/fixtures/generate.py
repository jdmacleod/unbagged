"""Generate a structurally faithful, entirely fabricated Kroger report.

Seeded from the *structure* of one real report, never from its values. Every
value here is invented: reserved-for-fiction phone exchanges (555), example.com
email domains, and street names taken from a fixed list of places that do not
exist. Nothing in this file was copied from a real response.

The point is fidelity of shape, because that is what the adapter has to survive:

* four pretty-printed JSON blobs separated by prose headers, inside what is
  really a PDF text layer
* bare page-number lines interleaved into the middle of the JSON
* the ``UNKNOWN`` placeholder item with its constant UPC and zero amounts
* negative ``retailamt`` values from returns and voids
* no Section 2, 3 or 4 — the disclosure obligations the report simply omits

Output is deterministic: the same seed produces byte-identical text, which is
what lets CI regenerate the committed fixture and fail on any difference.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

from faker import Faker

DEFAULT_SEED = 20260101
FILENAME = "synthetic_report.txt"

# Lines of body text between interleaved page-number lines. The real report's
# text layer breaks roughly this often.
LINES_PER_PAGE = 46

# The placeholder row that appears constantly in the real export: no product, no
# money, but a real line in the JSON. It is generated because the adapter has to
# recognise it, and counting it as a purchase would inflate every basket.
PLACEHOLDER_DESCRIPTION = "UNKNOWN"
PLACEHOLDER_UPC = "00010000080000"  # pii-scan: allow placeholder UPC, not an identifier

# Street names that do not exist anywhere, so a fabricated address cannot
# accidentally name a real household.
FICTIONAL_STREETS = (
    "Thistlewick Row", "Kelmarsh Bend", "Ombersley Reach", "Farrowgate Close",
    "Nether Quill Walk", "Pennywhistle Bank", "Yarrowmede Rise", "Cobbleshaw Mews",
)

# The catalogue is built rather than listed: the reference dataset had roughly
# 380 distinct UPCs across 23 months, and a fixture with thirty of them makes the
# price-history view look like noise instead of a personal inflation series.
DEPARTMENTS = (
    # (department, [(item, low, high)], brands)
    ("PRODUCE", (
        ("BANANAS", 0.59, 0.89), ("HASS AVOCADO", 0.89, 2.49), ("ROMA TOMATO", 1.19, 2.99),
        ("GALA APPLE", 1.29, 2.79), ("BABY CARROT", 1.09, 2.29), ("BABY SPINACH", 2.49, 4.49),
        ("BROCCOLI CROWN", 1.49, 3.19), ("RED ONION", 0.99, 2.19), ("RUSSET POTATO", 3.49, 6.99),
        ("SEEDLESS GRAPE", 2.99, 5.99), ("STRAWBERRY", 2.49, 5.49), ("BLUEBERRY PINT", 2.99, 6.49),
        ("ROMAINE HEART", 2.29, 4.29), ("BELL PEPPER", 0.99, 2.49), ("LEMON", 0.59, 1.29),
    ), ("", "ORGANIC", "SIMPLE TRUTH ORG")),
    ("DAIRY", (
        ("2% MILK", 2.79, 4.99), ("WHOLE MILK", 2.89, 5.19), ("LARGE EGGS", 2.19, 6.49),
        ("SHRED CHEDDAR", 2.19, 4.29), ("GREEK YOGURT", 3.99, 6.79), ("BUTTER", 3.49, 6.99),
        ("SOUR CREAM", 1.79, 3.29), ("CREAM CHEESE", 1.99, 3.79), ("HALF AND HALF", 1.99, 3.99),
        ("STRING CHEESE", 3.29, 5.99),
    ), ("KRO", "SIMPLE TRUTH", "PRIVATE SELECTION")),
    ("MEAT", (
        ("CHICKEN BREAST", 3.99, 12.49), ("GROUND BEEF 80/20", 4.49, 9.99),
        ("PORK CHOP", 3.29, 8.49), ("SALMON FILLET", 8.99, 16.99), ("BACON", 4.99, 9.49),
        ("SIRLOIN STEAK", 7.99, 15.99), ("CHICKEN THIGH", 2.49, 6.99),
        ("GROUND TURKEY", 3.99, 7.49), ("DELI TURKEY", 4.49, 8.99),
    ), ("KRO", "HERITAGE FARM", "PRIVATE SELECTION")),
    ("GROCERY", (
        ("WHOLE WHEAT BREAD", 1.99, 3.99), ("SPAGHETTI", 0.99, 2.49),
        ("MARINARA SAUCE", 1.79, 3.99), ("PEANUT BUTTER", 2.49, 4.99),
        ("HONEY NUT CEREAL", 3.29, 5.99), ("GROUND COFFEE", 5.49, 11.49),
        ("OLIVE OIL", 6.99, 13.49), ("CANNED TOMATO", 0.89, 2.19),
        ("BLACK BEANS", 0.79, 1.89), ("WHITE RICE", 2.49, 6.49),
        ("CHICKEN BROTH", 1.29, 2.99), ("TORTILLA CHIP", 2.49, 4.99),
        ("SALSA", 2.19, 4.49), ("HONEY", 3.99, 7.99), ("MAPLE SYRUP", 4.99, 9.99),
    ), ("KRO", "SIMPLE TRUTH", "")),
    ("FROZEN", (
        ("PEPPERONI PIZZA", 4.49, 8.99), ("VANILLA ICE CREAM", 3.29, 6.99),
        ("MIXED VEGETABLE", 1.29, 2.99), ("CHICKEN NUGGET", 4.99, 9.49),
        ("WAFFLE", 2.29, 4.29), ("FRENCH FRY", 2.49, 4.99),
    ), ("KRO", "PRIVATE SELECTION", "")),
    ("BEVERAGE", (
        ("ORANGE JUICE", 2.79, 5.29), ("SPARKLING WATER", 3.49, 6.99),
        ("COLA", 4.99, 9.49), ("ICED TEA", 1.99, 3.99), ("SPORTS DRINK", 4.49, 8.99),
    ), ("KRO", "SIMPLE TRUTH", "")),
    ("HOUSEHOLD", (
        ("PAPER TOWEL", 5.99, 11.49), ("BATHROOM TISSUE", 7.49, 14.99),
        ("LAUNDRY DETERGENT", 8.49, 15.99), ("DISH SOAP", 2.29, 4.49),
        ("TRASH BAG", 8.99, 16.49), ("ALUMINUM FOIL", 3.49, 6.49),
        ("SPONGE", 2.49, 4.99),
    ), ("KRO", "HOME SENSE", "")),
    ("PERSONAL CARE", (
        ("TOOTHPASTE", 2.99, 5.49), ("SHAMPOO", 4.49, 9.99), ("BAR SOAP", 3.29, 6.49),
        ("DEODORANT", 3.49, 6.99), ("HAND SOAP", 2.49, 4.99),
    ), ("KRO", "SIMPLE TRUTH", "")),
    ("PET", (
        ("DRY DOG FOOD", 12.99, 28.99), ("CAT LITTER", 8.49, 16.99),
        ("DOG TREAT", 3.99, 8.49), ("WET CAT FOOD", 0.79, 1.69),
    ), ("KRO", "PET PRIDE", "")),
)

SIZES = ("8Z", "12Z", "16Z", "24Z", "32Z", "DZ", "GAL", "LB", "EA", "6CT", "12CT")


def build_catalogue() -> tuple[tuple[str, str, float], ...]:
    """(description, upc, base_price) for every product, deterministic in order.

    UPCs are sequential inside a per-department prefix, which is not how real
    UPCs work but does give the adapter stable 11-14 digit codes to group by.
    """
    catalogue: list[tuple[str, str, float]] = []
    for dept_index, (_dept, items, brands) in enumerate(DEPARTMENTS, start=1):
        serial = 0
        for item, low, high in items:
            for brand_index, brand in enumerate(brands):
                size = SIZES[(serial + brand_index) % len(SIZES)]
                description = " ".join(part for part in (brand, item, size) if part)
                serial += 1
                upc = f"000{dept_index:02d}{serial:06d}"
                # Spread base prices across the band so the same item in two
                # brands is not the same price.
                base = low + (high - low) * ((brand_index + 1) / (len(brands) + 1))
                catalogue.append((description, upc, round(base, 2)))
    return tuple(catalogue)


CATALOGUE = build_catalogue()

# Annual price drift, applied on top of per-visit jitter. Without a trend the
# price-history view shows noise; with one it shows what the data actually
# contains, which is what makes that view worth building.
ANNUAL_DRIFT = 0.061

TENDERS = ("CREDIT", "DEBIT", "CASH", "EBT", "GIFT CARD")
DIVISIONS = ("016", "024", "701")
STORES = ("00318", "00427", "00891", "01102")

# The five propensity axes, with the prose values the real report uses instead of
# numbers. Classified FIRST_PARTY_MODEL: they are computable from baskets.
PROPENSITY_AXES = (
    ("Convenience", ("Low", "Below Average", "Average", "Above Average", "High")),
    ("Loyalty", ("Low", "Below Average", "Average", "Above Average", "High")),
    ("Price", ("Low", "Below Average", "Average", "Above Average", "High")),
    ("Quality", ("Low", "Below Average", "Average", "Above Average", "High")),
    ("Variety Seeking", ("Low", "Below Average", "Average", "Above Average", "High")),
)

ORDINAL_1_7 = (
    "1 - Very Unlikely", "2 - Unlikely", "3 - Somewhat Unlikely", "4 - Neutral",
    "5 - Somewhat Likely", "6 - Likely", "7 - Very Likely",
)

INCOME_BANDS = (
    "1 - Under $25,000", "2 - $25,000-$49,999", "3 - $50,000-$74,999",
    "4 - $75,000-$99,999", "5 - $100,000-$149,999", "6 - $150,000-$199,999",
    "7 - $200,000+",
)

EDUCATION_LEVELS = (
    "High School", "Some College", "Bachelors Degree", "Graduate Degree",
)

HOUSEHOLD_COMPOSITIONS = (
    "Single, No Children", "Couple, No Children", "Family with Young Children",
    "Family with Teens", "Multi-Generational", "Empty Nesters",
)

PROSE_INTRO = """\
Thank you for contacting us regarding your California consumer privacy request.

The information below reflects the personal information we hold about you as of
the date of this report, covering the twenty-four (24) month period preceding
your request. If you would like information relating to the period prior to
2022, please contact our privacy office separately and we will process that as
a supplemental request.

This report is provided in response to your request for the specific pieces of
personal information we have collected about you.
"""

PROSE_CLOSING = """\
If you have questions about this report, please contact our privacy office.

End of report.
"""


def _priced(rng: random.Random, base: float, when: datetime, origin: datetime) -> float:
    """Base price plus drift since the start of the window, plus per-visit noise.

    Promotions are part of the record too, so roughly one line in eight comes in
    materially under trend.
    """
    years = (when - origin).days / 365.25
    trend = base * (1 + ANNUAL_DRIFT * years)
    jitter = rng.uniform(0.96, 1.06)
    if rng.random() < 0.12:
        jitter *= rng.uniform(0.70, 0.85)
    return round(max(trend * jitter, 0.10), 2)


def _loyalty_number(rng: random.Random) -> str:
    return f"6{rng.randrange(10**11, 10**12):012d}"


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_number_with_cd(rng: random.Random, loyalty: str) -> str:
    """Loyalty number plus a trailing check digit, chosen so the result fails Luhn.

    A 14-digit run that passes Luhn is indistinguishable from a payment card, and
    tools/scan_pii.py rightly refuses to let one sit in the tree. Failing the
    check costs nothing here — the field's length and shape are what the adapter
    parses — and it means a real card number pasted into this fixture still gets
    caught by the scanner instead of hiding behind a relaxed rule.
    """
    for candidate in rng.sample(range(10), 10):
        value = f"{loyalty}{candidate}"
        if not _luhn_ok(value):
            return value
    raise AssertionError("unreachable: at most one check digit satisfies Luhn")


def _identity_blob(fake: Faker, rng: random.Random, loyalty: str) -> dict:
    """The identity graph. Eight different identifiers for one shopper, which is
    itself the finding: the report never explains what any of them are for."""
    street = f"{rng.randrange(100, 9999)} {rng.choice(FICTIONAL_STREETS)}"
    return {
        "customer": [
            {
                "firstName": fake.first_name(),
                "lastName": fake.last_name(),
                "loyaltyno": loyalty,
                "cardNumberWithCD": _card_number_with_cd(rng, loyalty),
                "alternateId": f"{rng.randrange(10**9, 10**10)}",
                "ehhn": f"{rng.randrange(10**10, 10**11)}",
                "householdId": f"HH{rng.randrange(10**8, 10**9)}",
                "cgPersonId": f"CG-{rng.randrange(10**7, 10**8)}",
                "epsn": f"{rng.randrange(10**11, 10**12)}",
                "SubscriberID": f"{rng.randrange(10**7, 10**8)}",
                "emailAddress": f"{fake.user_name()}@example.com",
                "phoneNumber": f"({rng.randrange(200, 990)}) 555-{rng.randrange(0, 10000):04d}",
                "addressLine1": street,
                "city": fake.city(),
                "state": "CA",
                "zipCode": f"9{rng.randrange(0, 10000):04d}",
                "enrollmentDate": fake.date_between(
                    datetime(2009, 1, 1).date(), datetime(2019, 12, 31).date()
                ).isoformat(),
            }
        ]
    }


def _inference_blob(rng: random.Random) -> dict:
    """Both inference classes in one blob, exactly as the real report mixes them.

    Separating them is the adapter's job, and the classification is the most
    interesting output the tool produces: propensity axes are computable from the
    baskets in this same report, while household income and cruise likelihood are
    not, and the report never says where they came from.
    """
    return {
        "customer": [
            {
                "propensities": {
                    axis: rng.choice(values) for axis, values in PROPENSITY_AXES
                },
                "demographics": {
                    "ageRange": rng.choice(("25-34", "35-44", "45-54", "55-64", "65+")),
                    "educationLevel": rng.choice(EDUCATION_LEVELS),
                    "gender": rng.choice(("M", "F", "U")),
                    "householdComposition": rng.choice(HOUSEHOLD_COMPOSITIONS),
                    "numberOfAdults": rng.randrange(1, 5),
                    "numberOfChildren": rng.randrange(0, 4),
                    "petOwner": rng.choice(("Y", "N")),
                    "homeOwnerStatus": rng.choice(("Owner", "Renter", "Unknown")),
                    "lengthOfResidence": rng.randrange(1, 25),
                },
                "likelihoods": {
                    "incomePredictorScore": rng.choice(INCOME_BANDS),
                    "cruiseLikelihood": rng.choice(ORDINAL_1_7),
                    "travelLikelihood": rng.choice(ORDINAL_1_7),
                    "charitableGivingLikelihood": rng.choice(ORDINAL_1_7),
                    "onlineShopperLikelihood": rng.choice(ORDINAL_1_7),
                },
            }
        ]
    }


def _email_blob(fake: Faker, rng: random.Random, start: datetime) -> dict:
    campaigns = (
        "Weekly Digital Deals", "Fuel Points Reminder", "Personalized Coupons",
        "New Store Opening", "Boost Membership Offer",
    )
    records = []
    for i in range(rng.randrange(8, 14)):
        sent = start + timedelta(days=rng.randrange(0, 700), hours=rng.randrange(0, 24))
        records.append(
            {
                "campaignName": rng.choice(campaigns),
                "sentDate": sent.strftime("%Y-%m-%d"),
                "sentTime": sent.strftime("%H:%M:%S"),
                "opened": rng.choice(("Y", "N")),
                "clicked": rng.choice(("Y", "N")),
                "emailAddress": f"{fake.user_name()}@example.com",
                "subscriptionStatus": "Subscribed" if i % 7 else "Unsubscribed",
            }
        )
    return {"customer": [{"emailActivity": records}]}


def _basket(rng: random.Random, when: datetime, index: int, origin: datetime) -> dict:
    items = []
    total = 0.0
    for _ in range(rng.randrange(3, 18)):
        description, upc, base = rng.choice(CATALOGUE)
        retail = _priced(rng, base, when, origin)
        loyalty = round(retail * rng.choice((0.0, 0.0, 0.0, 0.05, 0.1, 0.2)), 2)
        total += retail
        items.append(
            {
                "purchasedescription": description,
                "productupc": upc,
                "retailamt": retail,
                "customerloyamt": loyalty,
            }
        )

    # The placeholder appears in most baskets, sometimes more than once.
    for _ in range(rng.randrange(0, 3)):
        items.append(
            {
                "purchasedescription": PLACEHOLDER_DESCRIPTION,
                "productupc": PLACEHOLDER_UPC,
                "retailamt": 0.0,
                "customerloyamt": 0.0,
            }
        )

    # Returns and voids show up as negative amounts. They are real and must not
    # be filtered, so the fixture has to contain some.
    if rng.random() < 0.12:
        description, upc, base = rng.choice(CATALOGUE)
        refund = -_priced(rng, base, when, origin)
        total += refund
        items.append(
            {
                "purchasedescription": description,
                "productupc": upc,
                "retailamt": refund,
                "customerloyamt": 0.0,
            }
        )

    rng.shuffle(items)
    return {
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M:%S"),
        "division": rng.choice(DIVISIONS),
        "store": rng.choice(STORES),
        "orderno": f"{index:06d}",
        "total_amount_prior_to_discounts": round(total, 2),
        "tenders": [{"tendertype": rng.choice(TENDERS), "amount": round(total, 2)}],
        "items": items,
    }


def _purchase_blob(rng: random.Random, start: datetime, months: int) -> dict:
    baskets = []
    when = start
    index = 1
    end = start + timedelta(days=months * 30)
    while when < end:
        when += timedelta(days=rng.randrange(2, 11), hours=rng.randrange(-4, 5))
        if when >= end:
            break
        baskets.append(_basket(rng, when, index, start))
        index += 1
    return {"customer": [{"basket": baskets}]}


def _interleave_page_numbers(text: str, lines_per_page: int = LINES_PER_PAGE) -> str:
    """Insert bare page-number lines through the body, including mid-JSON.

    This is the quirk that stops the report's JSON from parsing directly, and
    reproducing it is the whole reason this fixture exists.
    """
    out: list[str] = []
    page = 1
    for i, line in enumerate(text.splitlines(), start=1):
        out.append(line)
        if i % lines_per_page == 0:
            page += 1
            out.append(f"  {page}")
    return "\n".join(out) + "\n"


def build(seed: int = DEFAULT_SEED, *, months: int = 24) -> str:
    """Render one synthetic report. Deterministic for a given seed."""
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)

    end = datetime(2026, 1, 31, tzinfo=UTC).replace(tzinfo=None)
    start = end - timedelta(days=months * 30)
    loyalty = _loyalty_number(rng)

    def blob(value: dict) -> str:
        return json.dumps(value, indent=2)

    sections = [
        "KROGER CONSUMER PRIVACY REQUEST RESPONSE",
        "",
        f"Report reference: SYNTH-{seed}",
        f"Report period: {start:%Y-%m-%d} through {end:%Y-%m-%d}",
        "",
        PROSE_INTRO,
        "",
        "Section 1: Specific Pieces of Personal Information Collected",
        "",
        "Data we hold related to our Loyalty program:",
        "",
        blob(_identity_blob(fake, rng, loyalty)),
        "",
        "Data we hold to communicate and advertise to you in a personalized way:",
        "",
        blob(_inference_blob(rng)),
        "",
        "Email Information",
        "",
        blob(_email_blob(fake, rng, start)),
        "",
        "Data related to in-store services:",
        "",
        "Information about your purchases:",
        "",
        blob(_purchase_blob(rng, start, months)),
        "",
        PROSE_CLOSING,
    ]
    return _interleave_page_numbers("\n".join(sections))


def generate(seed: int = DEFAULT_SEED) -> dict[str, str]:
    """Entry point for tools/make_fixtures.py: filename -> content."""
    return {FILENAME: build(seed)}
