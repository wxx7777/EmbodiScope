"""Task-and-motion reasoning grounded in embodied trajectory signals.

The module translates continuous robot observations into symbolic predicates,
checks a pick-and-place operator graph, and proposes recovery operators from the
first violated invariant.  It is deliberately diagnostic: recovery plans are
explainable suggestions, not claims that a policy was executed successfully.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from .analysis import _motion_speed, analyze_episode, validate_dataframe
from .profiles import AnalysisProfile, resolve_profile


PREDICATE_LABELS = {
    "observation_fresh": "观测新鲜且同步",
    "collision_free": "运动路径无异常碰撞",
    "target_localized": "目标已定位",
    "gripper_open": "夹爪处于张开状态",
    "near_object": "末端已接近目标",
    "gripper_closed": "夹爪完成闭合",
    "object_attached": "目标保持在夹爪中",
    "motion_progress": "运动持续产生有效进展",
    "at_goal": "目标到达放置区域",
    "object_released": "目标已释放",
    "task_complete": "任务结果确认成功",
    "control_smooth": "控制与状态变化连续",
}


OPERATORS = [
    {
        "key": "approach",
        "name": "ApproachObject",
        "label": "接近目标",
        "preconditions": ["observation_fresh", "collision_free"],
        "effects": ["target_localized"],
    },
    {
        "key": "reach",
        "name": "ReachPregrasp",
        "label": "到达预抓取位姿",
        "preconditions": ["target_localized", "gripper_open", "collision_free"],
        "effects": ["near_object"],
    },
    {
        "key": "grasp",
        "name": "SecureGrasp",
        "label": "建立并验证抓取",
        "preconditions": ["near_object", "gripper_open", "observation_fresh"],
        "effects": ["gripper_closed", "object_attached"],
    },
    {
        "key": "transport",
        "name": "TransportObject",
        "label": "携物运动",
        "preconditions": ["object_attached", "collision_free", "motion_progress"],
        "effects": ["at_goal"],
    },
    {
        "key": "place",
        "name": "ReleaseAtGoal",
        "label": "放置并释放",
        "preconditions": ["at_goal", "object_attached", "collision_free"],
        "effects": ["object_released", "task_complete"],
    },
]


ISSUE_PREDICATES = {
    "SENSOR_DESYNC": "observation_fresh",
    "FRAME_DROP": "observation_fresh",
    "MISSING_VALUES": "observation_fresh",
    "TIMESTAMP_GAP": "observation_fresh",
    "FORCE_SPIKE": "collision_free",
    "WORKSPACE_VIOLATION": "collision_free",
    "JOINT_JUMP": "control_smooth",
    "ROBOT_STUCK": "motion_progress",
    "GRASP_SLIP": "object_attached",
    "GRIPPER_RESPONSE_FAILURE": "object_attached",
}


DEFAULT_ISSUE_PHASE = {
    "SENSOR_DESYNC": "reach",
    "FRAME_DROP": "reach",
    "MISSING_VALUES": "approach",
    "TIMESTAMP_GAP": "grasp",
    "FORCE_SPIKE": "transport",
    "WORKSPACE_VIOLATION": "transport",
    "JOINT_JUMP": "transport",
    "ROBOT_STUCK": "transport",
    "GRASP_SLIP": "transport",
    "GRIPPER_RESPONSE_FAILURE": "grasp",
}


RECOVERY_LIBRARY = {
    "observation_fresh": [
        ("safety", "HoldPosition", "保持当前位置并冻结新动作", "停止使用过期观测继续控制"),
        ("perception", "ResyncObservation", "重新同步相机、状态与控制时间轴", "恢复可比较的 observation-action 时间关系"),
        ("verification", "ValidateFreshState", "重新采样并验证状态新鲜度", "确认时间戳、帧有效性和状态连续性"),
        ("planning", "ResumeInterruptedSkill", "从中断技能重新规划", "只在观测完整后恢复任务"),
    ],
    "collision_free": [
        ("safety", "EmergencyStop", "停止当前运动并限制接触力", "先阻断持续碰撞和二次伤害"),
        ("control", "RetreatToSafePose", "沿最近安全轨迹撤退", "恢复无碰撞状态并释放接触约束"),
        ("perception", "ReobserveObstacle", "重新观测障碍物与目标位姿", "更新碰撞几何和场景状态"),
        ("planning", "ReplanCollisionFreePath", "重新规划无碰撞路径", "从失败技能前的安全状态继续"),
        ("verification", "ForceGuardedRetry", "带力阈值监控重试", "验证新路径满足安全不变量"),
    ],
    "object_attached": [
        ("safety", "StopTransport", "立即停止携物运动", "防止滑落目标造成碰撞"),
        ("control", "OpenGripper", "张开夹爪并回到预抓取位姿", "解除失败抓取状态"),
        ("perception", "RelocalizeObject", "重新定位目标与可抓取区域", "更新目标位姿和抓取候选"),
        ("planning", "ReplanPregrasp", "重新规划预抓取位姿", "提高接近方向和抓取裕度"),
        ("verification", "CloseAndVerifyAttachment", "闭合夹爪并验证附着", "只有力觉和距离证据一致时才继续运输"),
    ],
    "motion_progress": [
        ("safety", "StopController", "停止卡滞控制器", "避免积分累积和突然恢复"),
        ("control", "RetreatToLastProgressState", "回退到最近有进展的状态", "移出局部阻塞或奇异位形"),
        ("planning", "ReplanMotion", "使用新约束重新规划运动", "绕开不可达或受阻路径"),
        ("verification", "ProgressWatchdog", "带进展监控恢复执行", "持续检查位姿变化与动作响应"),
    ],
    "control_smooth": [
        ("safety", "StopController", "停止异常控制输出", "阻断突跳继续传递到执行器"),
        ("control", "ResetControlState", "重置控制器内部状态与动作尺度", "排除积分、归一化和周期错误"),
        ("planning", "RetimingTrajectory", "重新进行速度与加速度时间参数化", "恢复满足动力学约束的轨迹"),
        ("verification", "DryRunWithLimits", "在速度限制下验证轨迹", "通过后才允许恢复真机执行"),
    ],
    "near_object": [
        ("control", "OpenGripper", "保持夹爪张开", "避免在未到位时提前闭合"),
        ("perception", "RelocalizeObject", "重新定位目标", "更新预抓取误差"),
        ("planning", "ReplanReach", "重新规划到预抓取位姿", "恢复 near_object 前置条件"),
    ],
    "at_goal": [
        ("control", "HoldObject", "保持抓取并停止释放", "防止在错误位置放置"),
        ("perception", "RelocalizeGoal", "重新定位放置区域", "更新目标约束"),
        ("planning", "ReplanTransport", "重新规划剩余运输路径", "恢复 at_goal 条件"),
    ],
    "task_complete": [
        ("verification", "VerifyTaskOutcome", "核验目标状态与任务结果", "区分标签缺失和真实任务失败"),
        ("planning", "ResumeFromLastUnsatisfiedEffect", "从最后未满足效果继续规划", "避免整段任务盲目重做"),
    ],
}


CAUSAL_CHAINS = {
    "observation_fresh": ["观测时间关系失效", "控制参考不可信", "当前技能应暂停", "任务结果需要重新验证"],
    "collision_free": ["安全不变量被破坏", "当前运动必须终止", "目标到达条件未被可信满足", "任务无法安全完成"],
    "object_attached": ["抓取保持失效", "运输动作失去任务对象", "到达放置区不再代表完成", "任务结果失败"],
    "motion_progress": ["动作未产生预期状态变化", "技能效果无法建立", "后续前置条件缺失", "任务执行停滞"],
    "control_smooth": ["控制或状态出现突变", "动力学连续性被破坏", "轨迹执行不可直接恢复", "任务结果不可信"],
    "near_object": ["预抓取位姿未建立", "安全抓取前置条件缺失", "目标附着无法验证", "运输不可开始"],
    "at_goal": ["目标区域条件未满足", "释放动作被禁止", "任务完成谓词为假"],
    "task_complete": ["末端效果未确认", "任务状态仍未闭合", "需要核验或局部重规划"],
}


def _truth(value: bool | None, evidence: str, confidence: str = "high") -> dict[str, Any]:
    return {
        "state": "unknown" if value is None else "true" if value else "false",
        "evidence": evidence,
        "confidence": confidence,
    }


def _numeric(data: pd.DataFrame, column: str) -> np.ndarray | None:
    if column not in data.columns:
        return None
    values = pd.to_numeric(data[column], errors="coerce")
    if values.notna().sum() == 0:
        return None
    return values.interpolate(limit_direction="both").to_numpy(dtype=float)


def _issue_phase(issue: dict[str, Any], data: pd.DataFrame) -> str:
    start = issue.get("start_time")
    if start is not None and "phase" in data.columns:
        timestamps = data["timestamp"].to_numpy(dtype=float)
        index = int(np.argmin(np.abs(timestamps - float(start))))
        return str(data["phase"].iloc[index])
    return DEFAULT_ISSUE_PHASE.get(str(issue.get("code")), "approach")


def _assigned_issues(analysis: dict[str, Any], data: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    assigned: dict[str, list[dict[str, Any]]] = {operator["key"]: [] for operator in OPERATORS}
    for issue in analysis.get("issues", []):
        phase = _issue_phase(issue, data)
        assigned.setdefault(phase, []).append(issue)
    return assigned


def _phase_slice(data: pd.DataFrame, phase: str) -> pd.DataFrame:
    if "phase" not in data.columns:
        return data.iloc[0:0]
    return data[data["phase"].astype(str) == phase]


def _with_next_phase(data: pd.DataFrame, phase: str) -> pd.DataFrame:
    current = _phase_slice(data, phase)
    keys = [operator["key"] for operator in OPERATORS]
    if phase not in keys or keys.index(phase) == len(keys) - 1:
        return current
    following = _phase_slice(data, keys[keys.index(phase) + 1])
    head = following.head(max(4, min(12, len(following) // 8)))
    return pd.concat([current, head], ignore_index=True)


def _predicate(
    key: str,
    data: pd.DataFrame,
    segment: pd.DataFrame,
    phase: str,
    assigned: dict[str, list[dict[str, Any]]],
    profile: AnalysisProfile,
    success: bool | None,
) -> dict[str, Any]:
    issues = assigned.get(phase, [])
    issue_codes = {str(issue.get("code")) for issue in issues}
    timestamps = segment["timestamp"].to_numpy(dtype=float) if not segment.empty else np.array([])

    if key == "observation_fresh":
        blockers = {"SENSOR_DESYNC", "FRAME_DROP", "MISSING_VALUES", "TIMESTAMP_GAP"} & issue_codes
        if blockers:
            if blockers == {"MISSING_VALUES"}:
                return _truth(None, "存在局部缺失值，当前阶段观测完整性只能部分确认")
            return _truth(False, f"诊断事件: {', '.join(sorted(blockers))}")
        if "frame_valid" in segment.columns:
            valid = pd.to_numeric(segment["frame_valid"], errors="coerce").fillna(0.0)
            ratio = float(valid.mean()) if len(valid) else 0.0
            return _truth(ratio >= 0.98, f"有效视觉帧 {ratio * 100:.1f}%")
        if len(timestamps) > 1:
            return _truth(bool(np.all(np.diff(timestamps) > 0)), "episode 时间戳连续单调", "medium")
        return _truth(None, "缺少可验证的帧或时间序列")

    if key == "collision_free":
        if "FORCE_SPIKE" in issue_codes or "WORKSPACE_VIOLATION" in issue_codes:
            return _truth(False, "安全诊断检测到碰撞力或工作空间越界")
        force = _numeric(segment, "force_z")
        if force is None:
            return _truth(None, "缺少力觉或碰撞信号")
        peak = float(np.max(np.abs(force))) if len(force) else 0.0
        return _truth(peak <= profile.force_floor, f"阶段峰值接触力 {peak:.1f} N，门限 {profile.force_floor:.1f} N")

    if key == "target_localized":
        phases = data["phase"].astype(str).tolist() if "phase" in data.columns else []
        observed = any(item in phases for item in ("reach", "grasp", "transport", "place"))
        if not observed:
            return _truth(None, "缺少目标位姿或阶段转换证据")
        source = "视觉信号与阶段转换" if "camera_motion" in data.columns else "阶段转换"
        return _truth(True, f"{source}表明规划器进入 reach", "medium")

    if key == "gripper_open":
        values = _numeric(segment, "gripper")
        if values is None:
            return _truth(None, "缺少夹爪开度")
        window = values[: max(3, len(values) // 4)]
        ratio = float(np.mean(window > 0.65))
        return _truth(ratio >= 0.6, f"阶段起始张开比例 {ratio * 100:.1f}%")

    if key == "near_object":
        values = _numeric(_with_next_phase(data, phase), "object_distance")
        if values is None:
            return _truth(None, "缺少末端-目标距离")
        closest = float(np.min(values)) if len(values) else float("inf")
        return _truth(closest < 0.08, f"最小目标距离 {closest:.3f} m")

    if key == "gripper_closed":
        values = _numeric(segment, "gripper")
        if values is None:
            return _truth(None, "缺少夹爪开度")
        window = values[-max(3, len(values) // 4):]
        ratio = float(np.mean(window < 0.35))
        return _truth(ratio >= 0.6, f"阶段末尾闭合比例 {ratio * 100:.1f}%")

    if key == "object_attached":
        if "GRASP_SLIP" in issue_codes:
            return _truth(False, "抓取滑脱事件表明目标未被持续保持")
        distance = _numeric(segment, "object_distance")
        gripper = _numeric(segment, "gripper")
        if distance is None or gripper is None:
            return _truth(None, "需要夹爪开度与目标距离联合验证")
        window = max(3, len(segment) // 4)
        if phase == "place":
            window = max(3, min(8, len(segment) // 12))
            distance, gripper = distance[:window], gripper[:window]
        else:
            distance, gripper = distance[-window:], gripper[-window:]
        attached = (distance < profile.slip_distance_threshold) & (gripper < 0.35)
        ratio = float(np.mean(attached)) if len(attached) else 0.0
        return _truth(ratio >= 0.6, f"夹爪闭合且目标距离小于 {profile.slip_distance_threshold:.2f} m 的比例 {ratio * 100:.1f}%")

    if key == "motion_progress":
        if "ROBOT_STUCK" in issue_codes:
            return _truth(False, "卡滞诊断检测到持续无有效运动")
        if segment.empty:
            return _truth(None, "缺少运动片段")
        speed = _motion_speed(segment, segment["timestamp"].to_numpy(dtype=float))
        active = float(np.mean(speed > profile.stuck_speed_threshold)) if len(speed) else 0.0
        return _truth(active >= 0.2, f"有效运动采样比例 {active * 100:.1f}%")

    if key == "at_goal":
        phases = data["phase"].astype(str).tolist() if "phase" in data.columns else []
        if "place" not in phases:
            return _truth(False, "未观察到 place 阶段")
        place = _phase_slice(data, "place")
        attached = _predicate("object_attached", data, place, "place", assigned, profile, success)
        if attached["state"] == "false":
            return _truth(False, "进入 place 时目标已经不在夹爪中")
        return _truth(True, "transport→place 阶段转换提供目标到达证据", "medium")

    if key == "object_released":
        gripper = _numeric(segment, "gripper")
        distance = _numeric(segment, "object_distance")
        if gripper is None:
            return _truth(None, "缺少夹爪开度")
        window = max(3, len(gripper) // 4)
        opened = float(np.mean(gripper[-window:] > 0.65))
        separated = None if distance is None else float(np.mean(distance[-window:] > 0.08))
        evidence = f"阶段末尾夹爪张开比例 {opened * 100:.1f}%"
        if separated is not None:
            evidence += f"，目标分离比例 {separated * 100:.1f}%"
        return _truth(opened >= 0.6 and (separated is None or separated >= 0.5), evidence)

    if key == "task_complete":
        if success is None:
            return _truth(None, "缺少 success/reward/terminal 结果")
        return _truth(success, "episode 末端 success 标签")

    if key == "control_smooth":
        if "JOINT_JUMP" in issue_codes:
            return _truth(False, "关节突跳诊断破坏控制连续性")
        return _truth(True, "未检测到控制或状态突跳", "medium")

    return _truth(None, "未定义谓词映射")


def _trace(
    data: pd.DataFrame,
    analysis: dict[str, Any],
    profile: AnalysisProfile,
    success: bool | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    assigned = _assigned_issues(analysis, data)
    trace: list[dict[str, Any]] = []
    for operator in OPERATORS:
        phase = operator["key"]
        segment = _phase_slice(data, phase)
        if segment.empty:
            trace.append({
                **operator,
                "status": "not_observed",
                "start_time": None,
                "end_time": None,
                "rows": 0,
                "preconditions": [],
                "effects": [],
                "issues": [],
            })
            continue
        preconditions = []
        effects = []
        for key in operator["preconditions"]:
            preconditions.append({"key": key, "label": PREDICATE_LABELS[key], **_predicate(key, data, segment, phase, assigned, profile, success)})
        for key in operator["effects"]:
            effects.append({"key": key, "label": PREDICATE_LABELS[key], **_predicate(key, data, segment, phase, assigned, profile, success)})
        phase_issues = assigned.get(phase, [])
        states = [item["state"] for item in preconditions + effects]
        has_critical = any(issue.get("severity") == "critical" for issue in phase_issues)
        has_warning = bool(phase_issues)
        if "false" in states or has_critical:
            status = "failed"
        elif "unknown" in states or has_warning:
            status = "degraded"
        else:
            status = "completed"
        trace.append({
            **operator,
            "status": status,
            "start_time": round(float(segment["timestamp"].iloc[0]), 3),
            "end_time": round(float(segment["timestamp"].iloc[-1]), 3),
            "rows": int(len(segment)),
            "preconditions": preconditions,
            "effects": effects,
            "issues": [
                {
                    "code": issue.get("code"),
                    "title": issue.get("title"),
                    "severity": issue.get("severity"),
                    "start_time": issue.get("start_time"),
                }
                for issue in phase_issues
            ],
        })
    return trace, assigned


def _first_violation(
    trace: list[dict[str, Any]],
    analysis: dict[str, Any],
    data: pd.DataFrame,
) -> dict[str, Any] | None:
    timed = [issue for issue in analysis.get("issues", []) if issue.get("start_time") is not None]
    candidates = sorted(timed, key=lambda issue: float(issue["start_time"]))
    if not candidates and analysis.get("issues"):
        candidates = sorted(
            analysis["issues"],
            key=lambda issue: (issue.get("severity") != "critical", str(issue.get("code"))),
        )
    if candidates:
        issue = candidates[0]
        predicate = ISSUE_PREDICATES.get(str(issue.get("code")), "task_complete")
        return {
            "predicate": predicate,
            "predicate_label": PREDICATE_LABELS[predicate],
            "skill": _issue_phase(issue, data),
            "time": issue.get("start_time"),
            "issue_code": issue.get("code"),
            "title": issue.get("title"),
            "evidence": issue.get("description"),
            "severity": issue.get("severity"),
        }
    for step in trace:
        for predicate in step["preconditions"] + step["effects"]:
            if predicate["state"] == "false":
                return {
                    "predicate": predicate["key"],
                    "predicate_label": predicate["label"],
                    "skill": step["key"],
                    "time": step["start_time"],
                    "issue_code": "UNSATISFIED_PREDICATE",
                    "title": f"{predicate['label']}未满足",
                    "evidence": predicate["evidence"],
                    "severity": "critical",
                }
    return None


def _recovery_plan(violation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not violation:
        return []
    predicate = str(violation["predicate"])
    library = RECOVERY_LIBRARY.get(predicate, RECOVERY_LIBRARY["task_complete"])
    return [
        {
            "index": index,
            "kind": kind,
            "operator": operator,
            "label": label,
            "rationale": rationale,
            "restores": predicate if index == len(library) else None,
        }
        for index, (kind, operator, label, rationale) in enumerate(library, start=1)
    ]


def _episode_reasoning(
    frame: pd.DataFrame,
    episode_id: str,
    profile: AnalysisProfile,
) -> dict[str, Any]:
    data = validate_dataframe(frame)
    data = data[data["episode_id"] == str(episode_id)].reset_index(drop=True)
    analysis = analyze_episode(data, str(episode_id), profile)
    success = analysis.get("success") if analysis.get("success_known", True) else None
    trace, _ = _trace(data, analysis, profile, success)
    violation = _first_violation(trace, analysis, data)
    recovery = _recovery_plan(violation)

    observed = [step for step in trace if step["status"] != "not_observed"]
    predicates = [item for step in observed for item in step["preconditions"] + step["effects"]]
    known = sum(item["state"] != "unknown" for item in predicates)
    grounding_coverage = known / len(predicates) * 100.0 if predicates else 0.0
    weighted = {"completed": 1.0, "degraded": 0.65, "failed": 0.2, "not_observed": 0.0}
    plan_progress = sum(weighted[step["status"]] for step in trace) / len(trace) * 100.0
    first_failed_index = next((index for index, step in enumerate(trace) if step["status"] == "failed"), None)

    if len(observed) < 3 or grounding_coverage < 35:
        status = "blocked"
    elif success is True and not violation:
        status = "verified"
    elif success is True:
        status = "degraded"
    else:
        status = "recoverable" if recovery else "blocked"

    if success is True:
        task_progress = 100.0
    elif first_failed_index is not None:
        task_progress = first_failed_index / len(trace) * 100.0
    else:
        task_progress = len(observed) / len(trace) * 100.0

    return {
        "episode_id": str(episode_id),
        "status": status,
        "success": success,
        "rows": int(len(data)),
        "duration": round(float(data["timestamp"].iloc[-1] - data["timestamp"].iloc[0]), 3),
        "task_progress": round(task_progress, 1),
        "plan_health": round(plan_progress, 1),
        "grounding_coverage": round(grounding_coverage, 1),
        "observed_skills": len(observed),
        "completed_skills": sum(step["status"] == "completed" for step in trace),
        "trace": trace,
        "first_violation": violation,
        "causal_chain": CAUSAL_CHAINS.get(str(violation["predicate"]), []) if violation else [],
        "recovery_plan": recovery,
    }


def task_reasoning_overview(
    frame: pd.DataFrame,
    profile: str | AnalysisProfile | None = None,
) -> dict[str, Any]:
    """Return dataset-level symbolic task reasoning and recovery plans."""

    data = validate_dataframe(frame)
    active_profile = resolve_profile(profile)
    episode_ids = [str(value) for value in data["episode_id"].drop_duplicates()]
    episodes = [_episode_reasoning(data, episode_id, active_profile) for episode_id in episode_ids]
    status_counts = {
        status: sum(episode["status"] == status for episode in episodes)
        for status in ("verified", "degraded", "recoverable", "blocked")
    }
    predicate_counts = Counter(
        episode["first_violation"]["predicate"]
        for episode in episodes
        if episode["first_violation"]
    )
    failure_predicates = [
        {
            "predicate": predicate,
            "label": PREDICATE_LABELS[predicate],
            "episode_count": count,
            "episodes": [
                episode["episode_id"]
                for episode in episodes
                if episode["first_violation"] and episode["first_violation"]["predicate"] == predicate
            ],
        }
        for predicate, count in predicate_counts.most_common()
    ]
    episode_count = len(episodes)
    return {
        "dataset": {
            "episode_count": episode_count,
            "row_count": int(len(data)),
            "task_template": "pick-and-place-v1",
        },
        "summary": {
            "verified_rate": round(status_counts["verified"] / episode_count * 100.0, 1) if episode_count else 0.0,
            "recoverable_rate": round(status_counts["recoverable"] / episode_count * 100.0, 1) if episode_count else 0.0,
            "average_grounding_coverage": round(float(np.mean([episode["grounding_coverage"] for episode in episodes])), 1) if episodes else 0.0,
            "average_plan_health": round(float(np.mean([episode["plan_health"] for episode in episodes])), 1) if episodes else 0.0,
            "recovery_steps": sum(len(episode["recovery_plan"]) for episode in episodes),
        },
        "status_counts": status_counts,
        "failure_predicates": failure_predicates,
        "operators": OPERATORS,
        "predicate_labels": PREDICATE_LABELS,
        "episodes": episodes,
        "profile": active_profile.to_dict(),
        "protocol": {
            "name": "Grounded Task Graph + Recovery Operators",
            "principles": [
                "连续信号只在有物理证据时落为 true/false，证据不足保持 unknown。",
                "先定位最早违反的前置条件或安全不变量，再解释下游失败。",
                "恢复计划只恢复缺失谓词，不把规则建议冒充真实策略成功率。",
            ],
            "sources": [
                {"name": "PDDLStream", "url": "https://github.com/caelan/pddlstream", "idea": "符号规划与连续采样约束结合"},
                {"name": "MoveIt Task Constructor", "url": "https://github.com/moveit/moveit_task_constructor", "idea": "分阶段操作任务、前向与后向状态传播"},
                {"name": "BehaviorTree.CPP", "url": "https://github.com/BehaviorTree/BehaviorTree.CPP", "idea": "可恢复、可监控的机器人技能执行树"},
                {"name": "py_trees", "url": "https://github.com/splintered-reality/py_trees", "idea": "任务行为状态与运行时可视化"},
            ],
            "limitations": [
                "当前内置模板针对 pick-and-place；未知任务需要提供 phase 到 operator 的映射。",
                "at_goal 可由阶段转换弱推断，但高可信验证仍需要目标位姿或任务谓词。",
                "恢复计划是诊断输出，执行前仍需运动规划器、碰撞检查和真机安全门。",
            ],
        },
    }
