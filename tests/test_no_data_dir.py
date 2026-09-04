from tools.no_data_dir import main


def test_staged_data_path_fails():
    assert main(["data/incoming/report.pdf"]) == 1


def test_staged_output_path_fails():
    assert main(["output/export.csv"]) == 1


def test_ordinary_paths_pass():
    assert main(["src/unbagged/cli.py", "tests/test_cli.py", "README.md"]) == 0


def test_no_files_passes():
    assert main([]) == 0


def test_similarly_named_paths_are_not_caught():
    assert main(["docs/data-model.md", "src/unbagged/data.py"]) == 0


def test_a_reset_backup_directory_fails():
    """`make reset` renames ./data to data.bak-<timestamp> and leaves it in the
    checkout, holding every report and the database. .gitignore denies it, so a
    staged path from there arrived via `git add -f` — the exact case this hook
    exists for, and the one it used to wave through."""
    # pii-scan: allow reset timestamp, a date not a card (it is Luhn-valid by luck)
    assert main(["data.bak-20260904-153000/incoming/report.pdf"]) == 1
    # pii-scan: allow reset timestamp, a date not a card
    assert main(["data.bak-20260904-153000/db/unbagged.sqlite"]) == 1


def test_a_directory_merely_starting_with_data_is_not_caught():
    # The prefix is `data.bak-`, not `data`: a `database/` or `datasets/`
    # directory is ordinary source and must stay committable.
    assert main(["database/schema.sql", "datasets/README.md", "data_model.py"]) == 0
