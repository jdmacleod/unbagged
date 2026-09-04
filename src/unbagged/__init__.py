"""unbagged — read what the grocery store knows about you."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Set at build time from the VERSION file; see pyproject's hatch config.
    __version__ = version("unbagged")
except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
    # Importable without being installed, which is how a stray `python -c`
    # against src/ behaves. Say so rather than inventing a number, because a
    # plausible-looking version in a bug report is worse than an obvious gap.
    __version__ = "0.0.0+unknown"
