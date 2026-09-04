"""unbagged — read what the grocery store knows about you."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# The repo-root VERSION file, when we are running from a checkout.
_VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"


def _read_version() -> str:
    """What this build actually is.

    The file wins over the metadata, and that ordering is the whole point.
    `importlib.metadata` reads the dist-info written at *install* time, so in an
    editable checkout it goes stale the moment `VERSION` changes: bumping to
    0.10.0 left a running dev server reporting 0.9.0 in its footer, and the
    footer exists precisely so a person filing a bug can say what they are
    running. A confidently wrong version sends the maintainer to the wrong
    commit.

    Installed non-editable — which is how the Docker image runs — the file is
    not beside the package and the metadata is correct by construction, because
    the image was built from that same file. So the fallback is not a
    degradation, it is the normal path in production.
    """
    try:
        declared = _VERSION_FILE.read_text(encoding="utf-8").strip()
        if declared:
            return declared
    except OSError:
        pass
    try:
        return version("unbagged")
    except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        # Importable without being installed, which is how a stray `python -c`
        # against src/ behaves. Say so rather than inventing a number, because a
        # plausible-looking version in a bug report is worse than an obvious gap.
        return "0.0.0+unknown"


__version__ = _read_version()
