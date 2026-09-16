"""The denylist builder.

The property that matters most is negative: it must not print what it finds.
A tool that echoes your address to a terminal has defeated its own purpose,
because terminals scroll back and transcripts get pasted into issues.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from tools import build_denylist
from tools.build_denylist import (
    _worth_keeping,
    harvest,
    is_gitignored,
    merge,
    split_known_values,
)

# Fabricated, but shaped like the real thing on purpose: a harvester tested only
# against obviously-fake values is a harvester that will miss the real ones. Each
# is suppressed individually so the scanner stays armed everywhere else.
FAKE_NAME = "Marisol"
FAKE_LOYALTY = "6042998877665"  # pii-scan: allow fabricated test value
FAKE_EMAIL = "marisol.okonkwo@fastmail.example"
FAKE_PHONE = "(503) 284-9917"  # pii-scan: allow fabricated test value
FAKE_STREET = "8814 Thistlewick Row"  # pii-scan: allow fabricated test value
FAKE_ZIP = "97214-1188"  # pii-scan: allow fabricated test value
FAKE_REFERENCE = "ABC12345"

REPORT = f"""\
Data we hold related to our Loyalty program:

{{
  "customer": [{{
    "firstName": "{FAKE_NAME}",
    "loyaltyno": "{FAKE_LOYALTY}",
    "emailAddress": "{FAKE_EMAIL}",
    "phoneNumber": "{FAKE_PHONE}",
    "addressLine1": "{FAKE_STREET}",
    "zipCode": "{FAKE_ZIP}",
    "purchasedescription": "BANANAS ORGANIC"
  }}]
}}
"""


class TestNeverPrintsValues:
    def test_the_cli_prints_counts_and_no_values(self, tmp_path, monkeypatch):
        report = tmp_path / "report.txt"
        report.write_text(REPORT, encoding="utf-8")
        output = tmp_path / "denylist.txt"
        monkeypatch.setattr(build_denylist, "is_gitignored", lambda _p: True)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'tools');"
                " import build_denylist as b; b.is_gitignored = lambda p: True;"
                f" raise SystemExit(b.main([{str(report)!r}, '-o', {str(output)!r}]))",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0
        printed = result.stdout + result.stderr
        for secret in (FAKE_NAME, FAKE_LOYALTY, FAKE_EMAIL, FAKE_PHONE, FAKE_STREET, FAKE_ZIP):
            assert secret not in printed, f"{secret!r} was printed"
        # It did find them; it just did not say what they were.
        assert output.read_text(encoding="utf-8").count("\n") > 4


class TestOutputSafety:
    def test_it_refuses_to_write_a_file_git_can_see(self, tmp_path, capsys):
        report = tmp_path / "report.txt"
        report.write_text(REPORT, encoding="utf-8")
        # A denylist that is not gitignored is a PII file waiting to be committed.
        assert build_denylist.main([str(report), "-o", str(tmp_path / "tracked.txt")]) == 1
        assert "not gitignored" in capsys.readouterr().err

    def test_the_real_denylist_path_is_gitignored(self):
        assert is_gitignored(build_denylist.DEFAULT_OUTPUT)


class TestHarvest:
    def test_it_finds_the_obvious_categories(self, tmp_path):
        found = harvest(REPORT, f"Kroger-{FAKE_REFERENCE} Access Report.pdf")
        assert found["email addresses"]
        assert found["phone numbers"]
        assert found["street addresses"]
        assert found["ZIP+4 codes"]
        assert found["long digit runs"]

    def test_it_reads_identifying_json_fields(self, tmp_path):
        found = harvest(REPORT, "report.pdf")
        assert FAKE_LOYALTY in found["identifying JSON fields"]
        # A first name belongs on a denylist: it is what gets typed into a
        # commit message without thinking.
        assert FAKE_NAME in found["identifying JSON fields"]

    def test_nested_json_is_reached(self):
        """The values live inside `{"customer": [{...}]}`.

        A non-greedy `{.*?}` stops at the first closing brace and never matches a
        nested object, so this harvested nothing at all from a real report while
        appearing to work. See unbagged.jsonscan.
        """
        found = harvest(REPORT, "report.pdf")
        assert found["identifying JSON fields"]

    def test_product_descriptions_are_not_identifying(self):
        # purchasedescription matches nothing personal and would add every item
        # in a two-year purchase history to the denylist.
        found = harvest(REPORT, "report.pdf")
        assert "BANANAS ORGANIC" not in found["identifying JSON fields"]

    def test_the_report_reference_in_the_filename_is_captured(self):
        # Retailers put the reference in the filename, and it identifies you as
        # surely as anything inside the document.
        found = harvest(REPORT, f"Kroger-{FAKE_REFERENCE} Access Report.pdf")
        assert FAKE_REFERENCE in found["report references"]

    def test_sweeping_can_be_skipped(self):
        found = harvest(REPORT, "report.pdf", sweep=False)
        assert not found["long digit runs"]


class TestWorthKeeping:
    @pytest.mark.parametrize("value", ["ab", "12345", "kroger", "unknown", "0000000"])
    def test_noise_is_rejected(self, value):
        # A denylist that fires on "Lee" trains people to bypass the scanner.
        assert not _worth_keeping(value)

    @pytest.mark.parametrize("value", [FAKE_LOYALTY, FAKE_STREET, FAKE_ZIP])
    def test_real_shapes_are_kept(self, value):
        assert _worth_keeping(value)


class TestAlreadyCommitted:
    def test_values_already_in_the_repository_are_dropped(self):
        """A value sitting in committed code is a format constant, not a secret.

        Kroger's placeholder UPC is in the fixture and in NOTES.md. Denylisting
        it makes the scanner fire on 150 innocent lines, and a scanner people
        learn to ignore protects nobody.
        """
        # pii-scan: allow known placeholder UPC, not an identifier
        constant = "00010000080000"
        corpus = f"the placeholder upc is {constant} and amount is a word"
        keep, dropped = split_known_values({constant, "amount", FAKE_LOYALTY}, corpus)
        assert keep == {FAKE_LOYALTY}
        assert dropped == {constant, "amount"}


class TestMerge:
    def test_a_second_report_adds_rather_than_replaces(self, tmp_path):
        output = tmp_path / "denylist.txt"
        merge(output, {FAKE_LOYALTY})
        before, after = merge(output, {FAKE_STREET})
        assert (before, after) == (1, 2)
        assert FAKE_LOYALTY in output.read_text(encoding="utf-8")

    def test_the_file_says_what_it_is(self, tmp_path):
        output = tmp_path / "denylist.txt"
        merge(output, {FAKE_LOYALTY})
        assert "gitignored" in output.read_text(encoding="utf-8")


class TestNameValuePairs:
    def test_a_field_label_is_not_treated_as_personal_data(self):
        """Reports use `{"Name": "SubscriberKey", "Value": "..."}`.

        The label half matched on "name", which put the retailer's own field
        names on the denylist — and then the scanner fired on the adapter code
        that reads those fields. Found by exactly that happening.
        """
        report = (
            "Email Information\n"
            '{"emailData": [{"Name": "SubscriberKey", "Value": "' + FAKE_LOYALTY + '"},'
            ' {"Name": "EmailAddress", "Value": "' + FAKE_EMAIL + '"}]}\n'
        )
        found = harvest(report, "report.pdf")["identifying JSON fields"]
        assert "SubscriberKey" not in found
        assert "EmailAddress" not in found

    def test_camel_case_name_fields_are_still_caught(self):
        report = '{"firstName": "' + FAKE_NAME + '", "lastName": "Okonkwo"}'
        found = harvest(report, "report.pdf")["identifying JSON fields"]
        assert FAKE_NAME in found
        assert "Okonkwo" in found


class TestDatesThatCameFromAResponse:
    """ISSUE-086, and the class of thing that got past every gate.

    A date has no shape that separates it from fiction — `2026-01-04` in a
    generated fixture, `%Y-%m-%d` in a format string and a release date in the
    CHANGELOG are the same characters — so the nine rule families in
    `scan_pii.py` cannot have a rule for one. The signal is not the date, it is
    a date that came from a response, and only a response can say which. This
    derives them instead of guessing.
    """

    def harvested(self, occurred_at: str) -> set[str]:
        found = build_denylist.buckets()
        build_denylist.from_visit_times(occurred_at, found)
        return found["dates and times of a visit"]

    def test_a_visit_yields_the_date_and_both_ways_of_writing_its_time(self):
        assert self.harvested("2024-02-29T16:05:00") == {
            "2024-02-29",
            "2024-02-29 16:05",
            "2024-02-29T16:05",
        }

    def test_a_report_that_writes_a_space_and_a_trailing_zero_is_read_the_same(self):
        """SpreadsheetML gives `... 16:05:00.0`; an ISO string gives `...T16:05:00`.
        The same instant has to reach the list in both shapes, because the
        scanner matches literal substrings."""
        assert self.harvested("2024-02-29 16:05:00.0") == self.harvested("2024-02-29T16:05:00")

    def test_a_visit_with_no_time_yields_the_date_alone(self):
        assert self.harvested("2024-02-29") == {"2024-02-29"}

    def test_something_that_is_not_a_date_yields_nothing(self):
        assert self.harvested("") == set()
        assert self.harvested("sometime last Tuesday") == set()

    @pytest.mark.parametrize(
        "line",
        [
            '"""`2024-02-29 16:05:00.0` to `2024-02-29T16:05:00`."""',
            'data = _cell("40100200300") + _cell("2024-02-29 16:05:00.0")',
            "# two trips on 2024-02-29, at different lanes",
            "locator='Transaction of 2024-02-29T16:05'",
        ],
    )
    def test_the_shapes_that_actually_leaked_are_all_matched(self, line):
        """Each of these is a real site the value reached, with the date changed.
        `scan_pii` lowercases both sides and matches a substring, so the check
        here is the one the scanner performs."""
        needles = {value.lower() for value in self.harvested("2024-02-29T16:05:00")}
        assert any(needle in line.lower() for needle in needles)

    def test_a_bare_date_is_kept_even_though_it_is_eight_digits(self):
        """`_worth_keeping` wants nine digits in an all-digit value, and a date
        strips to eight — so routing these through it dropped every one."""
        assert not _worth_keeping("2024-02-29")
        assert "2024-02-29" in self.harvested("2024-02-29")

    def test_the_two_harvest_paths_agree_on_which_buckets_exist(self):
        """They each built their own dict, so a bucket added to one was dropped
        by the other on merge."""
        assert set(harvest("", "report.xls", sweep=False)) == set(build_denylist.BUCKETS)

    def test_the_adapter_path_actually_feeds_them_in(self):
        """The unit above proves the shapes; this proves the wiring.

        Against the committed synthetic fixture, which is generated and holds
        nobody's real visits — but reaches `from_adapter` by exactly the route a
        real response does.
        """
        fixture = (
            Path(__file__).parent.parent
            / "src"
            / "unbagged"
            / "adapters"
            / "hmart"
            / "fixtures"
            / "synthetic_history.xls"
        )
        found = build_denylist.buckets()
        assert build_denylist.from_adapter(fixture, found), (
            "a response whose adapter finds no identity graph still has visits, "
            "and the visits are the part that leaked"
        )
        dates = found["dates and times of a visit"]
        assert dates, "the points statement is visits and nothing else"
        assert all(value[:4].isdigit() and value[4] == "-" for value in dates)
