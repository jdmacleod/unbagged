"""Retailer adapters.

Every retailer gets one adapter behind the RetailerAdapter protocol; nothing
downstream knows which retailer it is looking at. See docs/writing-an-adapter.md.

Importing this package registers every built-in adapter, so the registry is
populated by `import unbagged.adapters` and by nothing more magical than that.
"""

from unbagged.adapters import kroger  # noqa: F401  (registers the adapter)
from unbagged.adapters.registry import registry

__all__ = ["registry"]
