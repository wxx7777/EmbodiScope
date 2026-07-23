from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def rerun_status() -> dict[str, Any]:
    try:
        import rerun as rr
    except ImportError:
        return {"available": False, "version": None, "dependency": "rerun-sdk"}
    return {"available": True, "version": getattr(rr, "__version__", "unknown"), "dependency": "rerun-sdk"}


def export_episode_recording(
    frame: pd.DataFrame,
    episode_id: str,
    destination: Path,
    dataset_name: str,
    analysis: dict[str, Any],
) -> Path:
    try:
        import rerun as rr
    except ImportError as error:
        raise ValueError("导出 Rerun 记录需要安装 rerun-sdk") from error

    data = frame[frame["episode_id"].astype(str) == str(episode_id)].sort_values("timestamp").reset_index(drop=True)
    if data.empty:
        raise ValueError(f"未找到 Episode: {episode_id}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    recording_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"{dataset_name}_{episode_id}")
    recording = rr.new_recording("embodiscope", recording_id=recording_id)
    recording.save(destination)

    has_xyz = {"ee_x", "ee_y", "ee_z"}.issubset(data.columns)
    if has_xyz:
        trajectory = data[["ee_x", "ee_y", "ee_z"]].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(trajectory).all(axis=1)
        if valid.any():
            recording.log(
                "world/end_effector/trajectory",
                rr.LineStrips3D([trajectory[valid].tolist()], colors=[20, 125, 115], radii=[0.004]),
                static=True,
            )

    timestamps = pd.to_numeric(data["timestamp"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    scalar_columns = [column for column in ("force_z", "gripper", "object_distance", "camera_motion") if column in data.columns]
    joint_columns = sorted((column for column in data.columns if column.startswith("joint_")), key=lambda item: int(item.split("_")[-1]))

    for index, row in data.iterrows():
        recording.set_time_seconds("episode_time", float(timestamps[index]))
        if has_xyz:
            point = [row.get("ee_x"), row.get("ee_y"), row.get("ee_z")]
            if all(pd.notna(value) for value in point):
                recording.log(
                    "world/end_effector/current",
                    rr.Points3D([point], colors=[197, 62, 58], radii=[0.016], labels=["TCP"]),
                )
        for column in scalar_columns + joint_columns:
            value = row.get(column)
            if pd.notna(value):
                recording.log(f"signals/{column}", rr.Scalar(float(value)))

    for event in analysis.get("events", []):
        recording.set_time_seconds("episode_time", float(event.get("time", 0.0)))
        severity = str(event.get("severity", "warning"))
        level = "ERROR" if severity == "critical" else "WARN"
        recording.log(
            "diagnostics/events",
            rr.TextLog(str(event.get("label", "异常事件")), level=level),
        )

    recording.set_time_seconds("episode_time", float(timestamps[-1]))
    recording.log(
        "diagnostics/summary",
        rr.TextLog(
            f"Quality {analysis.get('quality_score', 0):.1f}/100 · {len(analysis.get('issues', []))} issues",
            level="INFO",
        ),
    )
    recording.flush()
    return destination
