"""Safeway (Albertsons) adapter stub."""

from unbagged.adapters.registry import register
from unbagged.adapters.safeway.adapter import SafewayAdapter, adapter

register(adapter)

__all__ = ["SafewayAdapter", "adapter"]
