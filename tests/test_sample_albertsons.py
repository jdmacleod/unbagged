"""The README sample must include the purchase workbook, outside user data."""

from pathlib import Path

import pytest
from tools import make_sample_albertsons as sample

from unbagged import db, ingest, repository


def test_sample_directory_is_excluded_from_git_and_docker() -> None:
    """Read the directory name from the tool: a renamed scratch path must stay ignored."""
    git_patterns = (sample.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    docker_patterns = (sample.ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert f"/{sample.SCRATCH_NAME}/" in git_patterns
    assert f"**/{sample.SCRATCH_NAME}/" in docker_patterns


def test_generated_pair_contains_purchases(tmp_path: Path) -> None:
    """A report alone looks valid but loses purchases: parse both actual outputs."""
    report, workbook = sample.write_sample(tmp_path / "sample")
    assert report.read_bytes() == (sample.FIXTURES / report.name).read_bytes()
    files = [
        stored
        for path in (report, workbook)
        for stored in ingest.store_upload_many(
            path.name, path.read_bytes(), directory=tmp_path / "incoming"
        )
    ]
    with db.open_db(tmp_path / "sample.sqlite") as conn:
        result = ingest.ingest(conn, files)
        assert result.match.adapter.retailer_id == "albertsons"
        assert result.result.item_count() > 0
        assert repository.get_transactions(conn, result.request_id)
        warnings = repository.get_warnings(conn, result.request_id)
        assert not any("No purchase history" in warning.message for warning in warnings)


@pytest.mark.parametrize("relative", ["data", "data/nested"])
def test_refuses_user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str) -> None:
    monkeypatch.setattr(sample, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="data/"):
        sample.write_sample(tmp_path / relative)
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("link_file", [False, True])
def test_refuses_symlink_into_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_file: bool
) -> None:
    """A scratch path's spelling is safe even when its destination is not."""
    monkeypatch.setattr(sample, "ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    output = tmp_path / "sample"
    if link_file:
        output.mkdir()
        (output / "synthetic_report.txt").symlink_to(data / "report.txt")
    else:
        output.symlink_to(data, target_is_directory=True)
    with pytest.raises(ValueError, match="data/"):
        sample.write_sample(output)
    assert not list(data.iterdir())
