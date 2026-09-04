"""Guards on the packaging configuration.

These assert properties of files rather than of code, because the highest-
consequence line in this repository is a port binding in a YAML file, and a
review will not catch it going missing during an unrelated edit.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
COMPOSE = ROOT / "docker-compose.yml"
DEV_COMPOSE = ROOT / "docker-compose.dev.yml"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


class ComposeLoader(yaml.SafeLoader):
    """A SafeLoader that understands Compose's own tags.

    `!override` and `!reset` are Compose merge directives, not YAML types, and
    safe_load refuses to construct them. A test that cannot read the file it is
    guarding is not a guard, so the loader learns the two tags rather than the
    compose file avoiding them.
    """


ComposeLoader.add_constructor(
    "!override", lambda loader, node: loader.construct_sequence(node)
    if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node)
)
ComposeLoader.add_constructor(
    "!reset", lambda loader, node: None
)


def services(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)["services"]


class TestPortBinding:
    """Without the 127.0.0.1 prefix Docker publishes on every interface and goes
    straight through the host firewall, putting two years of someone's groceries
    on their local network."""

    @pytest.mark.parametrize("path", [COMPOSE, DEV_COMPOSE])
    def test_every_published_port_is_loopback_only(self, path):
        for name, service in services(path).items():
            for published in service.get("ports") or []:
                assert str(published).startswith("127.0.0.1:"), f"{path.name}:{name}"

    def test_the_interface_is_not_configurable_even_though_the_port_is(self):
        """The port became an environment knob; the interface must not.

        `127.0.0.1:` has to sit OUTSIDE the interpolation. If it ever moves
        inside, one stray value in a .env file publishes someone's grocery
        history to their whole network, and nothing would say so.
        """
        for path in (COMPOSE, DEV_COMPOSE):
            for service in services(path).values():
                for published in service.get("ports") or []:
                    text = str(published)
                    prefix = text.split("${", 1)[0]
                    assert prefix.startswith("127.0.0.1:"), (
                        f"{text!r} lets the interface be overridden"
                    )

    def test_the_app_is_published_where_the_readme_says(self):
        ports = [str(p) for p in services(COMPOSE)["unbagged"]["ports"]]
        assert any(p.startswith("127.0.0.1:") and p.endswith(":8000") for p in ports)
        # The default inside the interpolation is what a reader of the README
        # will actually get, so the two must not drift apart.
        assert any("8420" in p for p in ports)
        assert "8420" in (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_configurable_port_is_documented(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "UNBAGGED_PORT" in example
        assert "UNBAGGED_PORT" in COMPOSE.read_text(encoding="utf-8")


class TestDataHandling:
    def test_the_data_directory_is_a_bind_mount_not_a_copy(self):
        # Real reports must never end up in an image layer.
        volumes = services(COMPOSE)["unbagged"]["volumes"]
        assert "./data:/data" in [str(v) for v in volumes]

    def test_the_dockerfile_never_copies_the_data_directory(self):
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("COPY") and "--from=" not in stripped:
                assert not stripped.split()[1].startswith(("data", "./data", "/data"))

    def test_the_dockerignore_excludes_the_data_directory(self):
        entries = {
            line.strip()
            for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        assert {"data/", "output/"} <= entries
        assert "*.pdf" in entries and "*.sqlite" in entries


class TestImageShape:
    def test_privileges_are_dropped_before_the_app_starts(self):
        """The static half of the non-root guarantee.

        This used to grep for `USER unbagged`. That line is gone on purpose: the
        container starts as root so the entrypoint can chown the bind-mounted
        /data, then drops. Grepping for USER would now fail, and "fixing" it by
        deleting the assertion would silently retire the guarantee. So this
        checks the drop exists, and
        tests/container/test_runtime.py::test_the_app_process_runs_as_uid_10001
        checks the effective uid at runtime, which is the claim that matters.
        """
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        assert "exec $as_app" in entrypoint or "exec gosu" in entrypoint
        assert "gosu" in DOCKERFILE.read_text(encoding="utf-8")

    def test_the_runtime_uid_is_asserted_somewhere_that_runs_it(self):
        # Guard against the container tier being deleted and this file being
        # left believing it still covers the non-root guarantee.
        runtime_tests = (ROOT / "tests" / "container" / "test_runtime.py")
        assert runtime_tests.is_file()
        assert "os.getuid()" in runtime_tests.read_text(encoding="utf-8")

    def test_the_ui_is_built_in_a_separate_stage(self):
        # Node exists only in the builder; the runtime gets a directory of files.
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "FROM node:" in text
        assert "COPY --from=frontend" in text
        runtime = text.split("AS runtime", 1)[1]
        assert "npm" not in runtime

    def test_the_default_build_pins_the_runtime_stage(self):
        """A build with no target builds the LAST stage in the Dockerfile.

        The dev stage sits last (it is `FROM runtime`, so it has to). Without an
        explicit target, `docker compose up` built and shipped dev — editable
        install, --reload, and a source tree mounted over the package. Caught by
        the container tier, which found itself watching for file changes in what
        was supposed to be the production image.
        """
        build = services(COMPOSE)["unbagged"]["build"]
        assert isinstance(build, dict), "build must name a target, not just a context"
        assert build.get("target") == "runtime"

        stages = [
            line.split(" AS ")[1].strip()
            for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
            if line.startswith("FROM ") and " AS " in line
        ]
        assert stages[-1] != "runtime", (
            "runtime is now the last stage, so this guard is testing nothing; "
            "either keep dev last or remove the pin deliberately"
        )

    def test_the_default_path_starts_exactly_one_service(self):
        # Individuals are the audience. A Postgres sidecar and a separate
        # frontend container are friction with no payoff at this scale.
        assert list(services(COMPOSE)) == ["unbagged"]

    def test_the_dev_overlay_adds_the_vite_server(self):
        assert "web" in services(DEV_COMPOSE)

    def test_dev_mode_publishes_exactly_one_url(self):
        """Dev used to publish both 5173 and 8420, and 8420 served the UI bundle
        frozen into the image at build time. Nothing distinguished them in a
        browser, so an edit appeared to do nothing."""
        dev = services(DEV_COMPOSE)
        published = [p for s in dev.values() for p in (s.get("ports") or [])]
        assert len(published) == 1, f"dev should publish one URL, got {published}"
        assert "5173" in str(published[0])


class TestVersionIsOneNumber:
    """`VERSION` and what the app reports must be the same string.

    The footer exists for one reason: with no telemetry, no crash reporting and
    no update check, a person filing an issue has no other way to say what they
    are running. A footer that reports a stale number is worse than no footer,
    because it is confidently wrong and it sends the maintainer to the wrong
    commit.

    Nothing asserted this, and it went wrong the first time it could: bumping
    `VERSION` to 0.10.0 left a running dev server reporting 0.9.0, because
    `__version__` reads install-time metadata rather than the file. Nothing in
    the suite noticed.
    """

    def test_the_package_reports_the_version_file(self):
        from unbagged import __version__

        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert __version__ == declared, (
            f"VERSION says {declared} but the package reports {__version__}."
        )

    def test_the_version_file_wins_over_stale_install_metadata(self):
        """The file is authoritative in a checkout, so a bump takes effect
        immediately. This used to require `pip install -e .`, and forgetting it
        left the footer reporting the previous release."""
        from unbagged import _read_version

        original = (ROOT / "VERSION").read_text(encoding="utf-8")
        try:
            (ROOT / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            assert _read_version() == "9.9.9"
        finally:
            (ROOT / "VERSION").write_text(original, encoding="utf-8")

    def test_the_api_reports_the_same_version(self):
        """The footer reads /api/health. A person filing a bug quotes what the
        footer says, so it has to be the same string as everything else."""
        from unbagged import __version__, api

        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert api.app.version == declared == __version__

    def test_the_version_file_is_a_bare_semver_string(self):
        """The Dockerfile and pyproject both read this file directly, so a
        stray comment or a `v` prefix breaks the image build rather than a
        test."""
        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", declared), (
            f"VERSION is {declared!r}; expected a bare semver like 0.10.0"
        )
