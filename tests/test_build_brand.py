"""The brand generator, including every way it is supposed to fail.

Its own drift check had exactly one test — `run(check=True) == 0` — which walks
the happy path and proves nothing about the failure modes the tool exists to
detect. A guard that always returned 0 would have passed. That is the shape this
repo has already been bitten by twice, and `tests/test_check_lock.py` names it in
its own docstring, so these mirror that suite.

It is not hypothetical here: the stray scan used `iterdir()` and skipped
subdirectories entirely, so a hand-copied source at `frontend/public/sub/x.svg`
shipped with its full C2PA manifest while `make brand-check` reported success.
Nothing could see it, because nothing tested the failing side.

These live outside `test_frontend_build.py` on purpose: that module skips
wholesale when the frontend has not been built, so `make test` never ran the
drift assertion locally. Source-to-served drift has nothing to do with the built
output and should fail on a plain `make test`.
"""

import io
import shutil
import struct

import pytest
from PIL import Image, ImageChops
from tools import build_brand


@pytest.fixture
def public(tmp_path, monkeypatch):
    """A throwaway copy of the served directory.

    `run()` reports paths with `relative_to(REPO_ROOT)`, so patching PUBLIC_DIR
    alone raises ValueError instead of failing usefully. Both move together.
    """
    root = tmp_path / "frontend" / "public"
    root.parent.mkdir(parents=True)
    shutil.copytree(build_brand.PUBLIC_DIR, root)
    monkeypatch.setattr(build_brand, "PUBLIC_DIR", root)
    monkeypatch.setattr(build_brand, "REPO_ROOT", tmp_path)
    return root


class TestTheCleanTree:
    def test_the_committed_assets_match_their_sources(self):
        assert build_brand.run(check=True) == 0

    def test_every_served_asset_is_one_the_tool_produces(self):
        produced = set(build_brand.build())
        on_disk = {p.name for p in build_brand.PUBLIC_DIR.iterdir() if p.is_file()}
        assert on_disk == produced


class TestDriftIsDetected:
    def test_a_hand_copied_source_is_drift(self, public):
        # The exact mistake the tool exists to stop: copying resources/ across
        # by hand, which ships the C2PA manifest the strip removes.
        (public / "unbagged-logo.svg").write_bytes(
            (build_brand.SOURCE_DIR / "unbagged-logo.svg").read_bytes()
        )
        assert build_brand.run(check=True) == 1

    def test_a_deleted_served_file_is_drift(self, public):
        (public / "favicon.ico").unlink()
        assert build_brand.run(check=True) == 1

    def test_a_stray_at_the_top_level_is_caught(self, public):
        (public / "leftover.svg").write_bytes(b"<svg/>")
        assert build_brand.run(check=True) == 1

    def test_a_stray_in_a_subdirectory_is_caught(self, public):
        """The hole this suite was written for.

        Vite copies `public/` recursively into the build root, so a file here
        ships. The scan walked only the top level, so this returned 0.
        """
        (public / "vendor").mkdir()
        (public / "vendor" / "smuggled.js").write_bytes(b"console.log(1)")
        assert build_brand.run(check=True) == 1

    def test_a_missing_source_stops_rather_than_reporting_clean(self, monkeypatch):
        monkeypatch.setattr(build_brand, "SOURCE_DIR", build_brand.REPO_ROOT / "nope")
        with pytest.raises(SystemExit):
            build_brand.run(check=True)


class TestWriteMode:
    def test_it_writes_every_served_asset(self, public, capsys):
        for existing in public.iterdir():
            existing.unlink()
        assert build_brand.run(check=False) == 0
        assert {p.name for p in public.iterdir()} == set(build_brand.SERVED)

    def test_it_reports_a_stray_rather_than_walking_past_it(self, public, capsys):
        # Strays used to be computed and discarded on this path, so `make brand`
        # said nothing and CI delivered the news instead.
        (public / "leftover.svg").write_bytes(b"<svg/>")
        build_brand.run(check=False)
        assert "STRAY" in capsys.readouterr().err


class TestTheStrip:
    def test_the_svg_strip_removes_the_manifest_and_its_namespace(self):
        out = build_brand.build_one("unbagged-logo.svg")
        assert b"<metadata>" not in out
        assert b"c2pa" not in out.lower()
        # The geometry survives: without this the test passes on an empty file.
        assert out.count(b"<path") + out.count(b"<circle") + out.count(b"<rect") > 0

    def test_the_svg_strip_is_idempotent(self):
        once = build_brand.build_one("unbagged-logo.svg")
        assert build_brand.strip_svg(once) == once

    def test_the_png_strip_keeps_every_pixel(self):
        source = Image.open(build_brand.SOURCE_DIR / "apple-touch-icon-180.png")
        stripped = Image.open(io.BytesIO(build_brand.build_one("apple-touch-icon-180.png")))
        assert source.size == stripped.size
        difference = ImageChops.difference(
            source.convert("RGBA"), stripped.convert("RGBA")
        )
        assert difference.getbbox() is None

    def test_the_png_strip_leaves_no_ancillary_chunks(self):
        """Asserted by chunk type, not by grepping for two magic strings.

        Re-saving drops most ancillary chunks as a side effect but re-emits an
        iCCP read from the source, and an ICC profile carries free-text fields
        that neither `c2pa` nor `jumd` would match.
        """
        data = build_brand.build_one("apple-touch-icon-180.png")
        chunks, offset = [], 8
        while offset < len(data):
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            chunks.append(data[offset + 4 : offset + 8].decode("ascii"))
            offset += 12 + length
        assert set(chunks) <= {"IHDR", "IDAT", "IEND", "PLTE", "tRNS"}, chunks

    def test_a_suffix_with_no_strip_passes_through_unchanged(self):
        assert build_brand.build_one("favicon.ico") == (
            build_brand.SOURCE_DIR / "favicon.ico"
        ).read_bytes()


class TestTheCommandLine:
    def test_check_is_wired_to_the_flag(self):
        # The Make targets and CI call main(), not run(); nothing asserted the
        # argparse wiring between them.
        assert build_brand.main(["--check"]) == 0


class TestSymlinksAreNotABlindSpot:
    """`Path.rglob` yields a symlinked directory but never descends it.

    Switching `iterdir` to `rglob` closed the subdirectory hole and left this
    one open, which is worse than the original bug: `public/vendor ->
    ../../resources` reported "4 served asset(s) match their sources" while
    Vite's copyDir, which stats through symlinks, copied every raw
    manifest-bearing source into the build.
    """

    def test_a_symlinked_directory_is_caught(self, public, capsys):
        (public / "vendor").symlink_to(build_brand.SOURCE_DIR)
        assert build_brand.run(check=True) == 1
        assert "SYMLINK" in capsys.readouterr().err

    def test_a_symlinked_directory_does_not_hide_what_it_contains(self, public, capsys):
        # The old scan exited 0 here. Naming the link is enough; what matters is
        # that the exit code is not success.
        (public / "vendor").symlink_to(build_brand.SOURCE_DIR)
        assert build_brand.run(check=True) != 0

    def test_a_symlinked_file_is_caught(self, public, capsys):
        (public / "sneaky.svg").symlink_to(build_brand.SOURCE_DIR / "unbagged-logo.svg")
        assert build_brand.run(check=True) == 1
        assert "SYMLINK" in capsys.readouterr().err

    def test_write_mode_does_not_write_through_a_symlink(self, public, tmp_path):
        # write_bytes on a symlink puts the bytes wherever it points, which can
        # be outside the repository entirely.
        outside = tmp_path / "outside.svg"
        outside.write_bytes(b"untouched")
        target = public / "unbagged-logo.svg"
        target.unlink()
        target.symlink_to(outside)
        build_brand.run(check=False)
        assert outside.read_bytes() == b"untouched"
        assert not target.is_symlink()


class TestStraysThatAreEasyToMiss:
    def test_a_dotfile_is_caught(self, public, capsys):
        # rglob matches hidden files where a shell glob would not, and Vite
        # copies them into the build. Finder visiting the directory is enough.
        (public / ".DS_Store").write_bytes(b"\x00")
        assert build_brand.run(check=True) == 1
        assert "STRAY" in capsys.readouterr().err

    def test_write_mode_fails_rather_than_reporting_success(self, public, capsys):
        # It used to print the stray to stderr and return 0, so `make brand`
        # exited clean and CI delivered the news.
        (public / "leftover.svg").write_bytes(b"<svg/>")
        assert build_brand.run(check=False) == 1

    def test_the_repair_instruction_admits_make_brand_cannot_clear_a_stray(
        self, public, capsys
    ):
        # The old text said "run `make brand`", which provably does not remove
        # a stray. Being told the wrong fix is worse than being told none.
        (public / "leftover.svg").write_bytes(b"<svg/>")
        build_brand.run(check=True)
        assert "removes nothing" in capsys.readouterr().err


class TestTheStripperBreakingIsVisible:
    """Both sides of an equality check run through this module.

    If the stripper stops matching, `produced == committed` stays true and
    --check stays green while the full manifest ships. Only an assertion about
    the bytes themselves catches that.
    """

    def test_a_stripper_that_stops_matching_is_caught(self, public, monkeypatch, capsys):
        import re

        monkeypatch.setattr(build_brand, "METADATA", re.compile(r"ZZZ-never-matches"))
        monkeypatch.setattr(build_brand, "C2PA_NS", re.compile(r"ZZZ-never-matches"))
        assert build_brand.run(check=True) == 1
        assert "UNSTRIPPED" in capsys.readouterr().err

    def test_the_metadata_pattern_matches_a_tag_carrying_attributes(self):
        # Inkscape writes `<metadata id="metadata7">`. A bare-tag pattern misses
        # it, and then the whole manifest ships.
        svg = b'<svg><metadata id="metadata7"><x/></metadata><path d="M0 0"/></svg>'
        assert b"metadata" not in build_brand.strip_svg(svg)

    @pytest.mark.parametrize(
        "svg, why",
        [
            (b'<svg><script>alert(1)</script></svg>', "a <script> element"),
            (b'<svg><foreignObject><b/></foreignObject></svg>', "a <foreignObject> element"),
            (b'<svg onload="alert(1)"/>', "an inline event handler"),
            (b'<svg><image href="https://elsewhere.example/x.png"/></svg>', "an off-origin href"),
        ],
    )
    def test_an_svg_that_is_more_than_a_picture_is_caught(self, svg, why):
        """A served SVG is a navigable same-origin document, not just artwork.

        The realistic ingress is an optimiser or generator round-trip on the
        logo, not an attacker. The current artwork is clean; nothing enforced
        that it stays clean.
        """
        assert build_brand.uncleanliness("x.svg", svg) == [f"carries {why}"]

    def test_a_clean_svg_has_nothing_to_report(self):
        assert build_brand.uncleanliness("x.svg", b'<svg><path d="M0 0"/></svg>') == []

    def test_an_ico_carrying_a_manifest_in_an_embedded_png_is_caught(self, tmp_path):
        """The .ico is a container of complete PNG streams.

        It is clean today only because build_icons.py re-renders it from
        cairosvg rather than assembling it from resources/favicon-*.png, each of
        which carries a caBX chunk. Nothing in this tool would have noticed if
        that changed.
        """
        source = build_brand.SOURCE_DIR / "favicon.ico"
        data = bytearray(source.read_bytes())
        streams = build_brand.ico_png_streams(bytes(data))
        assert streams, "expected a PNG-backed .ico; the assertion below rests on it"
        assert build_brand.uncleanliness("favicon.ico", bytes(data)) == []
        # A c2pa reference anywhere in the container is a fault on its own.
        assert build_brand.uncleanliness("favicon.ico", bytes(data) + b"c2pa") == [
            "carries a c2pa reference"
        ]


class TestPngHandlingIsNotLossy:
    def test_a_paletted_image_keeps_its_colours(self):
        """`Image.frombytes(mode, size, tobytes())` attaches a default palette.

        `tobytes()` on a P-mode image returns palette *indices*, so the rebuild
        that was supposed to make the strip lossless by construction turned a
        paletted icon black. Verified: an authored (200,100,50) came back
        (0,0,0).
        """
        image = Image.new("P", (4, 4))
        image.putpalette([200, 100, 50] * 256)
        image.putpixel((0, 0), 0)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        out = Image.open(io.BytesIO(build_brand.strip_png(buffer.getvalue())))
        out.load()
        assert out.mode == "P"
        assert out.convert("RGB").getpixel((0, 0)) == (200, 100, 50)

    def test_transparency_survives(self):
        # tRNS is pixel data wearing an `info` key, so emptying `info` to drop
        # the manifest drops it too unless it is put back by hand.
        image = Image.new("P", (4, 4))
        image.putpalette([200, 100, 50] * 256)
        buffer = io.BytesIO()
        image.save(buffer, "PNG", transparency=1)
        out = Image.open(io.BytesIO(build_brand.strip_png(buffer.getvalue())))
        out.load()
        assert out.info.get("transparency") is not None
        assert "tRNS" in [c.decode() for c in build_brand.png_chunks(
            build_brand.strip_png(buffer.getvalue())
        )]

    def test_a_repacked_png_with_identical_pixels_is_not_drift(self, public):
        """--check compares decoded pixels, not compressed bytes.

        zlib output is a property of whichever build the local Pillow wheel
        bundles. A byte comparison put anyone on a platform with no Pillow wheel
        into permanent DRIFT on a file they never touched, with a message
        telling them to run `make brand` — which would then rewrite the
        committed asset and turn everyone else red.
        """
        name = "apple-touch-icon-180.png"
        produced = build_brand.build_one(name)
        image = Image.open(io.BytesIO(produced))
        image.load()
        repacked = io.BytesIO()
        image.save(repacked, "PNG", optimize=False, compress_level=1)
        assert repacked.getvalue() != produced, "expected a different encoding"
        (public / name).write_bytes(repacked.getvalue())
        assert build_brand.run(check=True) == 0


class TestAnUnservableSuffixIsRefused:
    def test_an_unknown_suffix_raises_rather_than_shipping_bytes_verbatim(
        self, monkeypatch, tmp_path
    ):
        """The pass-through used to be the `else` branch for everything.

        Adding a .webp or .jpg to SERVED shipped its EXIF and XMP verbatim with
        --check green, because the comment said ".ico carries no manifest" while
        the code said "anything I do not recognise".
        """
        monkeypatch.setattr(build_brand, "SOURCE_DIR", tmp_path)
        (tmp_path / "photo.webp").write_bytes(b"RIFF....WEBP")
        with pytest.raises(SystemExit, match="no strip rule"):
            build_brand.build_one("photo.webp")
