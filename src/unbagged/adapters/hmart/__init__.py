"""H Mart adapter stub."""

from unbagged.adapters.hmart.adapter import HMartAdapter, adapter
from unbagged.adapters.registry import register

register(adapter)

__all__ = ["HMartAdapter", "adapter"]
