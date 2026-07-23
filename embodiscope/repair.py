from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .analysis import _robust_threshold, analyze_episode, validate_dataframe
from .profiles import AnalysisProfile, resolve_profile


INTERPOLATABLE_SIGNALS = {
    "ee_x", "ee_y", "ee_z", "camera_motion", "force_x", "force_y", "force_z",
    "gripper", "object_distance",
}
def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else round(float(value), 8)
    if pd.isna(value):
        return None
    return str(value)


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(mask):
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(mask) - 1):
            end = index if active and index == len(mask) - 1 else index - 1
            runs.append((start, end))
            start = None
    return runs


def source_sha256(path: Path) -> str:
    """Hash a source file or an entire dataset directory deterministically."""
    source = path.resolve()
    if not source.exists():
        raise ValueError(f"来源数据不存在: {source.name}")
    digest = hashlib.sha256()
    if source.is_file():
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    files = sorted(item for item in source.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(source).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(item.stat().st_size.to_bytes(8, "big"))
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RepairArtifact:
    cleaned: pd.DataFrame
    payload: dict[str, Any]
    csv_bytes: bytes

    def manifest_bytes(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False, indent=2).encode("utf-8")


def _signal_columns(data: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in data.columns:
        if column in INTERPOLATABLE_SIGNALS or column.startswith(("joint_", "state_", "action_")):
            if pd.to_numeric(data[column], errors="coerce").notna().any():
                columns.append(column)
    return columns


def build_repair_artifact(
    frame: pd.DataFrame,
    episode_id: str,
    source_path: Path,
    dataset_name: str,
    source: dict[str, Any] | None = None,
    profile: str | AnalysisProfile | None = None,
    max_short_gap: int = 3,
    source_digest: str | None = None,
) -> RepairArtifact:
    active_profile = resolve_profile(profile)
    validated = validate_dataframe(frame)
    selected = validated[validated["episode_id"] == str(episode_id)].copy()
    if selected.empty:
        raise ValueError(f"找不到 episode: {episode_id}")

    source_rows = selected.index.to_numpy(dtype=int)
    original = selected.reset_index(drop=True)
    cleaned = original.copy()
    timestamps = pd.to_numeric(cleaned["timestamp"], errors="coerce").to_numpy(dtype=float)
    before = analyze_episode(original, str(episode_id), active_profile)
    original_columns = list(original.columns)
    row_actions: list[set[str]] = [set() for _ in range(len(cleaned))]
    row_reasons: list[set[str]] = [set() for _ in range(len(cleaned))]
    quality_valid = np.ones(len(cleaned), dtype=bool)
    action_log: dict[str, dict[str, Any]] = {}
    changed_columns: set[str] = set()

    def record(
        code: str,
        kind: str,
        title: str,
        description: str,
        rows: np.ndarray | list[int],
        columns: list[str] | None = None,
        cells: int = 0,
        invalidate: bool = False,
    ) -> None:
        indices = sorted({int(index) for index in rows if 0 <= int(index) < len(cleaned)})
        if not indices:
            return
        entry = action_log.setdefault(code, {
            "code": code,
            "kind": kind,
            "title": title,
            "description": description,
            "rows": set(),
            "columns": set(),
            "cells": 0,
            "reversible": True,
        })
        entry["rows"].update(indices)
        entry["columns"].update(columns or [])
        entry["cells"] += int(cells)
        for index in indices:
            row_actions[index].add(code)
            row_reasons[index].add(code)
        if invalidate:
            quality_valid[indices] = False

    def preserve(column: str) -> None:
        backup = f"{column}__original"
        if backup not in cleaned.columns:
            cleaned[backup] = original[column]
        changed_columns.add(column)

    interpolated_cells = 0
    interpolated_rows: set[int] = set()
    for column in _signal_columns(cleaned):
        values = pd.to_numeric(cleaned[column], errors="coerce").to_numpy(dtype=float)
        for start, end in _true_runs(~np.isfinite(values)):
            bounded = start > 0 and end + 1 < len(values)
            short = end - start + 1 <= max_short_gap
            anchors_valid = bounded and np.isfinite(values[start - 1]) and np.isfinite(values[end + 1])
            time_valid = anchors_valid and timestamps[end + 1] > timestamps[start - 1]
            indices = np.arange(start, end + 1, dtype=int)
            if short and time_valid:
                preserve(column)
                values[indices] = np.interp(
                    timestamps[indices],
                    [timestamps[start - 1], timestamps[end + 1]],
                    [values[start - 1], values[end + 1]],
                )
                cleaned.loc[indices, column] = values[indices]
                interpolated_cells += len(indices)
                interpolated_rows.update(indices.tolist())
            else:
                record(
                    "MISSING_VALUES_UNRESOLVED", "quarantine", "隔离连续缺失片段",
                    f"缺口超过 {max_short_gap} 个采样点或缺少双侧有效锚点，不生成推测值。",
                    indices, [column], invalidate=True,
                )
    if interpolated_rows:
        record(
            "MISSING_VALUES", "correction", "短缺口时间插值",
            f"仅对不超过 {max_short_gap} 个采样点且具有双侧锚点的数值信号执行线性插值。",
            sorted(interpolated_rows), sorted(changed_columns), interpolated_cells,
        )

    joint_columns = [column for column in original_columns if column.startswith("joint_")]
    jump_rows: set[int] = set()
    jump_cells = 0
    if joint_columns and len(cleaned) >= 3:
        safe_dt = np.diff(timestamps, prepend=timestamps[0])
        positive_dt = safe_dt[safe_dt > 0]
        median_dt = float(np.median(positive_dt)) if positive_dt.size else 0.02
        safe_dt[safe_dt <= 0] = median_dt
        for column in joint_columns:
            values = pd.to_numeric(cleaned[column], errors="coerce").interpolate(limit_direction="both").to_numpy(dtype=float)
            velocity = np.diff(values, prepend=values[0]) / safe_dt
            threshold = _robust_threshold(
                np.abs(velocity),
                scale=active_profile.joint_velocity_mad_scale,
                floor=active_profile.joint_velocity_floor,
            )
            candidates: list[int] = []
            for index in range(1, len(values) - 1):
                incoming = velocity[index]
                outgoing = (values[index + 1] - values[index]) / safe_dt[index + 1]
                if abs(incoming) > threshold and abs(outgoing) > threshold and incoming * outgoing < 0:
                    candidates.append(index)
            if not candidates:
                continue
            preserve(column)
            for index in candidates:
                denominator = timestamps[index + 1] - timestamps[index - 1]
                ratio = (timestamps[index] - timestamps[index - 1]) / denominator if denominator > 0 else 0.5
                cleaned.at[index, column] = values[index - 1] + ratio * (values[index + 1] - values[index - 1])
                jump_rows.add(index)
                jump_cells += 1
    if jump_rows:
        record(
            "JOINT_JUMP", "correction", "孤立关节突跳插值",
            "仅修复前后速度同时越界且方向相反的单点突跳，持续异常不会被平滑掩盖。",
            sorted(jump_rows), joint_columns, jump_cells,
        )

    sync_rows: np.ndarray = np.array([], dtype=int)
    sync_edge_rows: np.ndarray = np.array([], dtype=int)
    sync_offset = float(before["metrics"]["sync_offset_ms"]) / 1000.0
    sync_confidence = float(before["metrics"]["sync_confidence"])
    if (
        "camera_motion" in cleaned.columns
        and abs(sync_offset) >= active_profile.sync_warning_seconds
        and sync_confidence >= active_profile.sync_min_confidence
    ):
        values = pd.to_numeric(cleaned["camera_motion"], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(values)
        if finite.sum() >= 2:
            target_times = timestamps + sync_offset
            valid_target = (target_times >= timestamps[finite].min()) & (target_times <= timestamps[finite].max())
            sync_rows = np.flatnonzero(valid_target)
            sync_edge_rows = np.flatnonzero(~valid_target)
            preserve("camera_motion")
            cleaned.loc[sync_rows, "camera_motion"] = np.interp(target_times[sync_rows], timestamps[finite], values[finite])
            record(
                "SENSOR_DESYNC", "correction", "视觉时间偏移校正",
                f"依据互相关估计将 camera_motion 校正 {sync_offset * 1000:+.1f} ms，置信度 {sync_confidence:.3f}。",
                sync_rows, ["camera_motion"], len(sync_rows),
            )
            record(
                "SENSOR_DESYNC_EDGE", "quarantine", "隔离同步校正边界",
                "时间偏移后超出原始视觉信号覆盖范围，保留原值但禁止直接进入训练。",
                sync_edge_rows, ["camera_motion"], invalidate=True,
            )

    dt = np.diff(timestamps)
    positive_dt = dt[dt > 0]
    median_dt = float(np.median(positive_dt)) if positive_dt.size else 0.02
    gap_threshold = max(
        median_dt * active_profile.gap_period_multiplier,
        median_dt + active_profile.gap_extra_seconds,
    )
    boundaries = np.flatnonzero((dt <= 0) | (dt > gap_threshold)) + 1
    segment_id = np.zeros(len(cleaned), dtype=int)
    for boundary in boundaries:
        segment_id[boundary:] += 1
    record(
        "TIMESTAMP_SEGMENT", "segmentation", "建立时间缺口边界",
        "不补造缺失样本，在异常时间间隔后建立新的连续片段。",
        boundaries, ["timestamp"],
    )
    non_monotonic_rows = np.flatnonzero(dt <= 0) + 1
    record(
        "NON_MONOTONIC_TIME", "quarantine", "隔离非单调时间戳",
        "保留采集顺序与原始时间戳，禁止倒序或重复采样进入训练。",
        non_monotonic_rows, ["timestamp"], invalidate=True,
    )

    if "frame_valid" in cleaned.columns:
        frame_valid = pd.to_numeric(cleaned["frame_valid"], errors="coerce").fillna(0).to_numpy() >= 0.5
        record(
            "FRAME_DROP", "quarantine", "隔离无效视觉帧",
            "不伪造图像内容，依据 frame_valid 标记排除丢失或重复帧。",
            np.flatnonzero(~frame_valid), ["frame_valid"], invalidate=True,
        )

    if "force_z" in cleaned.columns:
        force = pd.to_numeric(cleaned["force_z"], errors="coerce").to_numpy(dtype=float)
        force_threshold = _robust_threshold(
            np.abs(force), scale=active_profile.force_mad_scale, floor=active_profile.force_floor,
        )
        force_rows = np.flatnonzero(np.abs(force) > force_threshold)
        expanded_force_rows = sorted({index + delta for index in force_rows for delta in (-1, 0, 1) if 0 <= index + delta < len(cleaned)})
        record(
            "FORCE_SPIKE", "quarantine", "隔离异常接触力窗口",
            "保留真实接触力测量，并隔离峰值前后一个采样点供人工复核。",
            expanded_force_rows, ["force_z"], invalidate=True,
        )

    for issue in before["issues"]:
        code = issue["code"]
        if code not in {"ROBOT_STUCK", "GRASP_SLIP"}:
            continue
        start = issue.get("start_time")
        end = issue.get("end_time")
        if start is None:
            continue
        mask = (timestamps >= float(start)) & (timestamps <= float(end if end is not None else start))
        title = "隔离执行卡滞片段" if code == "ROBOT_STUCK" else "隔离抓取滑脱片段"
        record(
            code, "quarantine", title,
            "保留物理事实和任务结果，仅通过质量掩码阻止风险片段直接进入训练。",
            np.flatnonzero(mask), invalidate=True,
        )

    workspace_rows: set[int] = set()
    workspace_columns: list[str] = []
    for column, (lower, upper) in active_profile.workspace_bounds.items():
        if column not in cleaned.columns:
            continue
        values = pd.to_numeric(cleaned[column], errors="coerce").to_numpy(dtype=float)
        rows = np.flatnonzero((values < lower) | (values > upper))
        if rows.size:
            workspace_columns.append(column)
            workspace_rows.update(rows.tolist())
    record(
        "WORKSPACE_OUTLIER", "quarantine", "隔离工作空间越界点",
        "保留原始位姿，依据当前机器人 Profile 的 XYZ 边界排除危险样本。",
        sorted(workspace_rows), workspace_columns, invalidate=True,
    )

    remaining_missing = cleaned[original_columns].isna().any(axis=1).to_numpy()
    record(
        "MISSING_VALUES_UNRESOLVED", "quarantine", "隔离仍含缺失值的样本",
        "修复后仍含空值的行不会进入训练可用集合。",
        np.flatnonzero(remaining_missing), invalidate=True,
    )

    cleaned["source_row"] = source_rows
    cleaned["quality_valid"] = quality_valid
    cleaned["repair_actions"] = [";".join(sorted(items)) for items in row_actions]
    cleaned["repair_reason"] = [";".join(sorted(items)) for items in row_reasons]
    cleaned["segment_id"] = segment_id

    after = analyze_episode(cleaned[original_columns], str(episode_id), active_profile)
    actions: list[dict[str, Any]] = []
    for entry in action_log.values():
        rows = sorted(entry.pop("rows"))
        columns = sorted(entry.pop("columns"))
        actions.append({
            **entry,
            "row_count": len(rows),
            "columns": columns,
            "start_time": round(float(timestamps[rows[0]]), 4),
            "end_time": round(float(timestamps[rows[-1]]), 4),
        })
    kind_order = {"correction": 0, "segmentation": 1, "quarantine": 2}
    actions.sort(key=lambda item: (kind_order.get(item["kind"], 3), item["code"]))

    modified_rows = np.flatnonzero(np.array([bool(items) for items in row_actions]))
    preview_rows: list[dict[str, Any]] = []
    for index in modified_rows[:30]:
        changes: dict[str, dict[str, Any]] = {}
        for column in sorted(changed_columns):
            before_value = _json_value(original.at[index, column])
            after_value = _json_value(cleaned.at[index, column])
            if before_value != after_value:
                changes[column] = {"before": before_value, "after": after_value}
        preview_rows.append({
            "source_row": int(source_rows[index]),
            "timestamp": round(float(timestamps[index]), 4),
            "quality_valid": bool(quality_valid[index]),
            "actions": sorted(row_actions[index]),
            "changes": changes,
        })

    csv_bytes = cleaned.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
    artifact_hash = hashlib.sha256(csv_bytes).hexdigest()
    source_digest = source_digest or source_sha256(source_path)
    before_codes = [issue["code"] for issue in before["issues"]]
    after_codes = [issue["code"] for issue in after["issues"]]
    corrected_codes = [action["code"] for action in actions if action["kind"] == "correction"]
    quarantined_rows = int((~quality_valid).sum())
    payload = {
        "schema_version": "1.0",
        "episode_id": str(episode_id),
        "dataset_name": dataset_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "review_required" if quarantined_rows or after["issues"] else "ready",
        "policy": {
            "short_gap_limit": max_short_gap,
            "physical_events_are_preserved": True,
            "timestamp_samples_are_not_synthesized": True,
            "training_gate": "quality_valid == true",
        },
        "summary": {
            "source_rows": len(cleaned),
            "modified_rows": int(len(modified_rows)),
            "interpolated_cells": interpolated_cells,
            "joint_jump_cells": jump_cells,
            "sync_corrected_rows": int(len(sync_rows)),
            "segment_count": int(segment_id.max() + 1) if len(segment_id) else 0,
            "quarantined_rows": quarantined_rows,
            "retained_rows": int(quality_valid.sum()),
            "retained_rate": round(float(quality_valid.mean() * 100), 2),
            "before_issue_count": len(before["issues"]),
            "after_issue_count": len(after["issues"]),
            "before_quality_score": before["quality_score"],
            "after_quality_score": after["quality_score"],
        },
        "issue_resolution": {
            "before": before_codes,
            "corrected": corrected_codes,
            "resolved": sorted(set(before_codes).difference(after_codes)),
            "unresolved": after_codes,
            "quarantined": sorted({action["code"] for action in actions if action["kind"] == "quarantine"}),
        },
        "actions": actions,
        "preview_rows": preview_rows,
        "preview_truncated": len(modified_rows) > len(preview_rows),
        "analysis_profile": active_profile.to_dict(),
        "provenance": {
            "source_name": source_path.name,
            "source_sha256": source_digest,
            "artifact_sha256": artifact_hash,
            "hash_algorithm": "SHA-256",
            "adapter_id": (source or {}).get("adapter_id"),
            "adapter_name": (source or {}).get("adapter_name"),
            "source_format": (source or {}).get("source_format"),
        },
        "downloads": {
            "csv": f"/api/repair/download/{episode_id}",
            "manifest": f"/api/repair/manifest/{episode_id}",
        },
    }
    return RepairArtifact(cleaned=cleaned, payload=payload, csv_bytes=csv_bytes)
