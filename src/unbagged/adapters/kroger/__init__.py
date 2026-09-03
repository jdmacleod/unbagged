"""Kroger adapter."""

from unbagged.adapters.kroger.adapter import KrogerAdapter, adapter
from unbagged.adapters.registry import register

register(adapter)

__all__ = ["KrogerAdapter", "adapter"]
