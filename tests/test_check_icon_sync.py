"""The icon-sync gate, including every way it is supposed to fail.

The failure this exists to catch: edit a source SVG, run `make brand`, push.
`build_brand --check` is green because the served copies faithfully match the
rasters in `resources/` — which are a version behind, because nothing re-runs
`build_icons.py`. Stale icons ship and every gate passes.

A gate nothing exercises is the shape this repository has shipped three times
(`make_fixtures --check` compared only the filenames it generated;
`build_brand --check` compared its own output to itself; and this). So the
failing side is tested first here, and the passing side second.
"""

import subprocess

import pytest
from tools import check_icon_sync as sync


class TestTheGateFails:
    def test_a_source_changed_with_nothing_generated(self, capsys):
        """The exact filed failure, in one assertion."""
        assert sync.run(["resources/unbagged-logo.svg"]) == 1
        assert "nothing it produces did" in capsys.readouterr().err

    def test_it_names_the_source_that_moved(self, capsys):
        sync.run(["resources/unbagged-logo-small.svg", "src/unbagged/api.py"])
        assert "STALE  resources/unbagged-logo-small.svg" in capsys.readouterr().err

    def test_it_says_how_to_fix_it(self, capsys):
        """Being told a gate failed without being told the command is worse than
        no gate: the reader's next move is to look for a way to skip it."""
        sync.run(["resources/unbagged-logo.svg"])
        err = capsys.readouterr().err
        assert "build_icons.py" in err
        assert "make brand" in err
        assert "pip install cairosvg" in err

    def test_a_bare_escape_marker_does_not_count(self, capsys):
        """A reason is required, like the PII scanner's suppressions."""
        assert sync.run(["resources/unbagged-logo.svg"], reasons={}) == 1

    def test_a_raster_from_the_other_source_does_not_count(self, capsys):
        """The aggregate version of this gate passed here.

        Edit the full logo, touch a favicon belonging to the small one, and a
        single-pool check sees "a source moved and a generated file moved" and
        waves it through, leaving icon-512 and the served apple-touch-icon
        stale. Raised by a Greptile review of PR #31.
        """
        assert sync.run([
            "resources/unbagged-logo.svg",
            "resources/favicon-16.png",
        ]) == 1
        err = capsys.readouterr().err
        assert "STALE  resources/unbagged-logo.svg" in err
        assert "resources/icon-512.png" in err

    def test_a_reason_for_one_source_does_not_excuse_the_other(self):
        """Reasons are keyed by source. A range-wide flag flattened every commit
        message into one yes, so a render-neutral edit in an early commit waved
        through a later one that moved pixels."""
        assert sync.run(
            ["resources/unbagged-logo.svg", "resources/unbagged-logo-small.svg"],
            reasons={"resources/unbagged-logo.svg": "whitespace only"},
        ) == 1

    def test_a_source_not_in_the_mapping_is_refused(self, capsys):
        """An unmapped source is an unwatched source, which is the whole bug."""
        assert sync.run(["resources/some-new-mark.svg"]) == 1
        assert "UNMAPPED" in capsys.readouterr().err


class TestTheGatePasses:
    def test_a_source_and_its_own_rasters_moving_together(self, capsys):
        assert sync.run([
            "resources/unbagged-logo.svg",
            "resources/icon-512.png",
        ]) == 0
        assert "each with what it produces" in capsys.readouterr().out

    def test_both_sources_each_with_their_own(self):
        assert sync.run([
            "resources/unbagged-logo.svg",
            "resources/apple-touch-icon-180.png",
            "resources/unbagged-logo-small.svg",
            "resources/favicon.ico",
        ]) == 0

    def test_the_ico_counts_as_generated(self):
        assert sync.run([
            "resources/unbagged-logo-small.svg",
            "resources/favicon.ico",
        ]) == 0

    def test_a_change_touching_no_source(self):
        assert sync.run(["src/unbagged/api.py", "README.md"]) == 0

    def test_a_raster_moving_on_its_own(self):
        """Deliberately not symmetric. Re-running the generator under a newer
        cairosvg moves the bytes without touching a source, which is not the
        mistake being caught."""
        assert sync.run(["resources/icon-512.png"]) == 0

    def test_an_svg_outside_resources_is_not_watched(self):
        """frontend/public/ holds served copies, which build_brand already
        checks against resources/. Watching them here would fail every brand
        change twice."""
        assert sync.run(["frontend/public/unbagged-logo.svg"]) == 0

    def test_an_escape_naming_its_source(self, capsys):
        """An edit that renders identically leaves nothing to commit, so the
        gate has to have an out or it cannot be satisfied at all."""
        assert sync.run(
            ["resources/unbagged-logo.svg"],
            reasons={"resources/unbagged-logo.svg": "reformatted, renders identically"},
        ) == 0
        assert "allowed by" in capsys.readouterr().out


class TestTheEscapeIsReadFromCommits:
    def _repo(self, tmp_path, monkeypatch):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
        (tmp_path / "seed").write_text("1")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
        subprocess.run(["git", "branch", "-q", "base"], cwd=tmp_path, check=True)
        monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
        return tmp_path

    def _commit(self, repo, message):
        (repo / "seed").write_text(message)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)

    def test_a_reason_naming_its_source_is_found(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path, monkeypatch)
        self._commit(
            repo,
            "tidy the artwork\n\nicons-unchanged: unbagged-logo.svg whitespace only",
        )
        assert sync.escape_reasons("base") == {
            "resources/unbagged-logo.svg": "whitespace only"
        }

    def test_a_bare_marker_is_not_a_reason(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path, monkeypatch)
        self._commit(repo, "tidy\n\nicons-unchanged:")
        assert sync.escape_reasons("base") == {}

    def test_a_marker_naming_no_source_is_refused(self, tmp_path, monkeypatch):
        """The unscoped form excused every source in the range."""
        repo = self._repo(tmp_path, monkeypatch)
        self._commit(repo, "tidy\n\nicons-unchanged: whitespace only")
        assert sync.escape_reasons("base") == {}

    def test_a_marker_naming_an_unknown_file_is_refused(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path, monkeypatch)
        self._commit(repo, "tidy\n\nicons-unchanged: not-a-source.svg because reasons")
        assert sync.escape_reasons("base") == {}

    def test_no_marker_finds_nothing(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path, monkeypatch)
        self._commit(repo, "an ordinary commit")
        assert sync.escape_reasons("base") == {}


class TestTheWatchedSetMatchesTheDocumentedContract:
    def test_every_source_svg_in_resources_is_watched(self):
        """`resources/README.md` says two source SVGs, everything else generated
        from them. If a third arrives, this fails and someone decides whether it
        is a source or an asset rather than it silently going unwatched."""
        from pathlib import Path

        svgs = sorted(p.name for p in (Path("resources")).glob("*.svg"))
        assert svgs == ["unbagged-logo-small.svg", "unbagged-logo.svg"], svgs

    def test_the_mapping_matches_what_the_generator_writes(self):
        """PRODUCES has to name every file build_icons.py writes. Read rather
        than imported: build_icons.py runs its whole pipeline at import time, so
        importing it here would render six files into the working directory."""
        from pathlib import Path

        source = Path("resources/build_icons.py").read_text()
        mapped = {name for names in sync.PRODUCES.values() for name in names}

        written = set()
        for line in source.splitlines():
            if '.save("' in line:
                written.add("resources/" + line.split('.save("')[1].split('"')[0])
            elif '.save(f"' in line:
                pattern = line.split('.save(f"')[1].split('"')[0]
                for size in (16, 32, 48):
                    written.add("resources/" + pattern.replace("{size}", str(size)))

        assert written == mapped, (
            f"build_icons.py writes {sorted(written - mapped)} that nothing watches, "
            f"and PRODUCES claims {sorted(mapped - written)} that it never writes"
        )

    def test_each_raster_is_attributed_to_the_source_that_renders_it(self):
        """The attribution, not just the coverage. build_icons.py renders the
        favicons and the .ico from SMALL, the touch icon and 512 from FULL.
        Swapping them would make the gate accept exactly the stale pairing it
        was rewritten to reject, while this file still listed every name."""
        small = sync.PRODUCES["resources/unbagged-logo-small.svg"]
        full = sync.PRODUCES["resources/unbagged-logo.svg"]
        assert "resources/favicon.ico" in small
        assert "resources/favicon-16.png" in small
        assert "resources/icon-512.png" in full
        assert "resources/apple-touch-icon-180.png" in full

    def test_the_derived_suffixes_cover_what_the_generator_writes(self):
        """Read rather than imported: build_icons.py runs its whole pipeline at
        import time, so importing it here would render six files into the test
        run's working directory."""
        from pathlib import Path

        source = Path("resources/build_icons.py").read_text()
        written = {
            line.split('.save("')[1].split('"')[0]
            for line in source.splitlines()
            if '.save("' in line
        }
        written |= {
            line.split('.save(f"')[1].split('"')[0].replace("{size}", "16")
            for line in source.splitlines()
            if '.save(f"' in line
        }
        for name in written:
            assert name.endswith(sync.DERIVED_SUFFIXES), (
                f"build_icons.py writes {name}, which the gate does not watch"
            )


@pytest.mark.parametrize("base", ["origin/main", "HEAD~1"])
def test_the_command_line_takes_a_base_ref(base, monkeypatch):
    """main() is what CI calls; nothing asserted the argparse wiring."""
    seen = {}
    monkeypatch.setattr(sync, "changed_paths", lambda b: seen.setdefault("base", b) and [])
    monkeypatch.setattr(sync, "escape_reasons", lambda b: [])
    assert sync.main([base]) == 0
    assert seen["base"] == base
