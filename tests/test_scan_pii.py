"""Tests for the PII scanner.

The literals below are deliberately realistic — a scanner tested only against
obviously-fake input is a scanner that has not been tested. Each such line carries an
inline suppression so the scanner does not flag its own test suite; the suppression
lives in the Python source, not in the string handed to `scan_lines`, so it does not
interfere with what is under test.
"""

import pytest
from tools import scan_pii
from tools.scan_pii import Finding, luhn_ok, mask, scan_lines

ADDRESS = "8814 Mockingbird Lane"  # pii-scan: allow synthetic test address
LOYALTY = "600123456789"  # pii-scan: allow synthetic loyalty-shaped test literal


def rules_hit(line: str, **kw) -> set[str]:
    return {f.rule for f in scan_lines([line], "sample.txt", **kw)}


class TestEmail:
    def test_real_domain_is_flagged(self):
        assert "EMAIL" in rules_hit("contact: shopper@gmail.com")  # pii-scan: allow test literal

    def test_reserved_domains_are_not_flagged(self):
        assert "EMAIL" not in rules_hit("contact: shopper@example.com")
        assert "EMAIL" not in rules_hit("contact: shopper@example.org")

    def test_commit_trailer_identities_are_not_flagged(self):
        # These appear in every commit message; flagging them would train people
        # to ignore the history scan.
        assert "EMAIL" not in rules_hit("Co-Authored-By: Bot <noreply@anthropic.com>")
        assert "EMAIL" not in rules_hit("someone@users.noreply.github.com")


class TestMachineIdentities:
    """A trailer written by a bot is not somebody's inbox.

    Every Dependabot commit signs off with `support@github.com`, so the history
    scan went red on all eleven open dependency PRs at once and would have on
    every future one. A check that is always red on a whole class of PR is one
    people learn to scroll past, which CONTRIBUTING.md says protects nobody.
    """

    def test_the_dependabot_trailer_is_not_a_finding(self):
        assert rules_hit("Signed-off-by: dependabot[bot] <support@github.com>") == set()

    def test_a_real_github_inbox_still_is(self):
        # Allowed by exact address, not by domain: github.com carries real mail.
        # pii-scan: allow the literal below is the input proving the rule fires
        assert "EMAIL" in rules_hit("reach me at octocat@github.com")

    def test_the_existing_machine_domains_still_pass(self):
        assert rules_hit("Co-Authored-By: X <a@users.noreply.github.com>") == set()
        assert rules_hit("bot <b@noreply.github.com>") == set()

    def test_the_allowance_is_case_insensitive(self):
        assert rules_hit("<Support@GitHub.com>") == set()


class TestPhone:
    def test_non_555_exchange_is_flagged(self):
        assert "PHONE" in rules_hit("cell 415-682-9013")  # pii-scan: allow test literal

    def test_555_exchange_is_allowed(self):
        assert "PHONE" not in rules_hit("cell 415-555-0142")

    def test_dotted_and_parenthesised_forms(self):
        assert "PHONE" in rules_hit("415.682.9013")  # pii-scan: allow test literal
        assert "PHONE" in rules_hit("(415) 682-9013")  # pii-scan: allow test literal


class TestAddress:
    def test_street_address_is_flagged(self):
        """The M0 acceptance case: a fake-but-realistic address must be rejected."""
        assert "STREET_ADDRESS" in rules_hit("1428 Elm Street")  # pii-scan: allow test literal

    def test_common_street_types(self):
        # pii-scan: allow test literals
        for line in ("221 Baker Ave", "742 Evergreen Terrace", "12 Ocean Blvd"):
            assert "STREET_ADDRESS" in rules_hit(line), line

    def test_state_plus_zip_is_flagged(self):
        assert "ZIP_WITH_STATE" in rules_hit("Portland, OR 97205")  # pii-scan: allow test literal

    def test_zip_plus_four_is_flagged(self):
        assert "ZIP_PLUS_FOUR" in rules_hit("97205-1234")  # pii-scan: allow test literal

    def test_prose_about_addresses_is_not_flagged(self):
        assert not rules_hit("Fabricated addresses must not resolve to a real location.")


class TestNumbers:
    def test_luhn_valid_run_is_a_card(self):
        assert "PAYMENT_CARD" in rules_hit("4111 1111 1111 1111")  # pii-scan: allow test literal

    def test_luhn_invalid_run_is_not_a_card(self):
        assert "PAYMENT_CARD" not in rules_hit("4111 1111 1111 1112")

    def test_luhn(self):
        assert luhn_ok("4111111111111111")  # pii-scan: allow test literal
        assert not luhn_ok("4111111111111112")

    def test_ssn_shape_is_flagged(self):
        assert "SSN" in rules_hit("123-45-6789")  # pii-scan: allow test literal

    def test_loyalty_length_run_flagged_outside_fixtures(self):
        assert "LOYALTY_NUMBER" in rules_hit("604512345678901"[:13])  # pii-scan: allow test literal

    def test_loyalty_length_run_allowed_inside_fixtures(self):
        hit = rules_hit("604512345678901"[:13], is_fixture=True)  # pii-scan: allow test literal
        assert "LOYALTY_NUMBER" not in hit

    def test_version_strings_are_not_loyalty_numbers(self):
        assert "LOYALTY_NUMBER" not in rules_hit("3.121212121212")


class TestHashDigests:
    """A sha256 is 64 hex characters, and about one in three carries a 12-to-14
    digit run somewhere inside it; a few pass a Luhn check by coincidence.
    docker/requirements.txt holds 58 of them and produced 71 findings, none real.
    The guard is on the rule rather than the file: a file-level exemption would
    silence every rule on that path forever, including the day someone pastes
    something else into it."""

    SHA256 = "bfb91aa2d334c61cb35ba9a116fc123b3d3df31640b801cf57a7a78ec3f603b3"
    GIT_SHA = "af680481f2c3d9e0a7b6c5d4e3f2a1b09c8d7e6f"

    def test_a_pip_hash_line_is_clean(self):
        assert rules_hit(f"    --hash=sha256:{self.SHA256}") == set()

    def test_a_git_sha_is_clean(self):
        assert rules_hit(f"commit {self.GIT_SHA}") == set()

    def test_a_real_identifier_beside_a_hash_still_fires(self):
        # The guard exempts what is *inside* the token, not the whole line.
        hits = rules_hit(f"sha256:{self.SHA256} loyalty {LOYALTY}")
        assert "LOYALTY_NUMBER" in hits

    def test_a_short_hex_run_is_not_a_digest(self):
        # 32 characters is the floor. Below it, a hex-looking string is just text
        # and a digit run in it is still a finding.
        assert "LOYALTY_NUMBER" in rules_hit(f"id ab12 {LOYALTY} cd34")

    def test_the_committed_lock_is_clean(self):
        lock = scan_pii.REPO_ROOT / "docker" / "requirements.txt"
        assert lock.is_file()
        assert scan_pii.scan_paths(["docker/requirements.txt"], denylist=()) == []


class TestUuid:
    UUID = "9f8b1c2d-3e4f-4a5b-8c9d-0e1f2a3b4c5d"

    def test_flagged_outside_fixtures_and_tests(self):
        assert "UUID" in rules_hit(self.UUID)

    def test_allowed_in_fixtures_and_tests(self):
        assert "UUID" not in rules_hit(self.UUID, is_fixture=True)
        assert "UUID" not in rules_hit(self.UUID, is_test=True)


class TestSuppression:
    def test_marker_with_reason_silences_the_line(self):
        line = "addr = '1428 Elm Street'  # pii-scan: allow synthetic"
        assert not scan_lines([line], "sample.py")

    def test_bare_marker_does_not_silence(self):
        line = "addr = '1428 Elm Street'  # pii-scan: allow"
        assert scan_lines([line], "sample.py")

    def test_marker_on_previous_line_silences(self):
        # Lets JSON and CSV fixtures be annotated from the line above.
        lines = ["// pii-scan: allow synthetic block", '"street": "1428 Elm Street"']
        assert not scan_lines(lines, "fixture.json")

    def test_marker_does_not_leak_downward(self):
        lines = ["// pii-scan: allow synthetic", "ok", '"street": "1428 Elm Street"']
        assert scan_lines(lines, "fixture.json")


class TestDenylist:
    def test_literal_match_is_case_insensitive(self):
        findings = scan_lines(
            ["Delivered to 1428 ELM STREET, apt 2"],
            "notes.md",
            denylist=("1428 elm street",),
        )
        assert "DENYLIST" in {f.rule for f in findings}


class TestOutput:
    def test_findings_are_masked(self):
        # pii-scan: allow test literal
        f = Finding("a.txt", 1, "EMAIL", "shopper@gmail.com", "hint")
        rendered = f.render()
        assert "shopper@gmail.com" not in rendered  # pii-scan: allow test literal
        assert "sh" in rendered and "*" in rendered

    def test_short_values_are_fully_masked(self):
        assert mask("abcd") == "****"


class TestPathClassification:
    def test_fixture_and_test_detection(self):
        assert scan_pii.classify("src/unbagged/adapters/kroger/fixtures/a.txt") == (True, False)
        assert scan_pii.classify("tests/fixtures/a.json") == (True, True)
        assert scan_pii.classify("tests/test_kroger.py") == (False, True)
        assert scan_pii.classify("src/unbagged/cli.py") == (False, False)


class TestOpaqueFixtures:
    """A fixtures directory is exempt from .gitignore's denials and stands
    address rules down. A committed file the scanner cannot read is therefore
    reviewed by nothing at all — which is how a report-shaped PDF used to pass
    every check this project has."""

    @pytest.mark.parametrize(
        "path",
        [
            "src/unbagged/adapters/kroger/fixtures/report.pdf",
            "src/unbagged/adapters/kroger/fixtures/export.zip",
            "src/unbagged/adapters/safeway/fixtures/response.xlsx",
            "tests/fixtures/hand-written.pdf",
        ],
    )
    def test_unreadable_fixture_files_are_findings(self, path):
        assert scan_pii.opaque_in_fixtures(path)

    @pytest.mark.parametrize(
        "path",
        [
            "src/unbagged/adapters/kroger/fixtures/screenshot.png",
            "src/unbagged/adapters/kroger/fixtures/logo.ico",
        ],
    )
    def test_images_a_person_can_look_at_are_allowed(self, path):
        assert not scan_pii.opaque_in_fixtures(path)

    def test_the_rule_is_scoped_to_fixture_directories(self):
        # Ordinary binaries elsewhere in the repo are not the scanner's business.
        assert not scan_pii.opaque_in_fixtures("resources/icon-512.png")
        assert not scan_pii.opaque_in_fixtures("docs/diagram.pdf")

    def test_a_dropped_in_report_is_reported(self, tmp_path, monkeypatch):
        fixtures = tmp_path / "src" / "unbagged" / "adapters" / "acme" / "fixtures"
        fixtures.mkdir(parents=True)
        (fixtures / "report.pdf").write_bytes(b"%PDF-1.7\n4417 Marbury Lane\n")
        monkeypatch.setattr(scan_pii, "REPO_ROOT", tmp_path)
        rel = "src/unbagged/adapters/acme/fixtures/report.pdf"
        findings = scan_pii.scan_paths([rel], denylist=())
        assert [f.rule for f in findings] == ["OPAQUE_FIXTURE"]


class TestSelfScan:
    def test_the_repository_is_clean(self):
        """The scanner must pass on its own repository, with no findings."""
        assert scan_pii.main([]) == 0


class TestHistoryScan:
    """The diff walker, exercised without a fixture repository."""

    ADD = (
        "\x01abcdef0123456789\n"
        "add the report\n"
        "\n"
        "diff --git a/notes.txt b/notes.txt\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/notes.txt\n"
        "@@ -0,0 +1,3 @@\n"
        "+first line\n"
        f"+{ADDRESS}\n"
        "+third line\n"
    )
    REMOVE = (
        "\x01fedcba9876543210\n"
        "remove it, hope nobody notices\n"
        "\n"
        "diff --git a/notes.txt b/notes.txt\n"
        "deleted file mode 100644\n"
        "--- a/notes.txt\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-first line\n"
        f"-{ADDRESS}\n"
        "-third line\n"
    )

    def test_added_line_is_attributed_to_file_and_line_number(self):
        findings = scan_pii.scan_diff_stream(self.ADD)
        assert [(f.origin, f.line_no, f.rule) for f in findings] == [
            ("abcdef012345:notes.txt", 2, "STREET_ADDRESS")
        ]

    def test_amended_away_content_is_still_caught(self):
        """The point of the history gate: the tip is clean, the history is not."""
        assert scan_pii.scan_diff_stream(self.ADD + self.REMOVE)

    def test_deletion_diff_does_not_double_report(self):
        # Removed lines were already scanned in the commit that added them, and
        # they must not be misattributed to the commit message either.
        findings = scan_pii.scan_diff_stream(self.REMOVE)
        assert findings == []

    def test_commit_messages_are_scanned(self):
        stream = f"\x01abcdef0123456789\nshipped for {ADDRESS}\n"
        findings = scan_pii.scan_diff_stream(stream)
        assert [(f.origin, f.rule) for f in findings] == [
            ("abcdef012345:<message>", "STREET_ADDRESS")
        ]

    def test_context_lines_are_not_rescanned(self):
        stream = (
            "\x01abcdef0123456789\ntouch the file\n"
            "diff --git a/notes.txt b/notes.txt\n"
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -1,3 +1,4 @@\n"
            f" {ADDRESS}\n"
            "+harmless addition\n"
        )
        assert scan_pii.scan_diff_stream(stream) == []

    def test_hunk_offsets_give_real_line_numbers(self):
        stream = (
            "\x01abcdef0123456789\nedit\n"
            "diff --git a/notes.txt b/notes.txt\n"
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -40,2 +40,3 @@\n"
            " context\n"
            f"+{ADDRESS}\n"
        )
        assert scan_pii.scan_diff_stream(stream)[0].line_no == 41

    def test_previous_line_suppression_survives_the_diff_walk(self):
        # The walker sees one line at a time; without threading the preceding line
        # through, every previous-line suppression would misfire in history.
        stream = (
            "\x01abcdef0123456789\nadd a fixture\n"
            "diff --git a/f.json b/f.json\n"
            "--- /dev/null\n"
            "+++ b/f.json\n"
            "@@ -0,0 +1,2 @@\n"
            '+// pii-scan: allow synthetic fixture\n'
            f'+"street": "{ADDRESS}"\n'
        )
        assert scan_pii.scan_diff_stream(stream) == []

    def test_suppression_on_an_unchanged_line_still_applies(self):
        stream = (
            "\x01abcdef0123456789\nedit a fixture\n"
            "diff --git a/f.json b/f.json\n"
            "--- a/f.json\n"
            "+++ b/f.json\n"
            "@@ -1,1 +1,2 @@\n"
            ' // pii-scan: allow synthetic fixture\n'
            f'+"street": "{ADDRESS}"\n'
        )
        assert scan_pii.scan_diff_stream(stream) == []
