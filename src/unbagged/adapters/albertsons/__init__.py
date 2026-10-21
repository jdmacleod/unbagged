"""Albertsons Companies adapter: Albertsons, Safeway, Vons and their sister banners."""

from unbagged.adapters.albertsons.adapter import AlbertsonsAdapter, adapter
from unbagged.adapters.registry import register

register(adapter)

__all__ = ["AlbertsonsAdapter", "adapter"]
