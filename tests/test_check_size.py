"""The size check: new files held to the limit, baselined files only shrinking."""

import json

from tools import check_size


def write(root, rel, lines):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * lines)


def baseline(root, entries):
    (root / "tools").mkdir(exist_ok=True)
    (root / check_size.BASELINE_NAME).write_text(json.dumps(entries))


def run(root, *args):
    return check_size.main(["--root", str(root), *args])


class TestLimits:
    def test_a_new_file_past_the_limit_fails(self, tmp_path):
        write(tmp_path, "src/unbagged/big.py", 801)
        assert run(tmp_path) == 1

    def test_a_test_file_gets_more_room(self, tmp_path):
        write(tmp_path, "tests/test_big.py", 1000)
        assert run(tmp_path) == 0

    def test_a_baselined_file_may_not_grow(self, tmp_path):
        write(tmp_path, "src/unbagged/views.py", 1001)
        baseline(tmp_path, {"src/unbagged/views.py": 1000})
        assert run(tmp_path) == 1

    def test_but_it_may_shrink(self, tmp_path):
        write(tmp_path, "src/unbagged/views.py", 999)
        baseline(tmp_path, {"src/unbagged/views.py": 1000})
        assert run(tmp_path) == 0

    def test_update_only_ever_lowers_the_baseline(self, tmp_path):
        write(tmp_path, "src/unbagged/views.py", 900)
        write(tmp_path, "src/unbagged/small.py", 10)
        baseline(tmp_path, {"src/unbagged/views.py": 1000, "src/unbagged/small.py": 900})
        assert run(tmp_path, "--update") == 0
        saved = json.loads((tmp_path / check_size.BASELINE_NAME).read_text())
        assert saved == {"src/unbagged/views.py": 900}


class TestScope:
    def test_data_is_never_in_scope(self, tmp_path):
        # Nothing under data/ is read, however large: it holds real reports.
        write(tmp_path, "data/incoming/huge.py", 5000)
        assert not check_size.in_scope("data/incoming/huge.py")
        assert run(tmp_path) == 0

    def test_fixture_generators_are_not_in_scope(self):
        assert not check_size.in_scope("src/unbagged/adapters/kroger/fixtures/generate.py")
