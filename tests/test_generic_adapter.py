"""The fallback adapter, and the registry's preference for real ones."""

import ast
from pathlib import Path

import pytest

import unbagged.adapters  # noqa: F401  (registers everything)
from unbagged.adapters.generic.adapter import GenericAdapter
from unbagged.adapters.hmart.adapter import HMartAdapter
from unbagged.adapters.registry import registry
from unbagged.adapters.safeway.adapter import SafewayAdapter
from unbagged.models import (
    AdapterError,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpKind,
    SourceBundle,
    SourceDocument,
)

KROGER_FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)

LETTER = """\
Dear Customer,

Thank you for your request under the California Consumer Privacy Act.

The categories of personal information we collect include identifiers and
commercial information. We do not sell personal information to third parties.

We retain your information for as long as your account is active.

Sincerely,
The Privacy Team
"""

BARE_LETTER = "Dear Customer,\n\nThank you for writing to us.\n\nSincerely.\n"


def bundle(tmp_path, text: str, name: str = "letter.txt", **kwargs) -> SourceBundle:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    document = SourceDocument(name, "0" * 64, path=str(path), id=1)
    return SourceBundle(documents=(document,), **kwargs)


class TestSelection:
    def test_a_letter_reaches_the_fallback_rather_than_being_refused(self, tmp_path):
        # A response with no data is a finding, not a parse failure.
        match = registry.select(bundle(tmp_path, LETTER))
        assert match is not None
        assert match.adapter.retailer_id == "generic"
        assert match.is_fallback

    def test_the_fallback_never_beats_a_real_adapter(self, tmp_path):
        match = registry.select(
            bundle(tmp_path, KROGER_FIXTURE.read_text(encoding="utf-8"))
        )
        assert match.adapter.retailer_id == "kroger"
        assert not match.is_fallback

    def test_a_fallback_match_is_reported_as_a_guess(self, tmp_path):
        assert not registry.select(bundle(tmp_path, LETTER)).is_confident

    def test_nothing_readable_selects_nothing(self, tmp_path):
        missing = SourceDocument("gone.pdf", "0" * 64, path=str(tmp_path / "gone.pdf"))
        assert registry.select(SourceBundle(documents=(missing,))) is None


class TestGenericParse:
    def test_a_letter_produces_a_complete_compliance_row(self, tmp_path):
        result = GenericAdapter().parse(bundle(tmp_path, LETTER))
        assert result.missing_categories() == ()
        assert len(result.disclosures) == len(DisclosureCategory)

    def test_matched_wording_is_partial_and_never_provided(self, tmp_path):
        """A keyword shows a topic came up. It cannot show it was answered.

        Treating the two as the same would put a green cell next to "we take your
        privacy seriously".
        """
        result = GenericAdapter().parse(bundle(tmp_path, LETTER))
        statuses = {d.status for d in result.disclosures}
        assert DisclosureStatus.PROVIDED not in statuses
        assert DisclosureStatus.PARTIAL in statuses

    def test_a_partial_quotes_the_sentence_it_matched(self, tmp_path):
        result = GenericAdapter().parse(bundle(tmp_path, LETTER))
        partial = [d for d in result.disclosures if d.status is DisclosureStatus.PARTIAL]
        assert partial
        for disclosure in partial:
            assert disclosure.evidence
            assert disclosure.evidence in " ".join(LETTER.split())
            assert "decide for yourself" in disclosure.notes

    def test_categories_the_letter_never_mentions_are_absent(self, tmp_path):
        result = GenericAdapter().parse(bundle(tmp_path, LETTER))
        by_category = {d.category: d for d in result.disclosures}
        assert by_category[DisclosureCategory.SOURCES].status is DisclosureStatus.ABSENT

    def test_a_bare_letter_is_absent_across_the_board(self, tmp_path):
        result = GenericAdapter().parse(bundle(tmp_path, BARE_LETTER))
        assert all(d.status is DisclosureStatus.ABSENT for d in result.disclosures)

    def test_it_says_loudly_that_it_is_not_a_real_parse(self, tmp_path):
        result = GenericAdapter().parse(bundle(tmp_path, LETTER))
        assert result.warnings
        assert "no adapter recognised" in result.warnings[0].message.lower()
        assert any(f.kind is FollowUpKind.CLARIFICATION for f in result.follow_ups)

    def test_it_extracts_no_purchases_or_attributes(self, tmp_path):
        # Guessing those out of prose would look authoritative and be wrong.
        result = GenericAdapter().parse(
            bundle(tmp_path, KROGER_FIXTURE.read_text(encoding="utf-8"))
        )
        assert result.transactions == ()
        assert result.identities == ()
        assert result.inferences == ()

    def test_a_declared_retailer_names_the_row(self, tmp_path):
        result = GenericAdapter().parse(
            bundle(tmp_path, LETTER, declared_retailer="Trader Joe's")
        )
        assert result.request.display_name == "Trader Joe's"

    def test_an_undeclared_retailer_is_named_honestly(self, tmp_path):
        result = GenericAdapter().parse(bundle(tmp_path, LETTER))
        assert result.request.display_name == "Unidentified retailer"

    def test_a_no_data_response_is_flagged_for_querying(self, tmp_path):
        text = "Dear Customer,\n\nWe have no record of an account for you.\n"
        result = GenericAdapter().parse(bundle(tmp_path, text))
        assert any("loyalty account" in f.description for f in result.follow_ups)

    def test_nothing_readable_raises_a_message_for_a_person(self, tmp_path):
        missing = SourceDocument("gone.pdf", "0" * 64, path=str(tmp_path / "gone.pdf"))
        with pytest.raises(AdapterError, match="scanned PDF"):
            GenericAdapter().parse(SourceBundle(documents=(missing,)))


class TestStubs:
    @pytest.mark.parametrize("adapter", [SafewayAdapter(), HMartAdapter()])
    def test_a_stub_never_claims_a_bundle(self, adapter, tmp_path):
        # Winning a sniff() it cannot honour would take the response away from
        # the fallback, which can at least record what is missing.
        assert adapter.sniff(bundle(tmp_path, LETTER)) == 0.0

    @pytest.mark.parametrize("adapter", [SafewayAdapter(), HMartAdapter()])
    def test_a_stub_explains_itself_when_called_directly(self, adapter, tmp_path):
        with pytest.raises(AdapterError, match="not been written yet"):
            adapter.parse(bundle(tmp_path, LETTER))

    @pytest.mark.parametrize("adapter", [SafewayAdapter(), HMartAdapter()])
    def test_a_stub_declares_schema_version_zero(self, adapter):
        # 0 means "no format observed yet"; the first real parse bumps it to 1.
        assert adapter.schema_version == 0

    def test_every_adapter_ships_notes(self):
        root = Path(__file__).parent.parent / "src" / "unbagged" / "adapters"
        for adapter in registry.all():
            notes = root / adapter.retailer_id / "NOTES.md"
            assert notes.is_file(), adapter.retailer_id
            assert len(notes.read_text(encoding="utf-8")) > 400, adapter.retailer_id


class TestAuthoringGuide:
    """The M7 acceptance criterion: a contributor can add a retailer without
    touching core code, and the guide tells them how."""

    GUIDE = Path(__file__).parent.parent / "docs" / "writing-an-adapter.md"

    def test_the_guide_exists_and_covers_the_five_rules(self):
        text = self.GUIDE.read_text(encoding="utf-8")
        for phrase in (
            "carries provenance",
            "Never mutate a value",
            "Absence is a finding",
            "Degrade, do not crash",
            "synthetic fixture",
        ):
            assert phrase in text, phrase

    def test_the_guide_warns_against_attaching_a_real_report(self):
        text = self.GUIDE.read_text(encoding="utf-8")
        assert "Do not attach your report" in text
        assert "unbagged sanitize" in text

    def test_the_guide_names_the_one_core_file_to_edit(self):
        # Adding a retailer touches adapters/__init__.py and nothing else.
        assert "adapters/__init__.py" in self.GUIDE.read_text(encoding="utf-8")

    def test_no_core_module_branches_on_a_retailer(self):
        """Nothing in the core reads a retailer_id.

        Checked against code rather than text: naming Kroger in a docstring as
        the worked example is useful, while a string literal "kroger" reachable
        by an `if` is the abstraction leaking.
        """
        core = Path(__file__).parent.parent / "src" / "unbagged"
        retailers = {"kroger", "safeway", "hmart"}
        for module in ("views.py", "api.py", "repository.py", "models.py",
                       "letters.py", "ingest.py", "extraction.py", "db.py"):
            tree = ast.parse((core / module).read_text(encoding="utf-8"))
            docstrings = {
                node.body[0].value
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node not in docstrings
                ):
                    assert node.value.lower() not in retailers, (
                        f"{module} has a live reference to {node.value!r}"
                    )
