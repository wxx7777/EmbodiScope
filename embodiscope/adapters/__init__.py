"""Dataset adapters for embodied-intelligence open-source formats."""

from .base import AdapterInfo, LoadedDataset
from .registry import adapter_catalog, load_dataset

__all__ = ["AdapterInfo", "LoadedDataset", "adapter_catalog", "load_dataset"]
