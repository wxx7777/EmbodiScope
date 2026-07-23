from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..analysis import validate_dataframe
from .base import AdapterInfo, LoadedDataset


def _load_metadata(path: Path) -> dict[str, Any]:
    candidates = (path.with_suffix(".json"), path.parent / f"{path.stem}.json")
    for candidate in candidates:
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
    return {}


def _dataset_by_suffix(group: Any, suffixes: tuple[str, ...]) -> Any | None:
    matches: list[tuple[str, Any]] = []

    def visit(name: str, value: Any) -> None:
        if hasattr(value, "shape"):
            normalized = name.lower().replace(".", "/")
            if any(normalized == suffix or normalized.endswith(f"/{suffix}") for suffix in suffixes):
                matches.append((normalized, value))

    group.visititems(visit)
    return min(matches, key=lambda item: len(item[0]))[1] if matches else None


def _matrix(dataset: Any | None, rows: int) -> np.ndarray | None:
    if dataset is None:
        return None
    values = np.asarray(dataset)
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    if values.ndim < 2:
        return None
    return values[:rows].reshape(min(rows, len(values)), -1)


class ManiSkillHdf5Adapter:
    info = AdapterInfo(
        adapter_id="maniskill",
        name="ManiSkill Trajectory",
        formats=(".h5", ".hdf5", "ManiSkill trajectory"),
        description="读取 ManiSkill 轨迹中的 actions、qpos、TCP 位姿、目标位置与任务结果。",
        dependency="ManiSkill schema / h5py",
        project_url="https://github.com/haosulab/ManiSkill",
        license="Apache-2.0 (assets may be CC BY-NC 4.0)",
    )

    def can_load(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in {".h5", ".hdf5"}

    def load(self, path: Path) -> LoadedDataset:
        try:
            import h5py
        except ImportError as error:
            raise ValueError("读取 ManiSkill 轨迹需要安装 h5py") from error

        metadata = _load_metadata(path)
        episode_metadata = {
            str(item.get("episode_id", index)): item
            for index, item in enumerate(metadata.get("episodes", []))
            if isinstance(item, dict)
        }
        fps = float(metadata.get("fps") or metadata.get("env_info", {}).get("fps") or 20.0)
        frames: list[pd.DataFrame] = []
        warnings: list[str] = []
        trajectory_names: list[str] = []

        with h5py.File(path, "r") as handle:
            groups = [(name, value) for name, value in handle.items() if isinstance(value, h5py.Group)]
            for group_index, (name, group) in enumerate(groups):
                actions_dataset = _dataset_by_suffix(group, ("actions", "action"))
                qpos_dataset = _dataset_by_suffix(group, ("obs/agent/qpos", "agent/qpos", "qpos"))
                ee_dataset = _dataset_by_suffix(group, ("obs/extra/tcp_pose", "extra/tcp_pose", "tcp_pose", "ee_pose"))
                goal_dataset = _dataset_by_suffix(group, ("obs/extra/goal_pos", "extra/goal_pos", "goal_pos", "target_pos"))
                force_dataset = _dataset_by_suffix(group, ("obs/extra/force", "extra/force", "force", "wrench"))
                object_dataset = _dataset_by_suffix(group, ("obs/extra/obj_pose", "extra/obj_pose", "obj_pose", "object_pose"))
                camera_motion_dataset = _dataset_by_suffix(group, ("obs/extra/camera_motion", "extra/camera_motion", "camera_motion"))
                frame_valid_dataset = _dataset_by_suffix(group, ("obs/extra/frame_valid", "extra/frame_valid", "frame_valid"))
                gripper_dataset = _dataset_by_suffix(group, ("obs/extra/gripper", "extra/gripper", "gripper"))
                gripper_command_dataset = _dataset_by_suffix(group, ("obs/extra/gripper_command", "extra/gripper_command", "gripper_command"))
                is_grasped_dataset = _dataset_by_suffix(group, ("obs/extra/is_grasped", "extra/is_grasped", "is_grasped"))
                phase_dataset = _dataset_by_suffix(group, ("obs/extra/phase", "extra/phase", "phase"))
                timestamp_dataset = _dataset_by_suffix(group, ("timestamps", "timestamp", "time"))

                lengths = [
                    len(dataset)
                    for dataset in (actions_dataset, qpos_dataset, ee_dataset, timestamp_dataset)
                    if dataset is not None and getattr(dataset, "shape", ())
                ]
                if not lengths:
                    warnings.append(f"{name} 不包含可识别的时序数组，已跳过。")
                    continue
                rows = min(lengths)
                if actions_dataset is not None and len(actions_dataset) > 0:
                    rows = min(rows, len(actions_dataset))
                if rows <= 0:
                    continue

                episode_id = name.removeprefix("traj_") or str(group_index)
                episode_info = episode_metadata.get(episode_id, episode_metadata.get(str(group_index), {}))
                payload: dict[str, Any] = {"episode_id": [episode_id] * rows}

                if timestamp_dataset is not None:
                    timestamps = np.asarray(timestamp_dataset).reshape(-1)[:rows].astype(float)
                    timestamps = timestamps - timestamps[0]
                else:
                    timestamps = np.arange(rows, dtype=float) / max(fps, 1e-6)
                payload["timestamp"] = timestamps

                qpos = _matrix(qpos_dataset, rows)
                if qpos is not None:
                    for index in range(qpos.shape[1]):
                        payload[f"joint_{index + 1}"] = qpos[:, index]

                actions = _matrix(actions_dataset, rows)
                if actions is not None:
                    for index in range(actions.shape[1]):
                        payload[f"action_{index + 1}"] = actions[:, index]

                ee_pose = _matrix(ee_dataset, rows)
                if ee_pose is not None and ee_pose.shape[1] >= 3:
                    payload["ee_x"], payload["ee_y"], payload["ee_z"] = ee_pose[:, 0], ee_pose[:, 1], ee_pose[:, 2]

                goal = _matrix(goal_dataset, rows)
                if goal is not None and ee_pose is not None and goal.shape[1] >= 3 and ee_pose.shape[1] >= 3:
                    payload["object_distance"] = np.linalg.norm(goal[:, :3] - ee_pose[:, :3], axis=1)

                force = _matrix(force_dataset, rows)
                if force is not None:
                    payload["force_z"] = force[:, 2] if force.shape[1] >= 3 else force[:, 0]

                object_pose = _matrix(object_dataset, rows)
                if object_pose is not None and object_pose.shape[1] >= 3:
                    payload["object_x"], payload["object_y"], payload["object_z"] = (
                        object_pose[:, 0], object_pose[:, 1], object_pose[:, 2]
                    )
                    if goal is not None and goal.shape[1] >= 3:
                        payload["object_distance"] = np.linalg.norm(goal[:, :3] - object_pose[:, :3], axis=1)

                camera_motion = _matrix(camera_motion_dataset, rows)
                if camera_motion is not None:
                    payload["camera_motion"] = camera_motion[:, 0]

                frame_valid = _matrix(frame_valid_dataset, rows)
                if frame_valid is not None:
                    payload["frame_valid"] = frame_valid[:, 0]

                gripper = _matrix(gripper_dataset, rows)
                if gripper is not None:
                    payload["gripper"] = gripper[:, 0]

                gripper_command = _matrix(gripper_command_dataset, rows)
                if gripper_command is not None:
                    payload["gripper_command"] = gripper_command[:, 0]

                is_grasped = _matrix(is_grasped_dataset, rows)
                if is_grasped is not None:
                    payload["is_grasped"] = is_grasped[:, 0].astype(bool)

                success_value = episode_info.get("success")
                if success_value is None:
                    success_dataset = _dataset_by_suffix(group, ("success", "terminated", "done"))
                    if success_dataset is not None:
                        success_value = bool(np.asarray(success_dataset).reshape(-1)[-1])
                payload["success"] = bool(success_value) if success_value is not None else True
                if phase_dataset is not None:
                    raw_phases = np.asarray(phase_dataset).reshape(-1)[:rows]
                    payload["phase"] = [
                        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
                        for value in raw_phases
                    ]
                else:
                    payload["phase"] = np.linspace(0.0, 1.0, rows)
                frames.append(pd.DataFrame(payload))
                trajectory_names.append(name)

        if not frames:
            raise ValueError("ManiSkill HDF5 中没有找到可分析的 trajectory group")

        frame = validate_dataframe(pd.concat(frames, ignore_index=True, sort=False))
        env_info = metadata.get("env_info", {}) if isinstance(metadata.get("env_info"), dict) else {}
        return LoadedDataset(
            frame=frame,
            source_format="ManiSkill HDF5",
            adapter_id=self.info.adapter_id,
            adapter_name=self.info.name,
            metadata={
                "trajectory_count": len(frames),
                "trajectory_groups": trajectory_names,
                "env_id": env_info.get("env_id") or metadata.get("env_id"),
                "fps": fps,
                "simulation": metadata.get("simulation", {}),
            },
            warnings=warnings,
        )
