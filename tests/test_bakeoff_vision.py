"""The bake-off's record of its own provenance.

The measuring needs a host and a pulled model, so it is not a test. What IS
testable, and matters as much as the numbers, is what a saved run says about
where it came from: a score belongs to a model on a day on a machine, and
`NOTES.md`'s own closing paragraph warns that a re-pulled tag can be a different
build. A results file that cannot name the build it measured cannot answer the
question the table tells you to ask.

The second half is the reading: files written before there was a header are the
only copy of runs measured in hours, and tidying the format up must not throw
them away.
"""

import json

import pytest
from tools import bakeoff_vision as bv


@pytest.fixture
def host_answering(monkeypatch):
    """A host that answers `/api/version` and `/api/tags`, and nothing else."""
    bodies = {
        "/api/version": {"version": "0.33.3"},
        "/api/tags": {
            "models": [
                {"name": "minicpm-v4.5:8b", "digest": "0123456789abcdef0123"},
                {"name": "not-measured:1b", "digest": "ffffffffffffffffffff"},
            ]
        },
    }

    def fake_get(url, timeout):
        for path, body in bodies.items():
            if url.endswith(path):
                return body
        raise AssertionError(f"asked for {url}, which provenance has no business fetching")

    monkeypatch.setattr(bv, "_get", fake_get)
    return bodies


class TestASavedRunNamesWhatProducedIt:
    def test_it_records_the_host_the_versions_and_the_date(self, host_answering):
        header = bv.provenance("http://10.0.0.2:11434", 1.0)

        assert header["host"] == "http://10.0.0.2:11434"
        assert header["ollama"] == "0.33.3"
        assert header["unbagged"]
        assert header["measured"].startswith("20")

    def test_the_header_starts_with_no_digests(self, host_answering):
        """They are filled per model, next to the model they describe.

        A snapshot of every digest before the matrix records what the host held
        at the START. A tag re-pulled during a run that takes hours is then
        measured with new weights and saved under the old build's digest, which
        attributes results to something that did not produce them — worse than
        no digest, because it reads as evidence.
        """
        assert bv.provenance("http://h:1", 1.0)["digests"] == {}

    def test_a_digest_is_read_for_the_model_asked_about(self, host_answering):
        """The field prose cannot keep up with.

        A tag is the same string across a re-pull and the weights behind it need
        not be. Nothing else recorded here can tell a re-run that it measured
        something other than what the table says.
        """
        assert bv.digest_for("http://h:1", "minicpm-v4.5:8b", 1.0) == "0123456789ab"

    def test_a_model_the_host_does_not_have_reads_as_no_digest(self, host_answering):
        assert bv.digest_for("http://h:1", "never-pulled:1b", 1.0) == ""

    def test_a_host_that_cannot_say_costs_a_field_and_not_the_run(self, monkeypatch):
        """An older server answers neither, and the matrix still runs.

        Provenance is worth recording and not worth refusing to measure over.
        """

        def no(url, timeout):
            raise OSError("no such endpoint")

        monkeypatch.setattr(bv, "_get", no)

        header = bv.provenance("http://h:1", 1.0)

        assert header["ollama"] is None
        assert header["digests"] == {}
        assert header["host"] == "http://h:1"
        assert bv.digest_for("http://h:1", "minicpm-v4.5:8b", 1.0) == ""


class TestReplayReadsBothShapes:
    """`--from` re-renders a table without asking a model anything."""

    def _row(self) -> dict:
        return {
            "model": "minicpm-v4.5:8b",
            "tier": "A",
            "case": "plain",
            "seconds": 8.2,
            "returned": 3,
            "matched": 3,
            "expected": 3,
            "recall": 1.0,
            "furniture": 0,
            "strict": 1.0,
            "marks": [],
            "total_seen": True,
            "total_ok": True,
            "signs_ok": True,
            "accepted": True,
            "gate_ok": True,
            "failed": "",
        }

    def test_a_file_with_a_header_reports_and_names_its_host(self, tmp_path, capsys):
        saved = tmp_path / "results.json"
        saved.write_text(
            json.dumps(
                {
                    "measured": "2026-09-16T00:00:00+00:00",
                    "host": "http://10.0.0.2:11434",
                    "ollama": "0.33.3",
                    "unbagged": "0.16.0",
                    "digests": {"minicpm-v4.5:8b": "0123456789ab"},
                    "results": [self._row()],
                }
            )
        )

        assert bv.main(["--from", str(saved)]) == 0

        printed = capsys.readouterr().out
        assert "0.33.3" in printed
        assert "minicpm-v4.5:8b" in printed

    def test_a_bare_list_from_before_the_header_still_reads(self, tmp_path, capsys):
        """Written by a build that recorded none. Those runs cost hours."""
        saved = tmp_path / "old.json"
        saved.write_text(json.dumps([self._row()]))

        assert bv.main(["--from", str(saved)]) == 0

        printed = capsys.readouterr().out
        assert "minicpm-v4.5:8b" in printed
        assert "no header" in printed


class TestReplayRefusesWhatIsNotARun:
    """A corrupted or unrelated file must not report as a successful empty run.

    `saved.get("results") or []` accepted any JSON object at all, rendered a
    table of nothing, and exited 0 — so a write cut off mid-run, or a file that
    was never a bake-off, looked exactly like a clean measurement of nothing.
    That is the shape of failure this tool exists to refuse. Caught by review
    on #97.
    """

    def _write(self, tmp_path, payload):
        path = tmp_path / "saved.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def test_a_header_with_no_results_list_is_refused(self, tmp_path, capsys):
        path = self._write(tmp_path, {"measured": "2026-09-16T00:00:00+00:00", "host": "h"})

        assert bv.main(["--from", path]) == 2
        assert "no `results` list" in capsys.readouterr().err

    def test_a_header_whose_results_is_not_a_list_is_refused(self, tmp_path, capsys):
        path = self._write(tmp_path, {"host": "h", "results": {"model": "x"}})

        assert bv.main(["--from", path]) == 2
        assert "no `results` list" in capsys.readouterr().err

    def test_an_empty_run_is_refused(self, tmp_path, capsys):
        """No run this tool produces is empty: a model that answered nothing
        still records a row per case saying so.
        """
        path = self._write(tmp_path, {"host": "h", "results": []})

        assert bv.main(["--from", path]) == 2
        assert "no measurements" in capsys.readouterr().err

    def test_an_empty_bare_list_is_refused_too(self, tmp_path, capsys):
        path = self._write(tmp_path, [])

        assert bv.main(["--from", path]) == 2
        assert "no measurements" in capsys.readouterr().err

    def test_json_that_is_neither_shape_is_refused(self, tmp_path, capsys):
        path = self._write(tmp_path, "not a run at all")

        assert bv.main(["--from", path]) == 2
        assert "not a saved run" in capsys.readouterr().err
