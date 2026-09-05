"""Release notes come out of the CHANGELOG, or the release does not happen.

Notes retyped at tag time are a second description of the same release, and the
two drift — usually toward the notes being cheerier than the record, because one
is written to announce and the other to remember. Extracting them removes the
opportunity.

The refusals matter more than the extraction. A release whose notes are missing
is obvious; a release whose VERSION file disagrees with its own tag is not, and
the running app would report a version that was never released.
"""

import pytest
from tools import release_notes as notes

SAMPLE = """# Changelog

Preamble that is not part of any release.

## [0.12.0] - 2026-09-05

### Added

- A thing.

## [0.11.0] - 2026-09-05

### Fixed

- An older thing.
"""


class TestExtraction:
    def test_it_takes_the_body_under_the_heading(self):
        body = notes.extract(SAMPLE, "0.12.0")
        assert "### Added" in body
        assert "- A thing." in body

    def test_it_stops_at_the_next_version(self):
        body = notes.extract(SAMPLE, "0.12.0")
        assert "0.11.0" not in body
        assert "An older thing" not in body

    def test_it_excludes_the_preamble(self):
        assert "Preamble" not in notes.extract(SAMPLE, "0.12.0")

    def test_the_last_section_runs_to_the_end(self):
        body = notes.extract(SAMPLE, "0.11.0")
        assert "An older thing" in body

    def test_an_absent_version_is_none_not_empty(self):
        """None and "" mean different things: no such release, versus a release
        described by nothing. Both are refusals, with different messages."""
        assert notes.extract(SAMPLE, "9.9.9") is None

    def test_a_version_that_is_a_prefix_of_another_is_not_confused(self):
        text = "## [0.1.0] - x\n\n- one\n\n## [0.1.0-rc1] - y\n\n- two\n"
        assert "- one" in notes.extract(text, "0.1.0")
        assert "- two" not in notes.extract(text, "0.1.0")


class TestTheTagPrefix:
    @pytest.mark.parametrize("tag", ["v0.12.0", "0.12.0"])
    def test_both_spellings_name_the_same_release(self, tag):
        assert notes.strip_prefix(tag) == "0.12.0"


class TestItRefuses:
    def test_a_tag_the_changelog_does_not_describe(self, capsys, monkeypatch):
        """VERSION has to agree first, or that check fires instead.

        Written the obvious way this test passed against the wrong branch: it
        asserted the CHANGELOG message while actually exercising the VERSION
        mismatch, because v9.9.9 disagrees with both.
        """
        monkeypatch.setattr(notes, "declared_version", lambda: "9.9.9")
        assert notes.run("v9.9.9") == 1
        err = capsys.readouterr().err
        assert "no section for 9.9.9" in err
        # Names what it does have, so the fix is obvious from the failure.
        assert "It describes:" in err

    def test_the_version_check_runs_before_the_changelog_lookup(self, capsys):
        """Order matters for the message. A tag that disagrees with VERSION and
        is also absent from the CHANGELOG should say the former: fix the version
        mismatch and the missing section usually goes with it."""
        assert notes.run("v9.9.9") == 1
        assert "VERSION says" in capsys.readouterr().err

    def test_a_tag_that_disagrees_with_the_version_file(self, capsys, monkeypatch):
        """The one that would otherwise ship silently.

        The app reports `__version__` from the VERSION file, so tagging a commit
        whose file says something else publishes a release whose own software
        contradicts its name.
        """
        monkeypatch.setattr(notes, "declared_version", lambda: "0.11.0")
        assert notes.run("v0.12.0") == 1
        assert "VERSION says 0.11.0" in capsys.readouterr().err

    def test_a_section_that_exists_but_says_nothing(self, capsys, monkeypatch, tmp_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("## [0.12.0] - 2026-09-05\n\n## [0.11.0] - 2026-09-04\n\n- x\n")
        monkeypatch.setattr(notes, "CHANGELOG", changelog)
        monkeypatch.setattr(notes, "declared_version", lambda: "0.12.0")
        assert notes.run("v0.12.0") == 1
        assert "is empty" in capsys.readouterr().err


class TestAgainstTheRealChangelog:
    def test_the_current_version_extracts(self, capsys):
        """The release this repository would cut right now."""
        assert notes.run(f"v{notes.declared_version()}") == 0
        out = capsys.readouterr().out
        assert len(out.splitlines()) > 10, "suspiciously short release notes"

    def test_the_notes_do_not_start_with_a_version_heading(self, capsys):
        """The heading is the release title on GitHub; repeating it in the body
        gives every release page the same line twice."""
        notes.run(f"v{notes.declared_version()}")
        assert not capsys.readouterr().out.lstrip().startswith("## [")


def test_the_command_line_takes_a_tag(capsys):
    assert notes.main([f"v{notes.declared_version()}"]) == 0
    assert capsys.readouterr().out.strip()
