"""The format quirks, isolated from the adapter that consumes them."""

import json
from pathlib import Path

import pytest

from unbagged.adapters.kroger import reader

FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)


@pytest.fixture(scope="module")
def raw() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cleaned(raw):
    return reader.strip_page_markers(raw)


class TestPageMarkers:
    def test_markers_are_removed(self, raw, cleaned):
        clean, pages = cleaned
        assert len(clean) < len(raw)
        assert len(pages.breaks) > 100

    def test_removal_loses_no_data(self, raw, cleaned):
        clean, _ = cleaned
        # The count of baskets in the cleaned JSON must equal the count of
        # orderno keys in the untouched text. Silently dropping a basket while
        # stripping page numbers is the failure mode that matters here.
        assert clean.count('"orderno"') == raw.count('"orderno"')

    def test_page_numbers_are_recoverable_from_offsets(self, cleaned):
        clean, pages = cleaned
        assert pages.page_of(0) >= 1
        offsets = [at for at, _ in pages.breaks]
        numbers = [pages.page_of(at) for at in offsets]
        assert numbers == sorted(numbers)

    def test_a_bare_number_in_the_json_is_not_mistaken_for_a_page(self):
        """The hazard recorded in NOTES.md.

        The documented strip is a regex over bare-number lines, which also eats a
        pretty-printed array element. Belonging to a run of consecutive numbers
        is what separates a page marker from a value.
        """
        text = (
            'Email Information\n'
            '{\n'
            '  "scores": [\n'
            '    7,\n'
            '    9\n'
            '  ]\n'
            '}\n'
            '  2\n'
            'Information about your purchases:\n'
            '{"customer": [{"basket": []}]}\n'
            '  3\n'
            'End of report.\n'
        )
        clean, pages = reader.strip_page_markers(text)
        assert reader.find_blobs(clean)[0].data["scores"] == [7, 9]
        # 2 and 3 form a run; the 9 sitting in an array does not join it.
        assert [n for _, n in pages.breaks] == [2, 3]

    def test_an_unevidenced_marker_is_left_in_rather_than_risked(self):
        # A document too short to print a run keeps its numbers. Leaving two
        # stray lines in is recoverable; silently deleting a value is not.
        text = 'Email Information\n{"scores": [\n    9\n  ]}\n  2\n'
        clean, pages = reader.strip_page_markers(text)
        assert clean == text
        assert pages.breaks == ()

    def test_the_naive_strip_would_have_eaten_it(self):
        # Kept as evidence for why the stricter rule exists at all.
        import re
        text = '{\n  "scores": [\n    7,\n    9\n  ]\n}\n'
        naive = re.sub(r"\n\s*\d{1,3}\r?\n", "\n", text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(naive)

    def test_a_lone_bare_number_is_not_a_page_sequence(self):
        # One number proves nothing. Two consecutive ones are evidence.
        text = '{\n  "scores": [\n    9\n  ]\n}\n'
        clean, pages = reader.strip_page_markers(text)
        assert clean == text
        assert pages.breaks == ()

    def test_text_with_no_markers_is_unchanged(self):
        text = "Information about your purchases:\n{}\n"
        clean, pages = reader.strip_page_markers(text)
        assert clean == text
        assert pages.breaks == ()


class TestSections:
    def test_all_documented_headers_are_found_in_order(self, cleaned):
        clean, _ = cleaned
        headers = [s.header for s in reader.find_sections(clean)]
        assert headers == list(reader.SECTION_HEADERS)

    def test_unrecognised_text_stays_with_its_section(self, cleaned):
        # An adapter that silently discards a region it did not recognise cannot
        # report that it failed to read it.
        clean, _ = cleaned
        sections = reader.find_sections(clean)
        rebuilt = clean[: sections[0].start - len(sections[0].header)]
        for section in sections:
            rebuilt += section.header + section.body
        assert rebuilt == clean

    def test_no_headers_means_no_sections(self):
        assert reader.find_sections("Dear customer, we have no data for you.") == []


class TestBlobs:
    def test_four_blobs_are_recovered(self, cleaned):
        clean, _ = cleaned
        assert len(reader.find_blobs(clean)) == 4

    def test_each_blob_is_attributed_to_its_header(self, cleaned):
        clean, _ = cleaned
        headers = [b.header for b in reader.find_blobs(clean)]
        assert headers == [
            reader.LOYALTY_HEADER,
            reader.ADVERTISING_HEADER,
            reader.EMAIL_HEADER,
            "Information about your purchases:",
        ]

    def test_the_purchase_blob_is_findable_under_either_header(self, cleaned):
        clean, _ = cleaned
        blobs = reader.find_blobs(clean)
        blob = reader.blob_for_header(blobs, *reader.PURCHASE_HEADERS)
        assert blob is not None
        assert "basket" in blob.data["customer"][0]

    def test_braces_inside_strings_do_not_confuse_the_scanner(self):
        text = 'Email Information\n{"note": "we use {} as a placeholder"}\n'
        blobs = reader.find_blobs(text)
        assert len(blobs) == 1
        assert blobs[0].data["note"].endswith("placeholder")

    def test_escaped_quotes_do_not_confuse_the_scanner(self):
        text = 'Email Information\n{"note": "he said \\"hello\\" and left"}\n'
        assert reader.find_blobs(text)[0].data["note"].startswith("he said")

    def test_a_corrupt_blob_is_skipped_not_raised(self):
        text = (
            'Data we hold related to our Loyalty program:\n'
            '{"loyaltyno": "123", }\n'
            'Information about your purchases:\n'
            '{"customer": [{"basket": []}]}\n'
        )
        blobs = reader.find_blobs(text)
        # One corrupt section must not cost the other.
        assert len(blobs) == 1
        assert blobs[0].header == "Information about your purchases:"
        assert len(reader.unparseable_spans(text)) == 1

    def test_no_json_at_all_is_not_an_error(self):
        assert reader.find_blobs("Dear customer, we hold no data about you.") == []
