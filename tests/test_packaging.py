"""Guards on the packaging configuration.

These assert properties of files rather than of code, because the highest-
consequence line in this repository is a port binding in a YAML file, and a
review will not catch it going missing during an unrelated edit.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
COMPOSE = ROOT / "docker-compose.yml"
DEV_COMPOSE = ROOT / "docker-compose.dev.yml"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


class TestPortBinding:
    """Without the 127.0.0.1 prefix Docker publishes on every interface and goes
    straight through the host firewall, putting two years of someone's groceries
    on their local network."""

    @pytest.mark.parametrize("path", [COMPOSE, DEV_COMPOSE])
    def test_every_published_port_is_loopback_only(self, path):
        for name, service in services(path).items():
            for published in service.get("ports", []):
                assert str(published).startswith("127.0.0.1:"), f"{path.name}:{name}"

    def test_the_app_is_published_where_the_readme_says(self):
        ports = services(COMPOSE)["unbagged"]["ports"]
        assert "127.0.0.1:8420:8000" in [str(p) for p in ports]
        assert "8420" in (ROOT / "README.md").read_text(encoding="utf-8")


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
    def test_the_container_does_not_run_as_root(self):
        assert "USER unbagged" in DOCKERFILE.read_text(encoding="utf-8")

    def test_the_ui_is_built_in_a_separate_stage(self):
        # Node exists only in the builder; the runtime gets a directory of files.
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "FROM node:" in text
        assert "COPY --from=frontend" in text
        runtime = text.split("AS runtime", 1)[1]
        assert "npm" not in runtime

    def test_the_default_path_starts_exactly_one_service(self):
        # Individuals are the audience. A Postgres sidecar and a separate
        # frontend container are friction with no payoff at this scale.
        assert list(services(COMPOSE)) == ["unbagged"]

    def test_the_dev_overlay_adds_the_vite_server(self):
        assert "web" in services(DEV_COMPOSE)
