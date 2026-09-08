"""A signature nobody can check is decoration.

`.github/allowed_signers` is what lets someone who cloned this repository verify
a release without asking GitHub to vouch for it. These assert the file still
does that, and — more usefully — that the check itself cannot pass while
verifying nothing.

That last one is the point. A checkout without tags verifies an empty set and
reports success unless something refuses, and `actions/checkout` fetches no tags
unless asked. Four guards in this repository have already shipped with that
shape; see issue #32.
"""

import subprocess

import pytest
from tools import check_signers as signers


class TestParsingTheSignersFile:
    def test_a_plain_entry(self):
        assert signers.entries("someone@example.com ssh-ed25519 AAAAC3") == [
            ("someone@example.com", "ssh-ed25519")
        ]

    def test_an_entry_with_options(self):
        """The options field sits between the principal and the key type, so a
        naive fields[1] reads `namespaces="git"` as the key type."""
        line = 'someone@example.com namespaces="git" ssh-ed25519 AAAAC3'
        assert signers.entries(line) == [("someone@example.com", "ssh-ed25519")]

    def test_comments_and_blank_lines_are_not_signers(self):
        text = "# a comment\n\n   \nsomeone@example.com ssh-ed25519 AAAAC3\n"
        assert len(signers.entries(text)) == 1

    def test_several_signers(self):
        text = (
            "one@example.com ssh-ed25519 AAAA\n"
            'two@example.com namespaces="git" ssh-rsa BBBB\n'
        )
        assert [p for p, _ in signers.entries(text)] == [
            "one@example.com", "two@example.com"
        ]

    def test_a_line_with_no_key_type_is_refused(self):
        with pytest.raises(ValueError, match="no key type"):
            signers.entries("someone@example.com not-a-key AAAA")

    def test_a_one_field_line_is_refused(self):
        with pytest.raises(ValueError, match="not a signer entry"):
            signers.entries("someone@example.com")


class TestItRefusesToVerifyNothing:
    def test_no_tags_is_a_failure_not_a_pass(self, monkeypatch, capsys):
        """The load-bearing test.

        Without this, a tag-less checkout verifies an empty set and reports
        success, and the check becomes decoration nobody notices is inert.
        """
        monkeypatch.setattr(signers, "tags", lambda: [])
        assert signers.run() == 1
        err = capsys.readouterr().err
        assert "verified nothing" in err
        assert "git fetch --tags" in err

    def test_an_empty_signers_file_is_a_failure(self, monkeypatch, tmp_path, capsys):
        empty = tmp_path / "allowed_signers"
        empty.write_text("# only comments\n")
        monkeypatch.setattr(signers, "SIGNERS", empty)
        assert signers.run() == 1
        assert "lists nobody" in capsys.readouterr().err

    def test_a_missing_signers_file_is_a_failure(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(signers, "SIGNERS", tmp_path / "nope")
        assert signers.run() == 1
        assert "no signers file" in capsys.readouterr().err

    def test_a_malformed_signers_file_is_a_failure(self, monkeypatch, tmp_path, capsys):
        """Verification fails open into 'cannot check', which reads like success
        to anyone not looking closely."""
        bad = tmp_path / "allowed_signers"
        bad.write_text("someone@example.com garbage\n")
        monkeypatch.setattr(signers, "SIGNERS", bad)
        assert signers.run() == 1
        assert "no key type" in capsys.readouterr().err

    def test_a_tag_that_does_not_verify_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(signers, "tags", lambda: ["v9.9.9"])
        monkeypatch.setattr(signers, "verify", lambda tag: (False, "no signature"))
        assert signers.run() == 1
        err = capsys.readouterr().err
        assert "FAILED  v9.9.9" in err
        # The rotation advice is the actionable part, and the reason it is
        # actionable is that a signed tag can never be re-signed here.
        assert "ADD the new line" in err


class TestTheCommittedFile:
    def test_it_names_at_least_one_signer(self):
        assert signers.entries(signers.SIGNERS.read_text())

    def test_it_restricts_keys_to_git_signatures(self):
        """`namespaces="git"` stops the key verifying a signature made for some
        other purpose with the same key."""
        text = signers.SIGNERS.read_text()
        for line in text.splitlines():
            if line.strip() and not line.strip().startswith("#"):
                assert 'namespaces="git"' in line, line

    def test_every_tag_in_this_repository_verifies(self):
        """The real thing, against the real tags.

        Skipped only where there are genuinely no tags to check — a shallow
        clone. CI fetches them, and `check_signers.py` itself refuses that case,
        so the gate does not inherit this skip.
        """
        found = signers.tags()
        if not found:
            pytest.skip("no tags in this checkout; tools/check_signers.py gates CI")
        for tag in found:
            ok, detail = signers.verify(tag)
            assert ok, f"{tag} does not verify: {detail}"

    def test_verification_does_not_depend_on_local_git_config(self):
        """`verify` passes the signers file with -c rather than relying on the
        caller having set gpg.ssh.allowedSignersFile. A check that only works on
        the machine that wrote it is not a check."""
        found = signers.tags()
        if not found:
            pytest.skip("no tags in this checkout")
        result = subprocess.run(
            ["git", "-c", "gpg.ssh.allowedSignersFile=", "verify-tag", found[0]],
            cwd=signers.REPO_ROOT, capture_output=True, text=True,
        )
        assert result.returncode != 0, (
            "verification passed with no signers file configured, so the -c in "
            "verify() is not what is doing the work"
        )
        ok, _ = signers.verify(found[0])
        assert ok
