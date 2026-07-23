from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .analysis import analyze_episode
from .benchmark import _noisy_copy, _prepare_base
from .profiles import AnalysisProfile, resolve_profile
from .repair import build_repair_artifact, source_sha256


@dataclass(frozen=True)
class RepairSpec:
    repair_id: str
    name: str
    action_code: str
    values: tuple[float, float, float]
    unit: str
    mode: str


REPAIR_CASES = (
    RepairSpec("short-gap", "短缺口插值", "MISSING_VALUES", (1.0, 2.0, 3.0), "frames", "reconstruction"),
    RepairSpec("joint-spike", "孤立关节突跳", "JOINT_JUMP", (0.09, 0.18, 0.35), "rad", "reconstruction"),
    RepairSpec("sensor-delay", "视觉时间偏移", "SENSOR_DESYNC", (0.10, 0.16, 0.24), "s", "synchronization"),
    RepairSpec("timestamp-gap", "采样时间缺口", "TIMESTAMP_SEGMENT", (0.08, 0.16, 0.28), "s", "segmentation"),
    RepairSpec("force-spike", "真实接触力峰值", "FORCE_SPIKE", (42.0, 55.0, 75.0), "N", "isolation"),
)


def _case_window(data: pd.DataFrame, rng: np.random.Generator, width: int = 1) -> tuple[int, int]:
    lower = max(20, int(len(data) * 0.42))
    upper = max(lower + 1, int(len(data) * 0.72) - width)
    start = int(rng.integers(lower, upper))
    return start, min(len(data), start + width)


def _inject_case(
    base: pd.DataFrame,
    spec: RepairSpec,
    value: float,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    truth = _noisy_copy(base, rng)
    corrupted = truth.copy(deep=True)
    timestamps = corrupted["timestamp"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(timestamps)))
    evidence: dict[str, Any] = {"rows": [], "column": None, "expected_offset_ms": None}

    if spec.repair_id == "short-gap":
        width = int(round(value))
        start, end = _case_window(corrupted, rng, width)
        joints = [column for column in corrupted.columns if column.startswith("joint_")]
        column = joints[int(rng.integers(0, len(joints)))]
        corrupted.loc[start:end - 1, column] = np.nan
        evidence.update(rows=list(range(start, end)), column=column)
    elif spec.repair_id == "joint-spike":
        start, end = _case_window(corrupted, rng)
        joints = [column for column in corrupted.columns if column.startswith("joint_")]
        column = joints[int(rng.integers(0, len(joints)))]
        direction = -1.0 if rng.random() < 0.5 else 1.0
        corrupted.loc[start, column] = float(corrupted.loc[start, column]) + value * direction
        evidence.update(rows=[start], column=column)
    elif spec.repair_id == "sensor-delay":
        lag = max(1, int(round(value / dt)))
        motion = corrupted["camera_motion"].to_numpy(dtype=float)
        delayed = np.roll(motion, lag)
        delayed[:lag] = motion[0]
        corrupted["camera_motion"] = delayed
        evidence.update(rows=list(range(0, len(corrupted) - lag)), column="camera_motion", expected_offset_ms=lag * dt * 1000)
    elif spec.repair_id == "timestamp-gap":
        start, _ = _case_window(corrupted, rng)
        corrupted.loc[start + 1 :, "timestamp"] = corrupted.loc[start + 1 :, "timestamp"] + value
        evidence.update(rows=[start + 1], column="timestamp")
    elif spec.repair_id == "force-spike":
        width = max(4, int(round(0.12 / dt)))
        start, end = _case_window(corrupted, rng, width)
        corrupted.loc[start:end - 1, "force_z"] = value + np.sin(np.linspace(0, np.pi, end - start)) * value * 0.08
        evidence.update(rows=list(range(start, end)), column="force_z")
    else:
        raise ValueError(f"未知 RepairBench 场景: {spec.repair_id}")
    return truth, corrupted, evidence


def _changed_signal_cells(before: pd.DataFrame, after: pd.DataFrame) -> tuple[int, int]:
    columns = [
        column for column in before.columns
        if column in {"ee_x", "ee_y", "ee_z", "camera_motion", "force_z", "gripper", "object_distance"}
        or column.startswith(("joint_", "state_", "action_"))
    ]
    changed = total = 0
    for column in columns:
        left = pd.to_numeric(before[column], errors="coerce").to_numpy(dtype=float)
        right = pd.to_numeric(after[column], errors="coerce").to_numpy(dtype=float)
        equal = np.isclose(left, right, rtol=0.0, atol=1e-12, equal_nan=True)
        changed += int((~equal).sum())
        total += len(equal)
    return changed, total


def _record_case(
    truth: pd.DataFrame,
    corrupted: pd.DataFrame,
    evidence: dict[str, Any],
    spec: RepairSpec,
    intensity: str,
    value: float,
    source_path: Path,
    source_digest: str,
    active_profile: AnalysisProfile,
) -> dict[str, Any]:
    started = time.perf_counter()
    artifact = build_repair_artifact(
        corrupted,
        "repairbench",
        source_path,
        "RepairBench synthetic trajectory",
        {"adapter_id": "repairbench", "adapter_name": "RepairBench", "source_format": "synthetic"},
        active_profile,
        source_digest=source_digest,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    cleaned = artifact.cleaned[list(corrupted.columns)]
    action_codes = {action["code"] for action in artifact.payload["actions"]}
    target_rows = np.asarray(evidence["rows"], dtype=int)
    quality_valid = artifact.cleaned["quality_valid"].to_numpy(dtype=bool)
    squared_error_sum = 0.0
    error_count = 0
    isolation_recall: float | None = None
    preservation_rate: float | None = None
    sync_residual_ms: float | None = None

    if spec.mode == "reconstruction":
        column = str(evidence["column"])
        expected = pd.to_numeric(truth.loc[target_rows, column], errors="coerce").to_numpy(dtype=float)
        actual = pd.to_numeric(cleaned.loc[target_rows, column], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(expected) & np.isfinite(actual)
        squared_error_sum = float(np.square(actual[valid] - expected[valid]).sum())
        error_count = int(valid.sum())
    elif spec.mode == "synchronization":
        valid_rows = np.flatnonzero(quality_valid)
        expected = pd.to_numeric(truth.loc[valid_rows, "camera_motion"], errors="coerce").to_numpy(dtype=float)
        actual = pd.to_numeric(cleaned.loc[valid_rows, "camera_motion"], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(expected) & np.isfinite(actual)
        squared_error_sum = float(np.square(actual[valid] - expected[valid]).sum())
        error_count = int(valid.sum())
        post = analyze_episode(cleaned, profile=active_profile)
        sync_residual_ms = abs(float(post["metrics"]["sync_offset_ms"]))
    elif spec.mode == "segmentation":
        isolation_recall = float(artifact.payload["summary"]["segment_count"] >= 2)
        preservation_rate = float(len(cleaned) == len(corrupted))
    elif spec.mode == "isolation":
        isolation_recall = float((~quality_valid[target_rows]).mean()) if len(target_rows) else 0.0
        before_force = pd.to_numeric(corrupted["force_z"], errors="coerce").to_numpy(dtype=float)
        after_force = pd.to_numeric(cleaned["force_z"], errors="coerce").to_numpy(dtype=float)
        preservation_rate = float(np.isclose(before_force, after_force, equal_nan=True).mean())

    successful = spec.action_code in action_codes
    if spec.mode == "segmentation":
        successful = successful and isolation_recall == 1.0 and preservation_rate == 1.0
    elif spec.mode == "isolation":
        successful = successful and (isolation_recall or 0.0) >= 0.95 and preservation_rate == 1.0
    elif spec.mode in {"reconstruction", "synchronization"}:
        successful = successful and error_count > 0

    return {
        "repair_id": spec.repair_id,
        "repair_name": spec.name,
        "action_code": spec.action_code,
        "mode": spec.mode,
        "intensity": intensity,
        "value": value,
        "unit": spec.unit,
        "success": bool(successful),
        "squared_error_sum": squared_error_sum,
        "error_count": error_count,
        "rmse": round(float(np.sqrt(squared_error_sum / error_count)), 8) if error_count else None,
        "sync_residual_ms": sync_residual_ms,
        "isolation_recall": isolation_recall,
        "preservation_rate": preservation_rate,
        "retained_rate": float(artifact.payload["summary"]["retained_rate"]) / 100.0,
        "latency_ms": latency_ms,
    }


def run_repair_benchmark(
    source_frame: pd.DataFrame,
    source_path: Path,
    profile: str | AnalysisProfile | None = None,
    seed_count: int = 4,
) -> dict[str, Any]:
    if not 2 <= seed_count <= 12:
        raise ValueError("RepairBench seed_count 必须在 2 到 12 之间")
    active_profile = resolve_profile(profile)
    base = _prepare_base(source_frame)
    base["episode_id"] = "repairbench"
    digest = source_sha256(source_path)
    records: list[dict[str, Any]] = []
    nominal_records: list[dict[str, Any]] = []
    intensity_names = ("轻微", "中等", "严重")

    for seed in range(seed_count):
        rng = np.random.default_rng(20260719 + seed)
        nominal = _noisy_copy(base, rng)
        started = time.perf_counter()
        artifact = build_repair_artifact(
            nominal,
            "repairbench",
            source_path,
            "RepairBench nominal trajectory",
            {"adapter_id": "repairbench", "adapter_name": "RepairBench", "source_format": "synthetic"},
            active_profile,
            source_digest=digest,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        changed, total = _changed_signal_cells(nominal, artifact.cleaned[list(nominal.columns)])
        nominal_records.append({
            "changed_cells": changed,
            "signal_cells": total,
            "quarantined_rows": int((~artifact.cleaned["quality_valid"].to_numpy(dtype=bool)).sum()),
            "rows": len(nominal),
            "latency_ms": latency_ms,
        })
        for spec in REPAIR_CASES:
            for intensity_index, value in enumerate(spec.values):
                truth, corrupted, evidence = _inject_case(base, spec, value, rng)
                records.append(_record_case(
                    truth, corrupted, evidence, spec, intensity_names[intensity_index], value,
                    source_path, digest, active_profile,
                ))

    per_class: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    for spec in REPAIR_CASES:
        group = [record for record in records if record["repair_id"] == spec.repair_id]
        squared_error = sum(record["squared_error_sum"] for record in group)
        error_count = sum(record["error_count"] for record in group)
        isolation = [record["isolation_recall"] for record in group if record["isolation_recall"] is not None]
        preservation = [record["preservation_rate"] for record in group if record["preservation_rate"] is not None]
        per_class.append({
            "repair_id": spec.repair_id,
            "name": spec.name,
            "action_code": spec.action_code,
            "mode": spec.mode,
            "support": len(group),
            "success_rate": round(sum(record["success"] for record in group) / max(1, len(group)), 4),
            "reconstruction_rmse": round(float(np.sqrt(squared_error / error_count)), 8) if error_count else None,
            "isolation_recall": round(float(np.mean(isolation)), 4) if isolation else None,
            "preservation_rate": round(float(np.mean(preservation)), 4) if preservation else None,
            "average_retained_rate": round(float(np.mean([record["retained_rate"] for record in group])), 4),
        })
        for intensity_index, value in enumerate(spec.values):
            intensity = intensity_names[intensity_index]
            subset = [record for record in group if record["intensity"] == intensity]
            errors = [record["rmse"] for record in subset if record["rmse"] is not None]
            matrix.append({
                "repair_id": spec.repair_id,
                "name": spec.name,
                "mode": spec.mode,
                "intensity": intensity,
                "value": value,
                "unit": spec.unit,
                "support": len(subset),
                "success_rate": round(sum(record["success"] for record in subset) / max(1, len(subset)), 4),
                "rmse": round(float(np.mean(errors)), 8) if errors else None,
                "retained_rate": round(float(np.mean([record["retained_rate"] for record in subset])), 4),
            })

    total_error = sum(record["squared_error_sum"] for record in records)
    total_error_count = sum(record["error_count"] for record in records)
    nominal_changed = sum(record["changed_cells"] for record in nominal_records)
    nominal_cells = sum(record["signal_cells"] for record in nominal_records)
    nominal_quarantined = sum(record["quarantined_rows"] for record in nominal_records)
    nominal_rows = sum(record["rows"] for record in nominal_records)
    force_records = [record for record in records if record["repair_id"] == "force-spike"]
    segment_records = [record for record in records if record["repair_id"] == "timestamp-gap"]
    sync_records = [record for record in records if record["sync_residual_ms"] is not None]
    latencies = np.asarray(
        [record["latency_ms"] for record in records] + [record["latency_ms"] for record in nominal_records],
        dtype=float,
    )
    metrics = {
        "repair_success_rate": round(sum(record["success"] for record in records) / len(records), 4),
        "reconstruction_rmse": round(float(np.sqrt(total_error / total_error_count)), 8) if total_error_count else None,
        "sync_residual_mae_ms": round(float(np.mean([record["sync_residual_ms"] for record in sync_records])), 3),
        "nominal_overcorrection_rate": round(nominal_changed / max(1, nominal_cells), 6),
        "nominal_false_quarantine_rate": round(nominal_quarantined / max(1, nominal_rows), 6),
        "risk_isolation_recall": round(float(np.mean([record["isolation_recall"] for record in force_records])), 4),
        "physical_measurement_preservation": round(float(np.mean([record["preservation_rate"] for record in force_records])), 4),
        "segmentation_recall": round(float(np.mean([record["isolation_recall"] for record in segment_records])), 4),
        "average_retained_rate": round(float(np.mean([record["retained_rate"] for record in records])), 4),
    }
    gates = [
        {"name": "修复动作成功率", "value": metrics["repair_success_rate"], "operator": ">=", "threshold": 0.95, "passed": metrics["repair_success_rate"] >= 0.95},
        {"name": "重建 RMSE", "value": metrics["reconstruction_rmse"], "operator": "<=", "threshold": 0.01, "passed": metrics["reconstruction_rmse"] <= 0.01},
        {"name": "正常轨迹过度修复率", "value": metrics["nominal_overcorrection_rate"], "operator": "<=", "threshold": 0.001, "passed": metrics["nominal_overcorrection_rate"] <= 0.001},
        {"name": "风险隔离召回率", "value": metrics["risk_isolation_recall"], "operator": ">=", "threshold": 0.95, "passed": metrics["risk_isolation_recall"] >= 0.95},
        {"name": "物理测量保持率", "value": metrics["physical_measurement_preservation"], "operator": "=", "threshold": 1.0, "passed": metrics["physical_measurement_preservation"] == 1.0},
        {"name": "时间分段召回率", "value": metrics["segmentation_recall"], "operator": "=", "threshold": 1.0, "passed": metrics["segmentation_recall"] == 1.0},
    ]
    return {
        "protocol": {
            "name": "EmbodiScope RepairBench",
            "version": "1.0",
            "seed_count": seed_count,
            "intensity_levels": 3,
            "repair_classes": len(REPAIR_CASES),
            "sample_count": len(records) + len(nominal_records),
            "fault_sample_count": len(records),
            "nominal_sample_count": len(nominal_records),
            "rows_per_sample": len(base),
            "profile": active_profile.to_dict(),
            "source_sha256": digest,
            "python": platform.python_version(),
        },
        "status": "passed" if all(gate["passed"] for gate in gates) else "review_required",
        "metrics": metrics,
        "performance": {
            "latency_p50_ms": round(float(np.percentile(latencies, 50)), 2),
            "latency_p95_ms": round(float(np.percentile(latencies, 95)), 2),
        },
        "quality_gates": gates,
        "per_class": per_class,
        "matrix": matrix,
    }
