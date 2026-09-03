"""Recovering JSON from a document's text.

Both repairs here come from a real report rather than from theory: PDF
extraction wraps long lines, and retailers emit trailing commas.
"""

import json

import pytest

from unbagged.jsonscan import (
    iter_json,
    json_spans,
    repair,
    unparseable_spans,
    unterminated_span,
    walk,
)


class TestFindingObjects:
    def test_nested_objects_are_found_whole(self):
        # A non-greedy `{.*?}` stops at the first closing brace, so it never
        # matches a nested object and silently finds nothing in a real report.
        text = 'prose {"customer": [{"basket": [{"id": 1}]}]} more prose'
        found = [data for _s, _e, data in iter_json(text)]
        assert found == [{"customer": [{"basket": [{"id": 1}]}]}]

    def test_braces_inside_strings_do_not_count(self):
        text = '{"note": "we use {} as a placeholder"}'
        assert next(iter_json(text))[2]["note"].endswith("placeholder")

    def test_escaped_quotes_do_not_end_a_string(self):
        text = r'{"note": "he said \"hello\" and left"}'
        assert next(iter_json(text))[2]["note"].startswith("he said")

    def test_several_objects_are_returned_in_order(self):
        text = '{"a": 1} words {"b": 2}'
        assert [d for _s, _e, d in iter_json(text)] == [{"a": 1}, {"b": 2}]

    def test_prose_with_no_json_yields_nothing(self):
        assert list(iter_json("Dear customer, we hold no data.")) == []


class TestTruncation:
    def test_an_unclosed_object_is_located(self):
        text = 'ok {"a": 1} then {"b": {"c": '
        assert unterminated_span(text) == text.index('{"b"')

    def test_complete_text_reports_no_truncation(self):
        assert unterminated_span('{"a": 1}') is None

    def test_a_truncated_tail_does_not_hide_what_came_before(self):
        text = '{"a": 1} {"b": '
        assert [d for _s, _e, d in iter_json(text)] == [{"a": 1}]


class TestRepair:
    def test_a_newline_inside_a_string_is_joined(self):
        """PDF extraction wraps long lines, including inside JSON string keys."""
        broken = '{"a very long label\nthat wrapped": "3.0"}'
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        assert json.loads(repair(broken)) == {"a very long label that wrapped": "3.0"}

    def test_a_trailing_comma_is_removed(self):
        broken = '{"a": 1, "b": [1, 2,],}'
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        assert json.loads(repair(broken)) == {"a": 1, "b": [1, 2]}

    def test_a_comma_inside_a_string_is_left_alone(self):
        text = '{"a": "one, two"}'
        assert json.loads(repair(text)) == {"a": "one, two"}

    def test_repaired_objects_are_returned_by_iter_json(self):
        text = 'Header\n{"a": 1,}\n'
        assert [d for _s, _e, d in iter_json(text)] == [{"a": 1}]

    def test_it_does_not_invent_structure(self):
        # Anything cleverer starts guessing at what the retailer meant, and a
        # parser that silently invents structure is worse than one that reports
        # it could not read the section.
        assert unparseable_spans('{"a": nonsense}')


class TestWalk:
    def test_every_scalar_is_reached_at_any_depth(self):
        data = {"a": 1, "b": {"c": 2, "d": [{"e": 3}]}}
        assert dict(walk(data)) == {"a": 1, "c": 2, "e": 3}

    def test_spans_line_up_with_the_source(self):
        text = 'xx {"a": 1} yy'
        start, end, _ = next(iter_json(text))
        assert text[start:end] == '{"a": 1}'
        assert json_spans(text) == [(start, end)]
