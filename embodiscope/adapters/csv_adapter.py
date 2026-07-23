from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..analysis import validate_dataframe
from .base import AdapterInfo, LoadedDataset


class CsvAdapter:
    info = AdapterInfo(
        adapter_id="csv",
        name="通用 CSV",
        formats=(".csv",),
        description="轻量通用轨迹格式，支持自定义机器人和仿真日志。",
        dependency="pandas",
        project_url="https://pandas.pydata.org/",
        license="BSD-3-Clause",
    )

    def can_load(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".csv"

    def load(self, path: Path) -> LoadedDataset:
        frame = validate_dataframe(pd.read_csv(path))
        return LoadedDataset(
            frame=frame,
            source_format="CSV",
            adapter_id=self.info.adapter_id,
            adapter_name=self.info.name,
            metadata={"path": path.name, "columns": len(frame.columns)},
        )
