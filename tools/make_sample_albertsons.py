"""Build the two-file synthetic Albertsons upload without committing a workbook."""

from pathlib import Path

from tools import make_fixtures

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = make_fixtures.ADAPTERS_DIR / "albertsons" / "fixtures"
SCRATCH_NAME = ".sample-albertsons"


def write_sample(directory: Path) -> tuple[Path, Path]:
    """Copy the synthetic report and build its workbook outside user data.

    Resolve both the directory and its files before writing: a scratch directory
    or an existing output file can itself be a symlink into data/.
    """
    report = directory / "synthetic_report.txt"
    workbook = directory / "synthetic_details.xlsx"
    data = (ROOT / "data").resolve()
    if any(path.resolve().is_relative_to(data) for path in (directory, report, workbook)):
        raise ValueError("The synthetic sample must be written outside data/.")
    generator = make_fixtures.load(FIXTURES / "generate.py")
    directory.mkdir(parents=True, exist_ok=True)
    report.write_bytes((FIXTURES / report.name).read_bytes())
    workbook.write_bytes(generator.build_workbook())
    return report, workbook


def main() -> None:
    """Generate the sample and tell the reader which files to upload together."""
    paths = write_sample(ROOT / SCRATCH_NAME)
    print("Upload both files together:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
