from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .base import DatasetAdapter, LoadedDataset
from .csv_adapter import CsvAdapter
from .lerobot_adapter import LeRobotParquetAdapter
from .maniskill_adapter import ManiSkillHdf5Adapter
from .rosbag_adapter import RosbagAdapter


def _adapters() -> tuple[DatasetAdapter, ...]:
    return (CsvAdapter(), LeRobotParquetAdapter(), RosbagAdapter(), ManiSkillHdf5Adapter())


def adapter_catalog() -> list[dict]:
    return [adapter.info.to_dict() for adapter in _adapters()]


def load_dataset(path: Path | str, adapters: Iterable[DatasetAdapter] | None = None) -> LoadedDataset:
    source = Path(path).resolve()
    if not source.exists():
        raise ValueError(f"数据源不存在: {source}")
    candidates = tuple(adapters) if adapters is not None else _adapters()
    for adapter in candidates:
        if adapter.can_load(source):
            return adapter.load(source)
    formats = sorted({item for adapter in candidates for item in adapter.info.formats})
    raise ValueError(f"无法识别数据格式。当前支持: {', '.join(formats)}")
