"""Tests for the runtime lock and its drift check.

The lock is what the shipped image installs, under `--require-hashes`. These
assert the properties that make it worth having: it covers what the project
declares, every package is pinned and hashed, and the checker actually fails on
each way the two files drift apart — a checker that only ever passes is the same
shape of problem as the fixtures check this branch already fixed.
"""

from pathlib import Path

import pytest
from tools import check_lock

ROOT = Path(__file__).parent.parent
LOCK = ROOT / "docker" / "requirements.txt"


class TestTheCommittedLock:
    def test_it_exists_and_is_not_empty(self):
        assert LOCK.is_file()
        assert check_lock.locked()

    def test_every_package_is_pinned_and_hashed(self):
        for name, (version, has_hash) in check_lock.locked().items():
            assert version, name
            assert has_hash, f"{name} has no --hash; --require-hashes would refuse the file"

    def test_it_covers_every_declared_runtime_dependency(self):
        pins = check_lock.locked()
        for name, _ in check_lock.declared():
            assert name in pins, f"{name} is declared in pyproject.toml and not locked"

    def test_the_repository_passes_its_own_check(self):
        assert check_lock.findings() == []

    def test_the_uvicorn_standard_extras_are_resolved(self):
        # `uvicorn[standard]` is declared with an extra; --strip-extras means the
        # extra's packages have to appear as their own pins or the image loses
        # the event loop and the websocket support it asks for.
        pins = check_lock.locked()
        for package in ("httptools", "uvloop", "watchfiles", "websockets"):
            assert package in pins, package


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("pdfminer.six", "pdfminer-six"),
            ("pdfminer_six", "pdfminer-six"),
            ("PDFMiner.Six", "pdfminer-six"),
            ("python-multipart", "python-multipart"),
        ],
    )
    def test_pep503_names_compare_equal(self, raw, expected):
        assert check_lock.normalise(raw) == expected

    def test_the_lock_and_pyproject_agree_after_normalisation(self):
        # pdfplumber pulls pdfminer.six, which pip-compile writes as pdfminer-six.
        # Without normalisation the checker reports a phantom miss.
        assert "pdfminer-six" in check_lock.locked()


class TestSpecifierSatisfaction:
    def test_a_pin_inside_the_floor_passes(self):
        assert check_lock.satisfies("0.141.1", ">=0.115")

    def test_a_pin_below_the_floor_fails(self):
        assert not check_lock.satisfies("0.100.0", ">=0.115")

    def test_no_specifier_accepts_anything(self):
        assert check_lock.satisfies("1.2.3", "")


class TestDriftIsDetected:
    """Each case is a way the two files come apart in practice."""

    def test_a_dependency_added_without_relocking(self, monkeypatch):
        monkeypatch.setattr(check_lock, "declared", lambda: [("httpx", ">=0.27")])
        problems = check_lock.findings()
        assert any("httpx" in p and "absent from the lock" in p for p in problems)

    def test_a_floor_raised_above_the_pin(self, monkeypatch):
        monkeypatch.setattr(check_lock, "declared", lambda: [("fastapi", ">=99.0")])
        problems = check_lock.findings()
        assert any("does not satisfy" in p for p in problems)

    def test_an_entry_that_lost_its_hash(self, monkeypatch):
        monkeypatch.setattr(
            check_lock, "locked", lambda: {"fastapi": ("0.141.1", False)}
        )
        monkeypatch.setattr(check_lock, "declared", lambda: [])
        problems = check_lock.findings()
        assert any("no --hash" in p for p in problems)

    def test_a_missing_lock_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(check_lock, "LOCK", tmp_path / "nope.txt")
        assert any("missing" in p for p in check_lock.findings())
