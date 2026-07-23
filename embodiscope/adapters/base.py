from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class AdapterInfo:
    adapter_id: str
    name: str
    formats: tuple[str, ...]
    description: str
    dependency: str
    project_url: str
    license: str
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["formats"] = list(self.formats)
        return payload


@dataclass
class LoadedDataset:
    frame: pd.DataFrame
    source_format: str
    adapter_id: str
    adapter_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def source_payload(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "adapter_id": self.adapter_id,
            "adapter_name": self.adapter_name,
            "source_metadata": self.metadata,
            "source_warnings": self.warnings,
        }


class DatasetAdapter(Protocol):
    info: AdapterInfo

    def can_load(self, path: Path) -> bool: ...

    def load(self, path: Path) -> LoadedDataset: ...
