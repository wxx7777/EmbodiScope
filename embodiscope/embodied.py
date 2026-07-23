"""Embodied-policy readiness checks for robot learning datasets.

The checks are intentionally model-free. They answer whether an episode preserves
the observation -> action -> state transition -> contact -> task outcome contract
used by common robot-learning dataset formats, rather than claiming policy quality.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .analysis import _motion_speed, validate_dataframe


DIMENSION_NAMES = {
    "observability": "观测覆盖",
    "controllability": "动作-状态响应",
    "temporal_grounding": "时序与阶段",
    "contact_grounding": "接触语义",
    "behavior_diversity": "行为多样性",
}


def _numeric(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    if not columns:
        return np.empty((len(frame), 0), dtype=float)
    return frame[columns].apply(pd.to_numeric, errors="coerce").interpolate(
        limit_direction="both"
    ).fillna(0.0).to_numpy(dtype=float)


def _finite_corr(left: np.ndarray, right: np.ndarray) -> float:
    valid = np.isfinite(left) & np.isfinite(right)
    if valid.sum() < 8:
        return 0.0
    left, right = left[valid], right[valid]
    if np.std(left) < 1e-9 or np.std(right) < 1e-9:
        return 0.0
    return float(np.clip(np.corrcoef(left, right)[0, 1], -1.0, 1.0))


def _response_alignment(action: np.ndarray, motion: np.ndarray, max_lag: int = 12) -> tuple[float, int]:
    if len(action) < 16 or len(motion) < 16:
        return 0.0, 0
    candidates: list[tuple[float, int]] = []
    for lag in range(max(0, min(max_lag, len(action) // 5)) + 1):
        current = _finite_corr(action[:-lag] if lag else action, motion[lag:] if lag else motion)
        candidates.append((current, lag))
    return max(candidates, key=lambda item: item[0], default=(0.0, 0))


def _phase_summary(data: pd.DataFrame) -> tuple[list[str], int, float]:
    if "phase" not in data.columns:
        return [], 0, 0.0
    phases = data["phase"].astype(str).replace({"nan": "unknown"}).tolist()
    unique = list(dict.fromkeys(phases))
    transitions = sum(left != right for left, right in zip(phases, phases[1:]))
    coverage = min(1.0, len(unique) / 4.0) * 0.6 + min(1.0, transitions / 4.0) * 0.4
    return unique, transitions, float(coverage)


def _task_outcome(data: pd.DataFrame) -> bool:
    if "success" not in data.columns:
        return False
    value = str(data["success"].iloc[-1]).strip().lower()
    return value in {"1", "true", "yes", "success", "成功"}


def _episode_result(frame: pd.DataFrame, episode_id: str) -> dict[str, Any]:
    data = validate_dataframe(frame)
    data = data[data["episode_id"] == str(episode_id)].reset_index(drop=True)
    if data.empty:
        raise ValueError(f"找不到 episode: {episode_id}")

    timestamps = data["timestamp"].to_numpy(dtype=float)
    dt = np.diff(timestamps)
    positive_dt = dt[dt > 0]
    median_dt = float(np.median(positive_dt)) if positive_dt.size else 0.02
    sample_rate = 1.0 / median_dt if median_dt > 0 else 0.0
    joint_columns = [column for column in data.columns if column.startswith("joint_")]
    ee_columns = [column for column in ("ee_x", "ee_y", "ee_z") if column in data.columns]
    state_columns = joint_columns or ee_columns
    action_columns = [column for column in data.columns if column.startswith("action_")]

    state_values = _numeric(data, state_columns)
    if state_values.shape[1]:
        safe_dt = np.diff(timestamps, prepend=timestamps[0])
        safe_dt[safe_dt <= 0] = median_dt
        state_velocity = np.linalg.norm(
            np.diff(state_values, axis=0, prepend=state_values[[0]]) / safe_dt[:, None], axis=1
        )
    else:
        state_velocity = np.zeros(len(data), dtype=float)
    if not state_columns:
        state_velocity = _motion_speed(data, timestamps)

    if action_columns:
        action_values = _numeric(data, action_columns)
        action_norm = np.linalg.norm(action_values, axis=1)
        action_source = "explicit"
        action_label = "显式 action 字段"
        action_out_of_bounds = float(np.mean(np.any(np.abs(action_values) > 1.05, axis=1)))
    elif state_columns:
        action_norm = state_velocity.copy()
        action_source = "proxy"
        action_label = "状态差分代理"
        action_out_of_bounds = None
    else:
        action_norm = np.zeros(len(data), dtype=float)
        action_source = "missing"
        action_label = "缺少状态与动作"
        action_out_of_bounds = None

    if action_source == "explicit":
        alignment, response_lag = _response_alignment(action_norm, state_velocity)
        response_lag_ms: float | None = response_lag * median_dt * 1000.0
        controllability = 50.0 + max(0.0, alignment) * 42.0
        controllability -= min(20.0, action_out_of_bounds * 200.0)
    elif action_source == "proxy":
        alignment = None
        response_lag_ms = None
        controllability = 45.0
    else:
        alignment = None
        response_lag_ms = None
        controllability = 15.0
    controllability = float(np.clip(controllability, 0.0, 100.0))

    has_visual = any(column in data.columns for column in ("camera_motion", "frame_valid"))
    has_contact = any(column in data.columns for column in ("force_z", "gripper", "object_distance"))
    has_outcome = "success" in data.columns
    phase_names, phase_transitions, phase_coverage = _phase_summary(data)
    monotonic = bool(np.all(dt > 0)) if len(dt) else True
    jitter = float(np.std(positive_dt) / (np.mean(positive_dt) + 1e-9)) if positive_dt.size else 0.0
    temporal_grounding = 100.0 * (
        0.55 * (1.0 if monotonic else 0.0)
        + 0.25 * max(0.0, 1.0 - min(1.0, jitter / 0.25))
        + 0.20 * phase_coverage
    )

    contact_ratio = 0.0
    contact_quality = 20.0
    contact_evidence: list[str] = []
    if {"gripper", "object_distance"}.issubset(data.columns):
        gripper = pd.to_numeric(data["gripper"], errors="coerce").fillna(1.0).to_numpy(dtype=float)
        distance = pd.to_numeric(data["object_distance"], errors="coerce").fillna(1.0).to_numpy(dtype=float)
        contact_mask = (gripper < 0.35) & (distance < 0.08)
        contact_ratio = float(np.mean(contact_mask))
        contact_quality += 45.0 if contact_mask.any() else 0.0
        contact_evidence.append("夹爪-目标距离")
    if "force_z" in data.columns:
        force = pd.to_numeric(data["force_z"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        force_activity = float(np.mean(np.abs(force) > max(2.0, np.percentile(np.abs(force), 65))))
        contact_quality += min(35.0, force_activity * 120.0)
        contact_evidence.append("接触力")
    contact_quality = float(np.clip(contact_quality, 0.0, 100.0))

    action_variation = float(np.std(action_norm) / (np.mean(np.abs(action_norm)) + 1e-9)) if len(action_norm) else 0.0
    behavior_diversity = float(np.clip(35.0 + min(1.0, action_variation) * 65.0, 0.0, 100.0))
    observability = 0.0
    observability += 28.0 if state_columns else 0.0
    observability += 22.0 if action_columns else 8.0 if state_columns else 0.0
    observability += 16.0 if has_visual else 0.0
    observability += 18.0 if has_contact else 0.0
    observability += 16.0 if has_outcome else 0.0
    observability = float(np.clip(observability, 0.0, 100.0))

    dimensions = {
        "observability": round(observability, 1),
        "controllability": round(controllability, 1),
        "temporal_grounding": round(temporal_grounding, 1),
        "contact_grounding": round(contact_quality, 1),
        "behavior_diversity": round(behavior_diversity, 1),
    }
    score = (
        dimensions["observability"] * 0.22
        + dimensions["controllability"] * 0.25
        + dimensions["temporal_grounding"] * 0.18
        + dimensions["contact_grounding"] * 0.20
        + dimensions["behavior_diversity"] * 0.15
    )

    blockers: list[dict[str, str]] = []
    recommendations: list[str] = []
    if action_source == "missing":
        blockers.append({"code": "ACTION_MISSING", "title": "缺少动作或可推断状态", "detail": "无法验证策略输出对环境状态的影响。"})
        recommendations.append("按 RLDS/robomimic 约定记录每一步 action，并与当前 observation 对齐。")
    elif action_source == "proxy":
        blockers.append({"code": "ACTION_PROXY", "title": "当前使用状态差分代理动作", "detail": "结果可用于筛查，但不能证明真实控制闭环。"})
        recommendations.append("导出控制器实际 action；状态差分只能作为回退代理，不能替代控制指令。")
    if not has_outcome:
        blockers.append({"code": "OUTCOME_MISSING", "title": "缺少任务结果标签", "detail": "无法把轨迹质量与任务成功建立联系。"})
        recommendations.append("补充 success、reward 或 is_terminal/is_last，区分完成、失败和截断轨迹。")
    if not phase_names:
        blockers.append({"code": "PHASE_MISSING", "title": "缺少任务阶段", "detail": "无法检查 approach、grasp、transport、place 等行为结构。"})
        recommendations.append("保留阶段或事件标签，支持按任务语义切分训练片段。")
    if not contact_evidence:
        blockers.append({"code": "CONTACT_MISSING", "title": "缺少接触语义", "detail": "抓取、碰撞与释放无法与状态变化对应。"})
        recommendations.append("至少同步记录 gripper、object distance 或 wrench/force 之一。")
    if not monotonic:
        blockers.append({"code": "TIME_INVALID", "title": "时间轴不单调", "detail": "动作与状态转移的先后关系不可信。"})
        recommendations.append("修复采集时钟和 episode 内排序，保留原始时间戳，不要仅靠重排掩盖问题。")
    if action_out_of_bounds is not None and action_out_of_bounds > 0.01:
        recommendations.append("检查 action 是否按控制器约定归一化到 [-1, 1]，避免策略训练出现尺度漂移。")
    if not recommendations:
        recommendations.append("当前轨迹满足基本闭环契约，可进入策略训练前的 train/validation 切分与仿真复核。")

    hard_blocker = any(item["code"] in {"ACTION_MISSING", "OUTCOME_MISSING", "TIME_INVALID"} for item in blockers)
    if hard_blocker or score < 55:
        status = "blocked"
    elif score < 78 or blockers:
        status = "review"
    else:
        status = "ready"
    return {
        "episode_id": str(episode_id),
        "rows": int(len(data)),
        "duration": round(max(0.0, float(timestamps[-1] - timestamps[0])), 3),
        "score": round(float(score), 1),
        "status": status,
        "action_source": action_source,
        "action_label": action_label,
        "dimensions": dimensions,
        "metrics": {
            "response_lag_ms": None if response_lag_ms is None else round(float(response_lag_ms), 1),
            "action_state_correlation": None if alignment is None else round(float(alignment), 3),
            "action_out_of_bounds_rate": None if action_out_of_bounds is None else round(action_out_of_bounds * 100.0, 2),
            "sample_rate_hz": round(sample_rate, 2),
            "time_jitter": round(jitter * 100.0, 2),
            "phase_count": len(phase_names),
            "phase_transitions": int(phase_transitions),
            "contact_ratio": round(contact_ratio * 100.0, 2),
            "state_channels": len(state_columns),
            "action_channels": len(action_columns),
        },
        "phases": phase_names,
        "blockers": blockers,
        "recommendations": recommendations,
        "task_success": _task_outcome(data) if has_outcome else None,
    }


def embodied_overview(frame: pd.DataFrame) -> dict[str, Any]:
    data = validate_dataframe(frame)
    episodes = [_episode_result(data, str(episode_id)) for episode_id in data["episode_id"].drop_duplicates()]
    statuses = {status: sum(item["status"] == status for item in episodes) for status in ("ready", "review", "blocked")}
    dimension_averages = {
        key: round(float(np.mean([item["dimensions"][key] for item in episodes])), 1) if episodes else 0.0
        for key in DIMENSION_NAMES
    }
    blocker_counts: dict[str, dict[str, Any]] = {}
    for episode in episodes:
        for blocker in episode["blockers"]:
            entry = blocker_counts.setdefault(blocker["code"], {**blocker, "episode_count": 0})
            entry["episode_count"] += 1
    top_blockers = sorted(blocker_counts.values(), key=lambda item: (-item["episode_count"], item["code"]))
    ready_rate = statuses["ready"] / len(episodes) * 100.0 if episodes else 0.0
    score = float(np.mean([item["score"] for item in episodes])) if episodes else 0.0
    return {
        "dataset": {
            "episode_count": len(episodes),
            "row_count": int(len(data)),
            "column_count": int(len(data.columns)),
            "columns": list(data.columns),
        },
        "score": round(score, 1),
        "status": "ready" if statuses["blocked"] == 0 and statuses["review"] == 0 and score >= 78 else "review" if score >= 55 else "blocked",
        "ready_rate": round(ready_rate, 1),
        "status_counts": statuses,
        "dimension_averages": dimension_averages,
        "dimension_names": DIMENSION_NAMES,
        "top_blockers": top_blockers,
        "episodes": episodes,
        "protocol": {
            "contract": ["observation", "action", "next_state", "contact", "task_outcome"],
            "sources": [
                {"name": "LeRobot", "url": "https://github.com/huggingface/lerobot", "idea": "标准化 observation/action 与视频、Episode 元数据"},
                {"name": "robomimic", "url": "https://github.com/ARISE-Initiative/robomimic", "idea": "trajectory 的 states/actions/rewards/dones/obs 结构"},
                {"name": "RLDS", "url": "https://github.com/google-research/rlds", "idea": "Episode/Step、终止与截断语义"},
                {"name": "ManiSkill", "url": "https://github.com/haosulab/ManiSkill", "idea": "仿真状态、动作、接触与任务回放闭环"},
            ],
            "limitations": [
                "这是数据可训练性筛查，不等价于策略成功率。",
                "缺少显式 action 时只使用状态差分代理，并降低状态为 review/blocked。",
                "接触分数依赖数据中存在 gripper、object_distance 或 force/wrench 信号。",
            ],
        },
    }
