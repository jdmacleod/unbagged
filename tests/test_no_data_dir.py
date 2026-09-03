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
