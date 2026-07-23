from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DatasetLibrary:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.manifest_path = self.project_root / "data" / "dataset_catalog.json"
        self._entries = self._read_manifest()

    def _read_manifest(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"数据集目录不可用: {error}") from error
        entries = payload.get("datasets", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            raise ValueError("数据集目录格式错误")
        output: list[dict[str, Any]] = []
        ids: set[str] = set()
        for raw in entries:
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("path"):
                raise ValueError("数据集目录包含无效条目")
            dataset_id = str(raw["id"])
            if dataset_id in ids:
                raise ValueError(f"数据集编号重复: {dataset_id}")
            ids.add(dataset_id)
            path = (self.project_root / str(raw["path"])).resolve()
            if path != self.project_root and not path.is_relative_to(self.project_root):
                raise ValueError(f"数据集路径越界: {dataset_id}")
            output.append({**raw, "id": dataset_id, "resolved_path": path})
        return output

    def resolve(self, dataset_id: str) -> Path:
        entry = self.entry(dataset_id)
        path = entry["resolved_path"]
        if not path.exists():
            raise ValueError(f"数据集尚未安装: {entry['name']}")
        return path

    def entry(self, dataset_id: str) -> dict[str, Any]:
        entry = next((item for item in self._entries if item["id"] == dataset_id), None)
        if entry is None:
            raise ValueError("找不到数据集")
        return entry

    def identify(self, path: Path) -> str | None:
        resolved = path.resolve()
        return next((item["id"] for item in self._entries if item["resolved_path"] == resolved), None)

    def catalog(self, current_path: Path) -> dict[str, Any]:
        current = current_path.resolve()
        entries = []
        for entry in self._entries:
            path = entry["resolved_path"]
            public = {key: value for key, value in entry.items() if key != "resolved_path"}
            public["available"] = path.exists()
            public["active"] = path == current
            public["size_bytes"] = self._size(path) if path.exists() else 0
            entries.append(public)
        return {
            "datasets": entries,
            "available_count": sum(item["available"] for item in entries),
            "episode_count": sum(int(item.get("episode_count", 0)) for item in entries if item["available"]),
            "row_count": sum(int(item.get("row_count", 0)) for item in entries if item["available"]),
        }

    @staticmethod
    def _size(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
