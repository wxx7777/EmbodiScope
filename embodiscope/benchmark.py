from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .analysis import analyze_episode, estimate_sync_offset
from .profiles import AnalysisProfile, resolve_profile


TARGET_CODES = (
    "SENSOR_DESYNC",
    "FORCE_SPIKE",
    "JOINT_JUMP",
    "TIMESTAMP_GAP",
    "FRAME_DROP",
    "ROBOT_STUCK",
    "GRASP_SLIP",
)


@dataclass(frozen=True)
class FaultSpec:
    fault_id: str
    name: str
    code: str
    values: tuple[float, float, float]
    unit: str


FAULTS = (
    FaultSpec("sensor-delay", "视觉延迟", "SENSOR_DESYNC", (0.10, 0.16, 0.24), "s"),
    FaultSpec("force-spike", "接触力峰值", "FORCE_SPIKE", (42.0, 55.0, 75.0), "N"),
    FaultSpec("joint-jump", "关节突跳", "JOINT_JUMP", (0.09, 0.18, 0.35), "rad"),
    FaultSpec("timestamp-gap", "采样缺口", "TIMESTAMP_GAP", (0.08, 0.16, 0.28), "s"),
    FaultSpec("frame-drop", "连续丢帧", "FRAME_DROP", (4.0, 7.0, 12.0), "frames"),
    FaultSpec("robot-stuck", "执行卡滞", "ROBOT_STUCK", (1.4, 2.0, 2.8), "s"),
    FaultSpec("grasp-slip", "抓取滑脱", "GRASP_SLIP", (0.13, 0.18, 0.24), "m"),
)


def _prepare_base(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().reset_index(drop=True)
    if "episode_id" in data.columns:
        clean = data[data["episode_id"].astype(str) == "EP-001"]
        if not clean.empty:
            data = clean.reset_index(drop=True)
    required = {"timestamp", "episode_id", "camera_motion", "force_z", "gripper", "object_distance"}
    required.update({"ee_x", "ee_y", "ee_z"})
    if not required.issubset(data.columns) or not any(column.startswith("joint_") for column in data.columns):
        raise ValueError("Benchmark 基准需要内置多模态操作轨迹")
    data["episode_id"] = "benchmark"
    data["frame_valid"] = 1
    data["success"] = True
    return data


def _noisy_copy(base: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    data = base.copy(deep=True)
    for column in [item for item in data.columns if item.startswith("joint_")]:
        data[column] = pd.to_numeric(data[column], errors="coerce") + rng.normal(0.0, 0.00035, len(data))
    for column in ("ee_x", "ee_y", "ee_z"):
        data[column] = pd.to_numeric(data[column], errors="coerce") + rng.normal(0.0, 0.00005, len(data))
    data["force_z"] = pd.to_numeric(data["force_z"], errors="coerce") + rng.normal(0.0, 0.08, len(data))
    data["camera_motion"] = pd.to_numeric(data["camera_motion"], errors="coerce") + rng.normal(0.0, 0.0004, len(data))
    return data


def _active_window(data: pd.DataFrame, rng: np.random.Generator, duration: float) -> tuple[int, int]:
    timestamps = data["timestamp"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(timestamps)))
    length = max(3, int(round(duration / max(dt, 1e-6))))
    lower = max(20, int(len(data) * 0.42))
    upper = max(lower + 1, min(len(data) - length - 20, int(len(data) * 0.72)))
    start = int(rng.integers(lower, upper))
    return start, min(len(data), start + length)


def _inject_fault(
    base: pd.DataFrame,
    fault: FaultSpec,
    value: float,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, float | None]:
    data = _noisy_copy(base, rng)
    timestamps = data["timestamp"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(timestamps)))
    true_start: float | None = None

    if fault.fault_id == "sensor-delay":
        lag = max(1, int(round(value / dt)))
        motion = data["camera_motion"].to_numpy(dtype=float)
        delayed = np.roll(motion, lag)
        delayed[:lag] = motion[0]
        data["camera_motion"] = delayed
        true_start = float(timestamps[lag])
    elif fault.fault_id == "force-spike":
        start, end = _active_window(data, rng, 0.12)
        data.loc[start:end - 1, "force_z"] = value + np.sin(np.linspace(0, np.pi, end - start)) * value * 0.08
        true_start = float(timestamps[start])
    elif fault.fault_id == "joint-jump":
        index = int(rng.integers(int(len(data) * 0.45), int(len(data) * 0.75)))
        joints = [column for column in data.columns if column.startswith("joint_")]
        column = joints[int(rng.integers(0, len(joints)))]
        data.loc[index, column] = float(data.loc[index, column]) + value * (-1 if rng.random() < 0.5 else 1)
        true_start = float(timestamps[index])
    elif fault.fault_id == "timestamp-gap":
        index = int(rng.integers(int(len(data) * 0.42), int(len(data) * 0.72)))
        data.loc[index + 1 :, "timestamp"] = data.loc[index + 1 :, "timestamp"] + value
        true_start = float(timestamps[index])
    elif fault.fault_id == "frame-drop":
        frames = int(round(value))
        start = int(rng.integers(int(len(data) * 0.42), int(len(data) * 0.72) - frames))
        data.loc[start:start + frames - 1, "frame_valid"] = 0
        data.loc[start:start + frames - 1, "camera_motion"] = np.nan
        true_start = float(timestamps[start])
    elif fault.fault_id == "robot-stuck":
        start, end = _active_window(data, rng, value)
        for column in ["ee_x", "ee_y", "ee_z"] + [item for item in data.columns if item.startswith("joint_")]:
            values = data[column].to_numpy(dtype=float)
            held_value = values[start]
            recovery_offset = held_value - values[end] if end < len(values) else 0.0
            values[start:end] = held_value
            if end < len(values):
                values[end:] += recovery_offset
            data[column] = values
        data.loc[start:end - 1, "phase"] = "transport"
        true_start = float(timestamps[start])
    elif fault.fault_id == "grasp-slip":
        start, end = _active_window(data, rng, 0.2)
        data.loc[start:end - 1, "gripper"] = 0.18
        data.loc[start:end - 1, "object_distance"] = np.linspace(0.105, value, end - start)
        data.loc[end:, "object_distance"] = value
        true_start = float(timestamps[start])
    else:
        raise ValueError(f"未知 Benchmark 故障: {fault.fault_id}")

    data["success"] = False
    return data, true_start


def _segment_duration(mask: np.ndarray, timestamps: np.ndarray) -> float:
    best = current = 0.0
    start: int | None = None
    for index, active in enumerate(mask):
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(mask) - 1):
            end = index if active and index == len(mask) - 1 else index - 1
            current = max(0.0, float(timestamps[end] - timestamps[start]))
            best = max(best, current)
            start = None
    return best


def _fixed_baseline_predictions(data: pd.DataFrame) -> set[str]:
    predictions: set[str] = set()
    timestamps = data["timestamp"].to_numpy(dtype=float)
    dt = np.diff(timestamps)
    median_dt = float(np.median(dt[dt > 0])) if np.any(dt > 0) else 0.02
    if np.any(dt > max(0.10, median_dt * 4.0)):
        predictions.add("TIMESTAMP_GAP")
    if "frame_valid" in data.columns and np.any(pd.to_numeric(data["frame_valid"], errors="coerce").fillna(0).to_numpy() < 0.5):
        predictions.add("FRAME_DROP")

    joints = [column for column in data.columns if column.startswith("joint_")]
    if joints:
        values = data[joints].apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both").to_numpy()
        safe_dt = np.diff(timestamps, prepend=timestamps[0])
        safe_dt[safe_dt <= 0] = median_dt
        velocity = np.abs(np.diff(values, axis=0, prepend=values[[0]]) / safe_dt[:, None])
        if np.any(velocity > 8.0):
            predictions.add("JOINT_JUMP")

    force = pd.to_numeric(data["force_z"], errors="coerce").to_numpy(dtype=float)
    if np.nanmax(np.abs(force)) > 50.0:
        predictions.add("FORCE_SPIKE")

    positions = data[["ee_x", "ee_y", "ee_z"]].apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both").to_numpy()
    safe_dt = np.diff(timestamps, prepend=timestamps[0])
    safe_dt[safe_dt <= 0] = median_dt
    speed = np.linalg.norm(np.diff(positions, axis=0, prepend=positions[[0]]) / safe_dt[:, None], axis=1)
    active = ~data["phase"].astype(str).str.lower().isin(["idle", "reset"]).to_numpy()
    if _segment_duration((speed < 0.003) & active, timestamps) >= 1.5:
        predictions.add("ROBOT_STUCK")

    offset, confidence = estimate_sync_offset(speed, pd.to_numeric(data["camera_motion"], errors="coerce").to_numpy(), median_dt)
    if abs(offset) >= 0.15 and confidence >= 0.35:
        predictions.add("SENSOR_DESYNC")

    gripper = pd.to_numeric(data["gripper"], errors="coerce").to_numpy()
    distance = pd.to_numeric(data["object_distance"], errors="coerce").to_numpy()
    slip = (gripper < 0.3) & (distance > 0.1) & (np.diff(distance, prepend=distance[0]) > 0.002)
    if int(slip.sum()) >= 6:
        predictions.add("GRASP_SLIP")
    return predictions


def _metrics(records: list[dict[str, Any]], prediction_key: str) -> dict[str, Any]:
    per_class = []
    for code in TARGET_CODES:
        tp = fp = fn = 0
        for record in records:
            expected = record["expected_code"] == code
            predicted = code in record[prediction_key]
            tp += int(expected and predicted)
            fp += int(not expected and predicted)
            fn += int(expected and not predicted)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class.append({
            "code": code,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        })
    nominal = [record for record in records if record["expected_code"] is None]
    nominal_false = sum(bool(set(record[prediction_key]).intersection(TARGET_CODES)) for record in nominal)
    exact = sum(
        set(record[prediction_key]).intersection(TARGET_CODES)
        == ({record["expected_code"]} if record["expected_code"] else set())
        for record in records
    )
    return {
        "macro_precision": round(float(np.mean([item["precision"] for item in per_class])), 4),
        "macro_recall": round(float(np.mean([item["recall"] for item in per_class])), 4),
        "macro_f1": round(float(np.mean([item["f1"] for item in per_class])), 4),
        "exact_match": round(exact / len(records), 4),
        "nominal_false_positive_rate": round(nominal_false / max(1, len(nominal)), 4),
        "per_class": per_class,
    }


def run_benchmark(
    source_frame: pd.DataFrame,
    profile: str | AnalysisProfile | None = None,
    seed_count: int = 8,
) -> dict[str, Any]:
    if not 2 <= seed_count <= 25:
        raise ValueError("seed_count 必须在 2 到 25 之间")
    active_profile = resolve_profile(profile)
    base = _prepare_base(source_frame)
    records: list[dict[str, Any]] = []
    intensity_names = ("轻微", "中等", "严重")

    for seed in range(seed_count):
        rng = np.random.default_rng(20260716 + seed)
        nominal = _noisy_copy(base, rng)
        started = time.perf_counter()
        analysis = analyze_episode(nominal, profile=active_profile)
        latency_ms = (time.perf_counter() - started) * 1000
        records.append({
            "fault_id": "nominal", "fault_name": "正常轨迹", "intensity": "正常", "value": 0.0,
            "expected_code": None,
            "predictions": [item["code"] for item in analysis["issues"]],
            "baseline_predictions": sorted(_fixed_baseline_predictions(nominal)),
            "latency_ms": latency_ms,
            "location_error_ms": None,
            "sync_error_ms": None,
        })
        for fault in FAULTS:
            for intensity_index, value in enumerate(fault.values):
                data, true_start = _inject_fault(base, fault, value, rng)
                started = time.perf_counter()
                analysis = analyze_episode(data, profile=active_profile)
                latency_ms = (time.perf_counter() - started) * 1000
                matching = next((item for item in analysis["issues"] if item["code"] == fault.code), None)
                location_error = None
                if matching and matching.get("start_time") is not None and true_start is not None:
                    location_error = abs(float(matching["start_time"]) - true_start) * 1000
                sync_error = None
                if fault.code == "SENSOR_DESYNC":
                    sync_error = abs(abs(float(analysis["metrics"]["sync_offset_ms"])) - value * 1000)
                records.append({
                    "fault_id": fault.fault_id,
                    "fault_name": fault.name,
                    "intensity": intensity_names[intensity_index],
                    "value": value,
                    "unit": fault.unit,
                    "expected_code": fault.code,
                    "predictions": [item["code"] for item in analysis["issues"]],
                    "baseline_predictions": sorted(_fixed_baseline_predictions(data)),
                    "latency_ms": latency_ms,
                    "location_error_ms": location_error,
                    "sync_error_ms": sync_error,
                })

    metrics = _metrics(records, "predictions")
    baseline = _metrics(records, "baseline_predictions")
    latencies = np.asarray([record["latency_ms"] for record in records], dtype=float)
    localization = [record["location_error_ms"] for record in records if record["location_error_ms"] is not None]
    sync_errors = [record["sync_error_ms"] for record in records if record["sync_error_ms"] is not None]
    matrix = []
    for fault in FAULTS:
        for intensity_index, value in enumerate(fault.values):
            group = [
                record for record in records
                if record["fault_id"] == fault.fault_id and record["intensity"] == intensity_names[intensity_index]
            ]
            detected = sum(fault.code in record["predictions"] for record in group)
            baseline_detected = sum(fault.code in record["baseline_predictions"] for record in group)
            matrix.append({
                "fault_id": fault.fault_id,
                "fault_name": fault.name,
                "code": fault.code,
                "intensity": intensity_names[intensity_index],
                "value": value,
                "unit": fault.unit,
                "support": len(group),
                "detection_rate": round(detected / max(1, len(group)), 4),
                "baseline_detection_rate": round(baseline_detected / max(1, len(group)), 4),
            })

    return {
        "protocol": {
            "name": "EmbodiScope FaultBench",
            "version": "1.0",
            "seed_count": seed_count,
            "intensity_levels": 3,
            "fault_classes": len(FAULTS),
            "sample_count": len(records),
            "rows_per_sample": len(base),
            "profile": active_profile.to_dict(),
            "python": platform.python_version(),
        },
        "metrics": metrics,
        "baseline": baseline,
        "comparison": {
            "macro_f1_delta": round(metrics["macro_f1"] - baseline["macro_f1"], 4),
            "recall_delta": round(metrics["macro_recall"] - baseline["macro_recall"], 4),
            "false_positive_delta": round(
                metrics["nominal_false_positive_rate"] - baseline["nominal_false_positive_rate"], 4
            ),
        },
        "performance": {
            "latency_p50_ms": round(float(np.percentile(latencies, 50)), 2),
            "latency_p95_ms": round(float(np.percentile(latencies, 95)), 2),
            "localization_median_error_ms": round(float(np.median(localization)), 2) if localization else None,
            "sync_offset_mae_ms": round(float(np.mean(sync_errors)), 2) if sync_errors else None,
        },
        "matrix": matrix,
    }
