"""Fallback adapter for responses no retailer adapter recognises."""

from unbagged.adapters.generic.adapter import GenericAdapter, adapter
from unbagged.adapters.registry import register

register(adapter)

__all__ = ["GenericAdapter", "adapter"]
