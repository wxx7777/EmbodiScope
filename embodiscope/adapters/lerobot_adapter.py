from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..analysis import validate_dataframe
from .base import AdapterInfo, LoadedDataset


def _as_vector(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        return [float(item) for item in value.reshape(-1)]
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    if hasattr(value, "as_py"):
        return _as_vector(value.as_py())
    return []


def _expand_vector_column(frame: pd.DataFrame, source: str, prefix: str) -> list[str]:
    vectors = frame[source].map(_as_vector)
    width = int(vectors.map(len).max()) if len(vectors) else 0
    created: list[str] = []
    for index in range(width):
        column = f"{prefix}_{index + 1}"
        frame[column] = vectors.map(lambda values, idx=index: values[idx] if idx < len(values) else np.nan)
        created.append(column)
    return created


def _read_info(path: Path) -> dict[str, Any]:
    candidates = []
    if path.is_dir():
        candidates.extend([path / "meta" / "info.json", path / "info.json"])
    else:
        candidates.extend([path.parent / "meta" / "info.json", path.parent / "info.json"])
    for candidate in candidates:
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
    return {}


def _dataset_root(path: Path) -> Path:
    if path.is_dir():
        return path
    for parent in (path.parent, *path.parents):
        if (parent / "meta" / "info.json").is_file():
            return parent
    return path.parent


def _read_source_manifest(path: Path) -> dict[str, Any]:
    candidate = _dataset_root(path) / "SOURCE.json"
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_tasks(path: Path) -> dict[int, str]:
    candidate = _dataset_root(path) / "meta" / "tasks.parquet"
    if not candidate.is_file():
        return {}
    try:
        table = pd.read_parquet(candidate)
        if "task_index" not in table.columns:
            return {}
        return {int(row["task_index"]): str(index) for index, row in table.iterrows()}
    except (OSError, ValueError, TypeError):
        return {}


def _video_segments(path: Path, info: dict[str, Any], visual_features: list[str]) -> dict[str, dict[str, Any]]:
    root = _dataset_root(path)
    files = sorted(root.rglob("*.mp4"))
    if not files:
        return {}
    metadata_files = sorted((root / "meta" / "episodes").rglob("*.parquet"))
    if not metadata_files or not visual_features:
        return {}
    try:
        episodes = pd.concat((pd.read_parquet(file) for file in metadata_files), ignore_index=True)
    except (OSError, ValueError):
        return {}

    feature = visual_features[0]
    pattern = str(info.get("video_path") or "")
    prefix = f"videos/{feature}"
    from_column = f"{prefix}/from_timestamp"
    to_column = f"{prefix}/to_timestamp"
    if "episode_index" not in episodes.columns or from_column not in episodes.columns or to_column not in episodes.columns:
        return {}

    output: dict[str, dict[str, Any]] = {}
    for _, row in episodes.iterrows():
        episode_index = int(row["episode_index"])
        chunk_index = int(row.get(f"{prefix}/chunk_index", 0))
        file_index = int(row.get(f"{prefix}/file_index", episode_index))
        relative: str | None = None
        if pattern:
            try:
                relative = pattern.format(
                    video_key=feature,
                    chunk_index=chunk_index,
                    file_index=file_index,
                    episode_index=episode_index,
                    episode_chunk=chunk_index,
                )
            except (KeyError, ValueError):
                relative = None
        candidate = root / relative if relative else files[min(file_index, len(files) - 1)]
        if not candidate.is_file():
            candidate = files[min(file_index, len(files) - 1)]
        output[str(episode_index)] = {
            "feature": feature,
            "relative_path": candidate.relative_to(root).as_posix(),
            "start": float(row[from_column]),
            "end": float(row[to_column]),
        }
    return output


def _data_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files = sorted(path.rglob("*.parquet"))
    episode_files = [
        file for file in files
        if "data" in {part.lower() for part in file.relative_to(path).parts[:-1]}
        or file.name.lower().startswith("episode_")
    ]
    return episode_files or files


class LeRobotParquetAdapter:
    info = AdapterInfo(
        adapter_id="lerobot",
        name="LeRobot Dataset v3",
        formats=(".parquet", ".zip", "LeRobot dataset directory"),
        description="兼容 LeRobotDataset 的 Parquet、MP4、meta/info.json 与 episode 索引结构。",
        dependency="Apache Arrow / PyArrow",
        project_url="https://github.com/huggingface/lerobot",
        license="Apache-2.0",
    )

    def can_load(self, path: Path) -> bool:
        if path.is_file():
            return path.suffix.lower() in {".parquet", ".pq"}
        return path.is_dir() and any(path.rglob("*.parquet"))

    def load(self, path: Path) -> LoadedDataset:
        try:
            import pyarrow.dataset as arrow_dataset
        except ImportError as error:
            raise ValueError("读取 LeRobot 数据需要安装 pyarrow") from error

        files = _data_files(path)
        if not files:
            raise ValueError("LeRobot 目录中没有找到 Parquet 数据文件")

        dataset = arrow_dataset.dataset([str(file) for file in files], format="parquet")
        original_columns = list(dataset.schema.names)
        selected = [
            column for column in original_columns
            if not any(token in column.lower() for token in ("image", "video", "jpeg", "bytes"))
        ]
        frame = dataset.to_table(columns=selected).to_pandas()
        info = _read_info(path)
        source_manifest = _read_source_manifest(path)
        warnings: list[str] = []
        expanded: dict[str, list[str]] = {}

        episode_source = next((name for name in ("episode_id", "episode_index", "episode") if name in frame.columns), None)
        if episode_source is None:
            frame["episode_id"] = "EP-000"
            warnings.append("未找到 episode_index，全部数据按单一 Episode 处理。")
        else:
            frame["episode_id"] = frame[episode_source].astype(str)

        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
        elif "frame_index" in frame.columns:
            fps = float(info.get("fps", 30.0))
            frame["timestamp"] = pd.to_numeric(frame["frame_index"], errors="coerce") / max(fps, 1e-6)
            warnings.append(f"未找到 timestamp，已按 frame_index / {fps:g} FPS 推导。")
        else:
            fps = float(info.get("fps", 30.0))
            frame["timestamp"] = frame.groupby("episode_id").cumcount() / max(fps, 1e-6)
            warnings.append(f"未找到 timestamp 与 frame_index，已按行号和 {fps:g} FPS 推导。")

        state_source = next((name for name in ("observation.state", "observation_state", "state") if name in frame.columns), None)
        ee_source = next((name for name in ("observation.ee_pose", "observation.end_effector_pose", "ee_pose") if name in frame.columns), None)
        state_width = int(frame[state_source].map(lambda value: len(_as_vector(value))).max()) if state_source else 0
        robot_type = str(info.get("robot_type") or "").lower()
        state_prefix = "state" if state_width <= 3 and robot_type in {"", "unknown"} and ee_source is None else "joint"
        vector_mappings = (
            (("observation.state", "observation_state", "state"), state_prefix),
            (("action", "actions"), "action"),
            (("observation.ee_pose", "observation.end_effector_pose", "ee_pose"), "ee"),
        )
        for candidates, prefix in vector_mappings:
            source = next((name for name in candidates if name in frame.columns), None)
            if source:
                columns = _expand_vector_column(frame, source, prefix)
                expanded[source] = columns
                if prefix == "ee" and len(columns) >= 3:
                    frame = frame.rename(columns={columns[0]: "ee_x", columns[1]: "ee_y", columns[2]: "ee_z"})

        scalar_aliases = {
            "observation.gripper": "gripper",
            "observation.gripper_position": "gripper",
            "observation.force_z": "force_z",
            "observation.camera_motion": "camera_motion",
            "observation.object_distance": "object_distance",
            "task_phase": "phase",
            "next.reward": "reward",
            "next.done": "done",
        }
        for source, target in scalar_aliases.items():
            if source in frame.columns and target not in frame.columns:
                frame[target] = frame[source]

        if "success" in frame.columns:
            frame["success_known"] = True
        elif "next.success" in frame.columns and source_manifest.get("success_semantics") != "not_labeled":
            frame["success"] = frame["next.success"].astype(bool)
            frame["success_known"] = True
        else:
            frame["success_known"] = False
        tasks = _read_tasks(path)
        if "task_index" in frame.columns and tasks:
            frame["task"] = pd.to_numeric(frame["task_index"], errors="coerce").map(tasks)

        frame = validate_dataframe(frame)
        video_files = sorted(path.rglob("*.mp4")) if path.is_dir() else []
        features = info.get("features", {}) if isinstance(info.get("features"), dict) else {}
        visual_features = [
            name for name, value in features.items()
            if isinstance(value, dict) and value.get("dtype") in {"video", "image"}
        ]
        segments = _video_segments(path, info, visual_features)
        return LoadedDataset(
            frame=frame,
            source_format="LeRobot Dataset v3" if path.is_dir() else "LeRobot Parquet",
            adapter_id=self.info.adapter_id,
            adapter_name=self.info.name,
            metadata={
                "files": len(files),
                "fps": info.get("fps"),
                "original_columns": original_columns,
                "loaded_columns": selected,
                "expanded_features": expanded,
                "video_files": len(video_files),
                "visual_features": visual_features,
                "video_segments": segments,
                "dataset_version": info.get("codebase_version") or info.get("version"),
                "robot_type": info.get("robot_type"),
                "tasks": sorted(set(tasks.values())),
                "provenance": source_manifest,
            },
            warnings=warnings,
        )
