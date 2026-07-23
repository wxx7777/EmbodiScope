from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .profiles import AnalysisProfile, resolve_profile


@dataclass
class Issue:
    code: str
    severity: str
    category: str
    title: str
    description: str
    recommendation: str
    count: int = 1
    start_time: float | None = None
    end_time: float | None = None
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if np.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _robust_threshold(values: np.ndarray, scale: float = 7.0, floor: float = 0.0) -> float:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return floor
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    return max(floor, median + scale * 1.4826 * max(mad, 1e-9))


def _contiguous_segments(mask: np.ndarray, timestamps: np.ndarray, min_duration: float) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(mask):
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(mask) - 1):
            end = index if active and index == len(mask) - 1 else index - 1
            if end > start and timestamps[end] - timestamps[start] >= min_duration:
                segments.append((start, end))
            start = None
    return segments


def validate_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "episode_id"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"CSV 缺少必需列: {', '.join(missing)}")

    data = frame.copy()
    data["timestamp"] = pd.to_numeric(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().all():
        raise ValueError("timestamp 列没有可用的数值")
    data["episode_id"] = data["episode_id"].astype(str)
    data = data.dropna(subset=["timestamp"]).reset_index(drop=True)
    return data


def estimate_sync_offset(
    robot_signal: np.ndarray,
    sensor_signal: np.ndarray,
    sample_period: float,
    max_lag_seconds: float = 0.5,
) -> tuple[float, float]:
    """Return offset in seconds; positive means the sensor arrives after robot motion."""
    robot = np.asarray(robot_signal, dtype=float)
    sensor = np.asarray(sensor_signal, dtype=float)
    valid = np.isfinite(robot) & np.isfinite(sensor)
    if valid.sum() < 20 or sample_period <= 0:
        return 0.0, 0.0
    robot = np.interp(np.arange(len(robot)), np.flatnonzero(np.isfinite(robot)), robot[np.isfinite(robot)])
    sensor = np.interp(np.arange(len(sensor)), np.flatnonzero(np.isfinite(sensor)), sensor[np.isfinite(sensor)])
    robot = (robot - robot.mean()) / (robot.std() + 1e-9)
    sensor = (sensor - sensor.mean()) / (sensor.std() + 1e-9)
    max_lag = min(int(max_lag_seconds / sample_period), len(robot) // 3)
    scores: list[tuple[int, float]] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            left, right = robot[:-lag], sensor[lag:]
        elif lag < 0:
            left, right = robot[-lag:], sensor[:lag]
        else:
            left, right = robot, sensor
        if len(left) < 10:
            continue
        scores.append((lag, float(np.corrcoef(left, right)[0, 1])))
    if not scores:
        return 0.0, 0.0
    best_lag, confidence = max(scores, key=lambda item: item[1])
    return best_lag * sample_period, max(0.0, confidence)


def _motion_speed(data: pd.DataFrame, timestamps: np.ndarray) -> np.ndarray:
    position_columns = [column for column in ("ee_x", "ee_y", "ee_z") if column in data.columns]
    if len(position_columns) >= 2:
        positions = data[position_columns].apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both").to_numpy()
        dt = np.diff(timestamps, prepend=timestamps[0])
        median_dt = np.median(dt[dt > 0]) if np.any(dt > 0) else 0.02
        dt[dt <= 0] = median_dt
        velocity = np.diff(positions, axis=0, prepend=positions[[0]]) / dt[:, None]
        return np.linalg.norm(velocity, axis=1)
    joint_columns = [column for column in data.columns if column.startswith("joint_")]
    if joint_columns:
        joints = data[joint_columns].apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both").to_numpy()
        dt = np.diff(timestamps, prepend=timestamps[0])
        median_dt = np.median(dt[dt > 0]) if np.any(dt > 0) else 0.02
        dt[dt <= 0] = median_dt
        velocity = np.diff(joints, axis=0, prepend=joints[[0]]) / dt[:, None]
        return np.linalg.norm(velocity, axis=1)
    return np.zeros(len(data))


def _has_robot_motion_signal(data: pd.DataFrame) -> bool:
    position_columns = [column for column in ("ee_x", "ee_y", "ee_z") if column in data.columns]
    return len(position_columns) >= 2 or any(column.startswith("joint_") for column in data.columns)


def analyze_episode(
    frame: pd.DataFrame,
    episode_id: str | None = None,
    profile: str | AnalysisProfile | None = None,
) -> dict[str, Any]:
    active_profile = resolve_profile(profile)
    data = validate_dataframe(frame)
    if episode_id is not None:
        data = data[data["episode_id"] == str(episode_id)].copy()
    if data.empty:
        raise ValueError(f"找不到 episode: {episode_id}")

    data = data.reset_index(drop=True)
    timestamps = data["timestamp"].to_numpy(dtype=float)
    duration = max(0.0, float(timestamps[-1] - timestamps[0]))
    dt = np.diff(timestamps)
    positive_dt = dt[dt > 0]
    median_dt = float(np.median(positive_dt)) if positive_dt.size else 0.02
    sample_rate = 1.0 / median_dt if median_dt > 0 else 0.0
    issues: list[Issue] = []
    events: list[dict[str, Any]] = []

    numeric = data.select_dtypes(include=[np.number])
    inspected_columns = [column for column in numeric.columns if column != "timestamp"]
    missing_cells = int(data[inspected_columns].isna().sum().sum()) if inspected_columns else 0
    total_cells = max(1, len(data) * max(1, len(inspected_columns)))
    missing_rate = missing_cells / total_cells
    if missing_cells:
        affected = data[inspected_columns].isna().sum()
        affected = affected[affected > 0].sort_values(ascending=False)
        issues.append(Issue(
            "MISSING_VALUES", "warning" if missing_rate < 0.02 else "critical", "完整性",
            "传感器数据存在缺失", f"检测到 {missing_cells} 个空值，影响 {len(affected)} 个信号通道。",
            "回查采集节点状态；短缺口可插值，连续缺失片段应剔除或重新采集。",
            missing_cells, evidence=", ".join(f"{key}: {value}" for key, value in affected.head(4).items()),
        ))

    frame_drop_rows = np.array([], dtype=int)
    frame_drop_segments: list[tuple[int, int]] = []
    if "frame_valid" in data.columns:
        frame_valid = pd.to_numeric(data["frame_valid"], errors="coerce").fillna(0.0).to_numpy()
        frame_drop_rows = np.flatnonzero(frame_valid < 0.5)
        frame_drop_segments = _contiguous_segments(
            frame_valid < 0.5,
            timestamps,
            min_duration=max(median_dt, active_profile.frame_drop_min_duration),
        )
        if frame_drop_rows.size:
            issues.append(Issue(
                "FRAME_DROP", "critical", "完整性", "视觉流存在连续丢帧",
                f"检测到 {len(frame_drop_rows)} 个无效视频帧，形成 {max(1, len(frame_drop_segments))} 个连续缺口。",
                "检查相机采集队列、编码线程和磁盘写入延迟；训练前应剔除或重新采集连续丢帧片段。",
                int(len(frame_drop_rows)), float(timestamps[frame_drop_rows[0]]), float(timestamps[frame_drop_rows[-1]]),
                evidence=f"frame_valid=0，占本回合 {len(frame_drop_rows) / len(data) * 100:.1f}%",
            ))
            segments = frame_drop_segments or [(int(frame_drop_rows[0]), int(frame_drop_rows[-1]))]
            for start, end in segments:
                events.append({
                    "time": float(timestamps[start]),
                    "end_time": float(timestamps[end]),
                    "type": "frame-drop",
                    "severity": "critical",
                    "label": "视觉连续丢帧",
                })

    non_monotonic = int(np.sum(dt <= 0))
    gap_threshold = max(
        median_dt * active_profile.gap_period_multiplier,
        median_dt + active_profile.gap_extra_seconds,
    )
    gap_indices = np.flatnonzero(dt > gap_threshold)
    jitter = float(np.std(positive_dt) / (np.mean(positive_dt) + 1e-9)) if positive_dt.size else 0.0
    if non_monotonic:
        issues.append(Issue(
            "NON_MONOTONIC_TIME", "critical", "时序", "时间戳不单调",
            f"发现 {non_monotonic} 处倒序或重复时间戳，可能破坏速度计算和多模态对齐。",
            "按采集序列追查时钟源，禁止仅通过排序掩盖采集顺序错误。", non_monotonic,
        ))
    if gap_indices.size:
        largest_gap = float(dt[gap_indices].max())
        issues.append(Issue(
            "TIMESTAMP_GAP", "warning", "时序", "采样流存在时间缺口",
            f"发现 {len(gap_indices)} 个异常采样间隔，最大缺口 {largest_gap * 1000:.0f} ms。",
            "检查消息队列拥塞、磁盘写入和传感器掉线；训练前标记缺口边界。", len(gap_indices),
            float(timestamps[gap_indices[0]]), float(timestamps[gap_indices[-1] + 1]),
            evidence=f"正常周期约 {median_dt * 1000:.1f} ms",
        ))
        for index in gap_indices:
            events.append({"time": float(timestamps[index]), "type": "gap", "severity": "warning", "label": "采样缺口"})

    speed = _motion_speed(data, timestamps)
    joint_columns = [column for column in data.columns if column.startswith("joint_")]
    jump_rows: np.ndarray = np.array([], dtype=int)
    if joint_columns:
        joints = data[joint_columns].apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both").to_numpy()
        safe_dt = np.diff(timestamps, prepend=timestamps[0])
        safe_dt[safe_dt <= 0] = median_dt
        joint_velocity = np.abs(np.diff(joints, axis=0, prepend=joints[[0]]) / safe_dt[:, None])
        channel_thresholds = np.array([
            _robust_threshold(
                joint_velocity[:, index],
                scale=active_profile.joint_velocity_mad_scale,
                floor=active_profile.joint_velocity_floor,
            )
            for index in range(joint_velocity.shape[1])
        ])
        jump_rows = np.flatnonzero(np.any(joint_velocity > channel_thresholds, axis=1))
        if jump_rows.size:
            max_velocity = float(np.nanmax(joint_velocity[jump_rows]))
            issues.append(Issue(
                "JOINT_JUMP", "critical", "运动", "关节状态出现突跳",
                f"检测到 {len(jump_rows)} 个异常控制点，峰值关节速度 {max_velocity:.1f} rad/s。",
                "核对编码器、控制周期和动作归一化；真机执行前应拦截该轨迹。", len(jump_rows),
                float(timestamps[jump_rows[0]]), float(timestamps[jump_rows[-1]]),
                evidence=f"鲁棒阈值：中位数 + {active_profile.joint_velocity_mad_scale:g} MAD，物理下限 {active_profile.joint_velocity_floor:g} rad/s",
            ))
            for index in jump_rows[:30]:
                events.append({"time": float(timestamps[index]), "type": "jump", "severity": "critical", "label": "关节突跳"})

    active_mask = np.ones(len(data), dtype=bool)
    if "phase" in data.columns:
        active_mask = ~data["phase"].astype(str).str.lower().isin(["idle", "reset"]).to_numpy()
    stuck_mask = (
        (speed < active_profile.stuck_speed_threshold) & active_mask
        if _has_robot_motion_signal(data) else np.zeros(len(data), dtype=bool)
    )
    stuck_segments = _contiguous_segments(stuck_mask, timestamps, min_duration=active_profile.stuck_min_duration)
    if stuck_segments:
        total_stuck = sum(timestamps[end] - timestamps[start] for start, end in stuck_segments)
        issues.append(Issue(
            "ROBOT_STUCK", "critical", "任务", "机器人长时间无有效运动",
            f"发现 {len(stuck_segments)} 个卡滞片段，累计 {total_stuck:.1f} 秒。",
            "结合接触力和控制指令判断是环境阻挡、规划失败还是执行器未响应。", len(stuck_segments),
            float(timestamps[stuck_segments[0][0]]), float(timestamps[stuck_segments[-1][1]]),
        ))
        for start, end in stuck_segments:
            events.append({"time": float(timestamps[start]), "end_time": float(timestamps[end]), "type": "stuck", "severity": "critical", "label": "运动卡滞"})

    sync_offset, sync_confidence = 0.0, 0.0
    if "camera_motion" in data.columns:
        camera_motion = pd.to_numeric(data["camera_motion"], errors="coerce").to_numpy()
        sync_offset, sync_confidence = estimate_sync_offset(
            speed,
            camera_motion,
            median_dt,
            max_lag_seconds=active_profile.sync_max_lag_seconds,
        )
        if abs(sync_offset) >= active_profile.sync_warning_seconds and sync_confidence >= active_profile.sync_min_confidence:
            direction = "滞后" if sync_offset > 0 else "超前"
            issues.append(Issue(
                "SENSOR_DESYNC", "critical" if abs(sync_offset) >= active_profile.sync_critical_seconds else "warning", "同步",
                "视觉与机器人状态不同步",
                f"相机运动信号相对机器人状态{direction}约 {abs(sync_offset) * 1000:.0f} ms，相关置信度 {sync_confidence:.2f}。",
                "统一硬件时钟或在数据导出阶段应用时间偏移校正，并重新验证对齐结果。", 1,
                evidence="基于末端速度与图像运动强度的限窗互相关估计",
            ))
            events.append({"time": float(timestamps[len(timestamps) // 2]), "type": "sync", "severity": "warning", "label": f"视觉偏移 {sync_offset * 1000:.0f} ms"})

    force_spikes = np.array([], dtype=int)
    if "force_z" in data.columns:
        force = pd.to_numeric(data["force_z"], errors="coerce").to_numpy()
        force_threshold = _robust_threshold(
            np.abs(force),
            scale=active_profile.force_mad_scale,
            floor=active_profile.force_floor,
        )
        force_spikes = np.flatnonzero(np.abs(force) > force_threshold)
        if force_spikes.size:
            peak_force = float(np.nanmax(np.abs(force[force_spikes])))
            issues.append(Issue(
                "FORCE_SPIKE", "critical", "安全", "检测到异常接触力峰值",
                f"{len(force_spikes)} 个采样点超过安全统计阈值，峰值 {peak_force:.1f} N。",
                "检查碰撞位置、力控参数和环境模型；该片段不应直接用于模仿学习。", len(force_spikes),
                float(timestamps[force_spikes[0]]), float(timestamps[force_spikes[-1]]), evidence=f"检测阈值 {force_threshold:.1f} N",
            ))
            for index in force_spikes[:30]:
                events.append({"time": float(timestamps[index]), "type": "collision", "severity": "critical", "label": "接触力峰值"})

    gripper_response_segments: list[tuple[int, int]] = []
    if {"gripper", "gripper_command"}.issubset(data.columns):
        gripper = pd.to_numeric(data["gripper"], errors="coerce").to_numpy()
        command = pd.to_numeric(data["gripper_command"], errors="coerce").to_numpy()
        response_mismatch = (command < -0.5) & (gripper > 0.65)
        gripper_response_segments = _contiguous_segments(
            response_mismatch,
            timestamps,
            min_duration=0.5,
        )
        if gripper_response_segments:
            mismatch_count = sum(end - start + 1 for start, end in gripper_response_segments)
            issues.append(Issue(
                "GRIPPER_RESPONSE_FAILURE", "critical", "控制", "夹爪闭合指令未得到执行",
                f"检测到 {mismatch_count} 个采样点持续请求闭合，但夹爪仍保持张开。",
                "检查夹爪驱动、动作映射、限位和控制器反馈；恢复前不要开始运输阶段。", mismatch_count,
                float(timestamps[gripper_response_segments[0][0]]),
                float(timestamps[gripper_response_segments[-1][1]]),
                evidence="gripper_command < -0.5 且 gripper > 0.65",
            ))
            events.append({
                "time": float(timestamps[gripper_response_segments[0][0]]),
                "end_time": float(timestamps[gripper_response_segments[-1][1]]),
                "type": "gripper-response",
                "severity": "critical",
                "label": "夹爪执行器无响应",
            })

    slip_rows = np.array([], dtype=int)
    grasp_loss_rows = np.array([], dtype=int)
    if {"gripper", "object_distance"}.issubset(data.columns):
        gripper = pd.to_numeric(data["gripper"], errors="coerce").to_numpy()
        distance = pd.to_numeric(data["object_distance"], errors="coerce").to_numpy()
        distance_change = np.diff(distance, prepend=distance[0])
        slip_rows = np.flatnonzero(
            (gripper < active_profile.slip_gripper_closed)
            & (distance > active_profile.slip_distance_threshold)
            & (distance_change > active_profile.slip_step_threshold)
        )
        if "is_grasped" in data.columns:
            grasped = data["is_grasped"].astype(bool).to_numpy()
            grasp_loss_rows = np.flatnonzero(
                np.r_[False, grasped[:-1] & ~grasped[1:]]
                & (gripper < active_profile.slip_gripper_closed)
            )
        slip_detected = slip_rows.size >= 3 or grasp_loss_rows.size > 0
        if slip_detected:
            evidence_rows = slip_rows if slip_rows.size >= 3 else grasp_loss_rows
            description = (
                f"夹爪保持闭合时，目标距离在 {len(slip_rows)} 个采样点持续增大。"
                if slip_rows.size >= 3
                else "物理抓取状态从已抓取变为未抓取，但夹爪仍保持闭合。"
            )
            issues.append(Issue(
                "GRASP_SLIP", "critical", "任务", "疑似抓取滑脱",
                description,
                "回放对应视觉帧，检查抓取位姿、夹持力和目标检测稳定性。", len(evidence_rows),
                float(timestamps[evidence_rows[0]]), float(timestamps[evidence_rows[-1]]),
            ))
            events.append({"time": float(timestamps[evidence_rows[0]]), "end_time": float(timestamps[evidence_rows[-1]]), "type": "slip", "severity": "critical", "label": "抓取滑脱"})
    slip_detected = slip_rows.size >= 3 or grasp_loss_rows.size > 0

    workspace_outliers = 0
    bounds = active_profile.workspace_bounds
    for column, (lower, upper) in bounds.items():
        if column in data.columns:
            values = pd.to_numeric(data[column], errors="coerce")
            workspace_outliers += int(((values < lower) | (values > upper)).sum())
    if workspace_outliers:
        issues.append(Issue(
            "WORKSPACE_OUTLIER", "critical", "安全", "末端位姿超出工作空间",
            f"发现 {workspace_outliers} 个超出默认安全边界的位置采样。",
            "根据机器人型号配置精确工作空间，并在执行层增加限位保护。", workspace_outliers,
        ))

    completion = 100.0 - min(100.0, missing_rate * 1200.0)
    temporal = 100.0 - min(100.0, non_monotonic * 20.0 + len(gap_indices) * 12.0 + max(0.0, jitter - 0.08) * 80.0)
    motion = 100.0 - min(100.0, len(jump_rows) * 9.0 + len(stuck_segments) * 42.0 + len(gripper_response_segments) * 35.0)
    sync_score = 100.0 - min(100.0, abs(sync_offset) * 350.0) if sync_confidence >= 0.25 else 80.0
    safety_penalty = workspace_outliers * 8.0
    if force_spikes.size:
        safety_penalty += 55.0 + min(30.0, len(force_spikes) * 5.0)
    if slip_detected:
        safety_penalty += 62.0
    safety = 100.0 - min(100.0, safety_penalty)
    scores = {
        "completeness": round(max(0.0, completion), 1),
        "temporal": round(max(0.0, temporal), 1),
        "motion": round(max(0.0, motion), 1),
        "sync": round(max(0.0, sync_score), 1),
        "safety": round(max(0.0, safety), 1),
    }
    overall = (
        scores["completeness"] * 0.22 + scores["temporal"] * 0.20 + scores["motion"] * 0.20
        + scores["sync"] * 0.18 + scores["safety"] * 0.20
    )
    critical_count = sum(issue.severity == "critical" for issue in issues)
    overall -= critical_count * 6.0
    if critical_count:
        overall = min(overall, 79.0)
    if min(scores.values()) < 40.0:
        overall = min(overall, 69.0)
    overall = max(0.0, overall)

    success_known = "success" in data.columns
    if "success_known" in data.columns:
        success_known = str(data["success_known"].iloc[-1]).strip().lower() in {"1", "true", "yes"}
    success = False
    if success_known and "success" in data.columns:
        value = data["success"].iloc[-1]
        success = str(value).strip().lower() in {"1", "true", "yes", "success"}
    root_causes: list[dict[str, str]] = []
    candidates = [
        (frame_drop_rows.size > 0, "视觉采集丢帧", "高", "frame_valid 标记出连续无效视频帧"),
        (force_spikes.size > 0, "碰撞/接触异常", "高", "接触力出现显著峰值"),
        (len(stuck_segments) > 0, "执行卡滞", "高", "任务阶段持续无有效运动"),
        (bool(gripper_response_segments), "夹爪执行失败", "高", "闭合指令与执行反馈持续不一致"),
        (slip_detected, "抓取滑脱", "高", "闭合夹爪未能保持目标"),
        (
            abs(sync_offset) >= active_profile.sync_warning_seconds
            and sync_confidence >= active_profile.sync_min_confidence,
            "多模态错位", "中", "视觉与状态时间轴不一致",
        ),
        (jump_rows.size > 0, "控制不稳定", "中", "关节速度出现离群峰值"),
    ]
    for condition, label, confidence, reason in candidates:
        if condition:
            root_causes.append({"label": label, "confidence": confidence, "reason": reason})
    if not root_causes and success_known and not success:
        root_causes.append({"label": "任务结果异常", "confidence": "低", "reason": "日志未包含足够信号定位根因"})

    issues.sort(key=lambda issue: {"critical": 0, "warning": 1, "info": 2}.get(issue.severity, 3))
    events.sort(key=lambda event: event["time"])
    return {
        "episode_id": str(data["episode_id"].iloc[0]),
        "analysis_profile": active_profile.to_dict(),
        "rows": len(data),
        "duration": round(duration, 3),
        "sample_rate": round(sample_rate, 2),
        "success": success,
        "success_known": success_known,
        "quality_score": round(overall, 1),
        "grade": "A" if overall >= 90 else "B" if overall >= 78 else "C" if overall >= 65 else "D",
        "scores": scores,
        "metrics": {
            "missing_rate": round(missing_rate * 100, 3),
            "timestamp_gaps": int(len(gap_indices)),
            "time_jitter": round(jitter * 100, 2),
            "sync_offset_ms": round(sync_offset * 1000, 1),
            "sync_confidence": round(sync_confidence, 3),
            "joint_jumps": int(len(jump_rows)),
            "stuck_segments": len(stuck_segments),
            "force_spikes": int(len(force_spikes)),
            "frame_drops": int(len(frame_drop_rows)),
        },
        "issues": [issue.to_dict() for issue in issues],
        "events": events,
        "root_causes": root_causes,
    }


def dataset_overview(
    frame: pd.DataFrame,
    profile: str | AnalysisProfile | None = None,
) -> dict[str, Any]:
    active_profile = resolve_profile(profile)
    data = validate_dataframe(frame)
    episodes: list[dict[str, Any]] = []
    issue_aggregates: dict[str, dict[str, Any]] = {}
    dimension_totals = {key: 0.0 for key in ("completeness", "temporal", "motion", "sync", "safety")}
    for episode_id, episode in data.groupby("episode_id", sort=False):
        analysis = analyze_episode(episode, str(episode_id), active_profile)
        issue_codes = [issue["code"] for issue in analysis["issues"]]
        for key, value in analysis["scores"].items():
            dimension_totals[key] += float(value)
        for issue in analysis["issues"]:
            aggregate = issue_aggregates.setdefault(issue["code"], {
                "code": issue["code"],
                "title": issue["title"],
                "category": issue["category"],
                "severity": issue["severity"],
                "episode_count": 0,
                "sample_count": 0,
            })
            aggregate["episode_count"] += 1
            aggregate["sample_count"] += int(issue.get("count", 1))
            if issue["severity"] == "critical":
                aggregate["severity"] = "critical"
        episodes.append({
            "episode_id": analysis["episode_id"],
            "quality_score": analysis["quality_score"],
            "grade": analysis["grade"],
            "success": analysis["success"],
            "success_known": analysis["success_known"],
            "duration": analysis["duration"],
            "issue_count": len(analysis["issues"]),
            "critical_count": sum(issue["severity"] == "critical" for issue in analysis["issues"]),
            "primary_cause": analysis["root_causes"][0]["label"] if analysis["root_causes"] else "未发现显著异常",
            "issue_codes": issue_codes,
            "scores": analysis["scores"],
            "missing_rate": analysis["metrics"]["missing_rate"],
            "primary_issue_code": issue_codes[0] if issue_codes else None,
        })
    scores = [episode["quality_score"] for episode in episodes]
    labeled = [episode for episode in episodes if episode["success_known"]]
    grade_distribution = {grade: sum(item["grade"] == grade for item in episodes) for grade in ("A", "B", "C", "D")}
    score_histogram = [
        {"label": "90-100", "minimum": 90, "maximum": 100, "count": sum(score >= 90 for score in scores)},
        {"label": "78-89", "minimum": 78, "maximum": 89.999, "count": sum(78 <= score < 90 for score in scores)},
        {"label": "65-77", "minimum": 65, "maximum": 77.999, "count": sum(65 <= score < 78 for score in scores)},
        {"label": "0-64", "minimum": 0, "maximum": 64.999, "count": sum(score < 65 for score in scores)},
    ]
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    issue_code_counts = sorted(
        issue_aggregates.values(),
        key=lambda item: (severity_order.get(item["severity"], 3), -item["episode_count"], item["code"]),
    )
    worst_episodes = sorted(episodes, key=lambda item: (item["quality_score"], -item["critical_count"], item["episode_id"]))[:10]
    episode_count = len(episodes)
    return {
        "episode_count": episode_count,
        "analysis_profile": active_profile.to_dict(),
        "row_count": len(data),
        "column_count": len(data.columns),
        "average_score": round(float(np.mean(scores)), 1) if scores else 0.0,
        "success_rate": round(sum(episode["success"] for episode in labeled) / len(labeled) * 100, 1) if labeled else None,
        "success_labeled_count": len(labeled),
        "critical_episodes": sum(episode["critical_count"] > 0 for episode in episodes),
        "training_ready_episodes": sum(episode["critical_count"] == 0 and episode["quality_score"] >= 78 for episode in episodes),
        "grade_distribution": grade_distribution,
        "score_histogram": score_histogram,
        "issue_code_counts": issue_code_counts,
        "average_dimension_scores": {
            key: round(value / episode_count, 1) if episode_count else 0.0
            for key, value in dimension_totals.items()
        },
        "worst_episodes": worst_episodes,
        "episodes": episodes,
        "columns": list(data.columns),
    }


def timeseries_payload(frame: pd.DataFrame, episode_id: str, max_points: int = 800) -> dict[str, Any]:
    data = validate_dataframe(frame)
    data = data[data["episode_id"] == str(episode_id)].reset_index(drop=True)
    if data.empty:
        raise ValueError(f"找不到 episode: {episode_id}")
    step = max(1, int(np.ceil(len(data) / max_points)))
    sample_indices = np.arange(0, len(data), step)
    sampled = data.iloc[sample_indices].copy()
    preferred = ["ee_x", "ee_y", "ee_z", "camera_motion", "force_z", "gripper", "object_distance", "reward"]
    joint_columns = [column for column in data.columns if column.startswith("joint_")][:6]
    state_columns = [column for column in data.columns if column.startswith("state_")][:6]
    action_columns = [column for column in data.columns if column.startswith("action_")][:6]
    timestamps = data["timestamp"].to_numpy(dtype=float)
    signals: dict[str, list[float | None]] = {}
    if _has_robot_motion_signal(data):
        speed = _motion_speed(data, timestamps)
        signals["ee_speed"] = [round(float(value), 5) for value in speed[sample_indices]]
    elif state_columns:
        states = data[state_columns].apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both").to_numpy()
        dt = np.diff(timestamps, prepend=timestamps[0])
        median_dt = np.median(dt[dt > 0]) if np.any(dt > 0) else 1.0
        dt[dt <= 0] = median_dt
        state_speed = np.linalg.norm(np.diff(states, axis=0, prepend=states[[0]]) / dt[:, None], axis=1)
        signals["state_speed"] = [round(float(value), 5) for value in state_speed[sample_indices]]
    for column in preferred + joint_columns + state_columns + action_columns:
        if column not in sampled.columns:
            continue
        values = pd.to_numeric(sampled[column], errors="coerce")
        signals[column] = [None if pd.isna(value) else round(float(value), 5) for value in values]
    phases = sampled["phase"].astype(str).tolist() if "phase" in sampled.columns else []
    return {
        "timestamp": [round(float(value), 4) for value in sampled["timestamp"]],
        "signals": signals,
        "phase": phases,
        "downsample_step": step,
    }
