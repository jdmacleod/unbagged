"""Retailer adapters.

Every retailer gets one adapter behind the RetailerAdapter protocol; nothing
downstream knows which retailer it is looking at. See docs/writing-an-adapter.md.

Importing this package registers every built-in adapter, so the registry is
populated by `import unbagged.adapters` and by nothing more magical than that.
Adding a retailer means adding a package here and one line below.
"""

from unbagged.adapters import (  # noqa: F401  (each import registers its adapter)
    generic,
    hmart,
    kroger,
    safeway,
)
from unbagged.adapters.registry import registry

__all__ = ["registry"]
