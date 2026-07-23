"""Paired failure/recovery experiments for embodied task execution."""

from __future__ import annotations

import json
import hashlib
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .simulation import (
    SCENARIOS,
    SIMULATION_EXECUTION_LOCK,
    SimulationConfig,
    run_simulation,
    runtime_status,
)


RECOVERY_PROTOCOLS: dict[str, dict[str, Any]] = {
    "collision": {
        "name": "碰撞后撤退与重规划",
        "predicate": "collision_free",
        "predicate_label": "运动路径恢复无碰撞",
        "description": "保留向下冲击故障，在接触后执行停止、撤退、重新观测和无碰撞重试。",
        "plan": [
            ("safety", "EmergencyStop", "停止当前运动", "event"),
            ("control", "RetreatToSafePose", "撤退到方块上方安全位姿", "recovery_retreat"),
            ("perception", "ReobserveObstacle", "重新观测目标与接触区域", "recovery_reobserve"),
            ("planning", "ReplanCollisionFreePath", "重新规划接近与抓取路径", "recovery_reach"),
            ("verification", "ForceGuardedRetry", "带接触力门限完成重试", "success_safe"),
        ],
    },
    "gripper-failure": {
        "name": "夹爪执行恢复与重新抓取",
        "predicate": "object_attached",
        "predicate_label": "目标重新建立物理附着",
        "description": "保留夹爪失效窗口，执行器恢复后回到预抓取状态并重新验证抓取。",
        "plan": [
            ("safety", "HoldPosition", "停止无效运输", "event"),
            ("planning", "ReplanPregrasp", "重新规划预抓取位姿", "recovery_reach"),
            ("control", "RestoreGripperActuation", "恢复夹爪动作执行", "gripper_restored"),
            ("verification", "CloseAndVerifyAttachment", "闭合并验证 object_attached", "predicate"),
            ("control", "ResumeTransport", "恢复运输到目标位姿", "success"),
        ],
    },
    "grasp-slip": {
        "name": "滑脱后重定位与重新抓取",
        "predicate": "object_attached",
        "predicate_label": "滑脱后重新保持目标",
        "description": "保留 60 N 横向扰动，检测抓取丢失后等待目标稳定，再执行重定位、重抓取与运输。",
        "plan": [
            ("safety", "StopTransport", "停止运输并张开夹爪", "event"),
            ("control", "WaitForObjectSettlement", "等待目标重新稳定", "recovery_hold"),
            ("perception", "RelocalizeObject", "重新定位滑落目标", "recovery_reobserve"),
            ("planning", "ReplanPregrasp", "重新规划预抓取路径", "recovery_reach"),
            ("verification", "CloseAndVerifyAttachment", "重新闭合并验证附着", "predicate"),
            ("control", "ResumeTransport", "恢复运输到目标位姿", "success"),
        ],
    },
}

FORCE_SAFETY_LIMIT_N = 36.0


def recovery_catalog() -> dict[str, Any]:
    return {
        "runtime": runtime_status(),
        "protocol": {
            "name": "Paired Counterfactual Recovery Evaluation",
            "variants": ["failure", "recovered"],
            "controlled_variables": ["environment", "seed", "fault", "horizon", "camera"],
            "trigger": "online physical predicate monitor",
            "metrics": [
                "task success", "predicate restoration", "recovery latency",
                "episode safety", "post-intervention safety", "post-recovery peak force",
                "path overhead", "operator completion",
            ],
            "verdicts": ["task recovery", "full-episode safety", "post-intervention safety"],
            "quality_gates": [
                "pair integrity", "failure control", "task success",
                "predicate restoration", "ordered plan completion", "force guard",
            ],
        },
        "scenarios": [
            {
                "id": scenario_id,
                "scenario_name": SCENARIOS[scenario_id]["name"],
                "recommended_steps": 140,
                **protocol,
            }
            for scenario_id, protocol in RECOVERY_PROTOCOLS.items()
        ],
        "sources": [
            {
                "name": "ManiSkill",
                "url": "https://github.com/haosulab/ManiSkill",
                "idea": "确定性环境重置、物理状态与轨迹回放",
            },
            {
                "name": "BehaviorTree.CPP",
                "url": "https://github.com/BehaviorTree/BehaviorTree.CPP",
                "idea": "失败检测、Fallback 与可恢复技能执行",
            },
            {
                "name": "MoveIt Task Constructor",
                "url": "https://github.com/moveit/moveit_task_constructor",
                "idea": "分阶段任务状态传播与局部重规划",
            },
        ],
    }


def _load_replay(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_length(replay: dict[str, Any], end_time: float | None = None) -> float:
    points = np.asarray(replay.get("tcp", []), dtype=float)
    if end_time is not None:
        timestamps = np.asarray(replay.get("timestamps", []), dtype=float)
        if len(timestamps) == len(points):
            points = points[timestamps <= end_time + 1e-9]
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def _event_index(replay: dict[str, Any], event_type: str) -> int | None:
    fps = max(1, int(replay.get("fps", 20)))
    event = next((item for item in replay.get("events", []) if item.get("type") == event_type), None)
    return None if event is None else max(0, round(float(event.get("time", 0.0)) * fps))


def _recovery_start_index(replay: dict[str, Any]) -> int | None:
    value = replay.get("recovery", {}).get("trigger_step")
    if value is not None:
        return int(value)
    return _event_index(replay, "recovery-start")


def _controlled_signature(replay: dict[str, Any], fault_type: str) -> dict[str, Any]:
    config = replay.get("config", {})
    controlled = {
        key: config.get(key, replay.get(key))
        for key in ("env_id", "scenario", "seed", "steps", "fps", "width", "height")
    }
    fault = next((
        {
            "type": item.get("type"),
            "time": round(float(item.get("time", 0.0)), 4),
            "label": item.get("label"),
        }
        for item in replay.get("events", [])
        if item.get("type") == fault_type and item.get("source") == "injection"
    ), None)
    initial = {
        key: [round(float(value), 6) for value in replay.get(key, [[]])[0]]
        if replay.get(key) else []
        for key in ("tcp", "object", "goal")
    }
    return {"controlled": controlled, "fault": fault, "initial": initial}


def _pair_integrity(scenario: str, failure_replay: dict[str, Any], recovered_replay: dict[str, Any]) -> dict[str, Any]:
    fault_types = {
        "collision": "collision-command",
        "gripper-failure": "gripper-failure",
        "grasp-slip": "grasp-slip-force",
    }
    failure_signature = _controlled_signature(failure_replay, fault_types[scenario])
    recovered_signature = _controlled_signature(recovered_replay, fault_types[scenario])
    checks = [
        {
            "key": "controlled_variables",
            "label": "环境、种子、故障预算与相机一致",
            "passed": failure_signature["controlled"] == recovered_signature["controlled"],
        },
        {
            "key": "initial_state",
            "label": "机器人、目标与物体初始状态一致",
            "passed": failure_signature["initial"] == recovered_signature["initial"],
        },
        {
            "key": "fault_signature",
            "label": "故障类型、参数与注入时刻一致",
            "passed": failure_signature["fault"] == recovered_signature["fault"] and failure_signature["fault"] is not None,
        },
    ]
    canonical = json.dumps(failure_signature, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
    }


def _first_true_time(replay: dict[str, Any], start_index: int) -> float | None:
    timestamps = replay.get("timestamps", [])
    for index in range(start_index, min(len(timestamps), len(replay.get("success_trace", [])))):
        if replay["success_trace"][index]:
            return float(timestamps[index])
    return None


def _variant_summary(replay: dict[str, Any], recovery_start: int | None = None) -> dict[str, Any]:
    force = np.asarray(replay.get("force", []), dtype=float)
    grasp = np.asarray(replay.get("is_grasped", []), dtype=bool)
    start = max(0, min(int(recovery_start or 0), len(force)))
    post_force = force[start:] if len(force) else np.array([], dtype=float)
    return {
        "success": bool(replay.get("success")),
        "rows": int(replay.get("rows", 0)),
        "duration": round(float(replay.get("duration", 0.0)), 3),
        "peak_force": round(float(np.max(force)), 3) if len(force) else 0.0,
        "post_recovery_peak_force": round(float(np.max(post_force)), 3) if len(post_force) else None,
        "path_length": round(_path_length(replay), 4),
        "grasped_frames": int(grasp.sum()),
        "final_grasped": bool(grasp[-1]) if len(grasp) else False,
        "issue_codes": [item["code"] for item in replay.get("diagnosis", {}).get("issues", [])],
    }


def _force_safety_verdict(
    replay: dict[str, Any],
    start_index: int = 0,
    *,
    scope: str,
) -> dict[str, Any]:
    force = np.asarray(replay.get("force", []), dtype=float)
    timestamps = np.asarray(replay.get("timestamps", []), dtype=float)
    start = max(0, min(int(start_index), len(force)))
    observed = force[start:]
    invalid = ~np.isfinite(observed)
    violations = invalid | (observed > FORCE_SAFETY_LIMIT_N)
    violation_indices = np.flatnonzero(violations) + start
    peak = float(np.nanmax(observed)) if len(observed) and np.any(np.isfinite(observed)) else None
    first_index = int(violation_indices[0]) if len(violation_indices) else None
    first_time = (
        float(timestamps[first_index])
        if first_index is not None and first_index < len(timestamps)
        else None
    )
    passed = bool(len(observed)) and not bool(np.any(violations))
    return {
        "passed": passed,
        "status": "safe" if passed else "unsafe",
        "scope": scope,
        "criterion": f"force <= {FORCE_SAFETY_LIMIT_N:.1f} N for every observed frame",
        "force_limit_n": FORCE_SAFETY_LIMIT_N,
        "observed_peak_force_n": None if peak is None else round(peak, 3),
        "evaluated_frames": int(len(observed)),
        "violation_frames": int(len(violation_indices)),
        "first_violation_time": None if first_time is None else round(first_time, 3),
    }


def _predicate_restored(scenario: str, replay: dict[str, Any], start_index: int) -> bool:
    if not replay.get("success"):
        return False
    restored_index = _event_index(replay, "predicate-restored")
    violated_index = _event_index(replay, "predicate-violated")
    if restored_index is None or violated_index is None or not violated_index < start_index <= restored_index:
        return False
    if scenario == "collision":
        force = np.asarray(replay.get("force", []), dtype=float)
        return bool(len(force) > restored_index and np.all(force[restored_index:restored_index + 3] <= 36.0))
    grasped = replay.get("is_grasped", [])
    return bool(restored_index < len(grasped) and grasped[restored_index])


def _plan_results(
    scenario: str,
    replay: dict[str, Any],
    predicate_restored: bool,
    post_recovery_peak: float | None,
) -> list[dict[str, Any]]:
    phases = [str(value) for value in replay.get("phases", [])]
    gripper = np.asarray(replay.get("gripper", []), dtype=float)
    command = np.asarray(replay.get("gripper_command", []), dtype=float)
    timestamps = replay.get("timestamps", [])
    start = _recovery_start_index(replay)
    cursor = int(start or 0)
    results: list[dict[str, Any]] = []

    def first_index(predicate: Callable[[int], bool], begin: int) -> int | None:
        return next((index for index in range(begin, len(timestamps)) if predicate(index)), None)

    for index, (kind, operator, label, check) in enumerate(RECOVERY_PROTOCOLS[scenario]["plan"], start=1):
        evidence = "未找到有序执行证据"
        found: int | None = None
        if check == "event":
            found = _event_index(replay, "recovery-start")
            evidence = "recovery-start event"
        elif check.startswith("recovery_"):
            found = first_index(lambda position, value=check: phases[position] == value, cursor)
            evidence = f"phase={check}"
        elif check == "gripper_restored":
            found = first_index(
                lambda position: position < len(command)
                and command[position] < -0.5
                and gripper[position] < 0.65,
                cursor,
            )
            evidence = "close command matched by physical gripper response"
        elif check == "predicate":
            candidate = _event_index(replay, "predicate-restored")
            found = candidate if predicate_restored and candidate is not None and candidate >= cursor else None
            evidence = "predicate-restored event with physical verification"
        elif check in {"success", "success_safe"}:
            found = first_index(lambda position: bool(replay.get("success_trace", [])[position]), cursor)
            safe = check != "success_safe" or post_recovery_peak is not None and post_recovery_peak <= 36.0
            if not safe:
                found = None
            evidence = "success_trace=true" + (" and force<=36.0 N" if check == "success_safe" else "")
        completed = found is not None and found >= cursor
        if completed:
            cursor = found
        results.append({
            "index": index,
            "kind": kind,
            "operator": operator,
            "label": label,
            "status": "completed" if completed else "failed",
            "started_at": None if found is None else round(float(timestamps[found]), 3),
            "completed_at": None if found is None else round(float(timestamps[found]), 3),
            "evidence": evidence,
        })
    return results


def build_recovery_result(
    scenario: str,
    seed: int,
    horizon: int,
    failure_replay: dict[str, Any],
    recovered_replay: dict[str, Any],
) -> dict[str, Any]:
    protocol = RECOVERY_PROTOCOLS[scenario]
    start_index = _recovery_start_index(recovered_replay)
    if start_index is None:
        start_index = len(recovered_replay.get("timestamps", []))
    start_time = start_index / max(1, int(recovered_replay.get("fps", 20)))
    failure = _variant_summary(failure_replay)
    restored = _predicate_restored(scenario, recovered_replay, start_index)
    restored_index = _event_index(recovered_replay, "predicate-restored")
    recovered = _variant_summary(recovered_replay, restored_index if restored else start_index)
    completion_time = _first_true_time(recovered_replay, start_index)
    latency = None if completion_time is None else max(0.0, completion_time - start_time)
    common_duration = min(failure["duration"], recovered["duration"])
    failure_common_path = _path_length(failure_replay, common_duration)
    recovered_common_path = _path_length(recovered_replay, common_duration)
    path_overhead = recovered_common_path - failure_common_path
    path_overhead_rate = path_overhead / max(failure_common_path, 1e-9) * 100.0
    pair_integrity = _pair_integrity(scenario, failure_replay, recovered_replay)
    failure_control_passed = not failure["success"] and recovered["success"]
    plan = _plan_results(
        scenario,
        recovered_replay,
        restored,
        recovered["post_recovery_peak_force"],
    )
    plan_complete = all(step["status"] == "completed" for step in plan)
    task_recovery_passed = bool(
        pair_integrity["passed"]
        and failure_control_passed
        and recovered["success"]
        and restored
        and plan_complete
    )
    episode_safety = _force_safety_verdict(recovered_replay, scope="full recovered episode")
    post_intervention_safety = _force_safety_verdict(
        recovered_replay,
        start_index,
        scope="from online recovery trigger",
    )
    task_recovery = {
        "passed": task_recovery_passed,
        "status": "recovered" if task_recovery_passed else "failed",
        "criterion": "paired failure control, task success, predicate restoration, and ordered recovery plan",
        "predicate_restored": restored,
        "plan_complete": plan_complete,
        "failure_control_passed": failure_control_passed,
    }
    gates = [
        {
            "key": "pair_integrity",
            "label": "配对实验受控变量一致",
            "passed": pair_integrity["passed"],
            "value": f"fingerprint={pair_integrity['fingerprint']}",
        },
        {
            "key": "failure_control",
            "label": "故障对照产生预期失败",
            "passed": failure_control_passed,
            "value": f"failure={str(failure['success']).lower()} -> recovered={str(recovered['success']).lower()}",
        },
        {
            "key": "task_success",
            "label": "恢复后任务成功",
            "passed": recovered["success"],
            "value": "success=true" if recovered["success"] else "success=false",
        },
        {
            "key": "predicate_restored",
            "label": protocol["predicate_label"],
            "passed": restored,
            "value": f"{protocol['predicate']}={'true' if restored else 'false'}",
        },
        {
            "key": "recovery_plan_complete",
            "label": "恢复算子全部完成",
            "passed": plan_complete,
            "value": f"{sum(step['status'] == 'completed' for step in plan)}/{len(plan)} operators",
        },
        {
            "key": "post_recovery_force",
            "label": "恢复阶段接触力受控",
            "passed": recovered["post_recovery_peak_force"] is not None and recovered["post_recovery_peak_force"] <= 36.0,
            "value": f"{recovered['post_recovery_peak_force']:.1f} N" if recovered["post_recovery_peak_force"] is not None else "—",
        },
    ]
    return {
        "scenario": scenario,
        "scenario_name": SCENARIOS[scenario]["name"],
        "recovery_name": protocol["name"],
        "predicate": protocol["predicate"],
        "predicate_label": protocol["predicate_label"],
        "seed": seed,
        "horizon": horizon,
        "recovery_start_time": round(start_time, 3),
        "trigger": recovered_replay.get("recovery", {}),
        "pair_integrity": pair_integrity,
        "comparison_window": {
            "duration": round(common_duration, 3),
            "policy": "shared observed interval",
        },
        "failure": failure,
        "recovered": recovered,
        "verdicts": {
            "task_recovery": task_recovery,
            "episode_safety": episode_safety,
            "post_intervention_safety": post_intervention_safety,
        },
        "metrics": {
            "success_delta": int(recovered["success"]) - int(failure["success"]),
            "predicate_restored": restored,
            "recovery_latency": None if latency is None else round(latency, 3),
            "path_overhead": round(path_overhead, 4),
            "path_overhead_rate": round(path_overhead_rate, 1),
            "post_recovery_peak_force": recovered["post_recovery_peak_force"],
            "operator_completion_rate": round(sum(step["status"] == "completed" for step in plan) / len(plan) * 100.0, 1),
            "task_recovery": task_recovery_passed,
            "episode_safety": episode_safety["passed"],
            "post_intervention_safety": post_intervention_safety["passed"],
        },
        "quality_gates": gates,
        "passed": all(gate["passed"] for gate in gates),
        "plan": plan,
        "comparison": [
            {"metric": "任务成功", "failure": failure["success"], "recovered": recovered["success"], "unit": "bool"},
            {"metric": "共同窗口轨迹长度", "failure": round(failure_common_path, 4), "recovered": round(recovered_common_path, 4), "unit": "m"},
            {"metric": "Episode 时长", "failure": failure["duration"], "recovered": recovered["duration"], "unit": "s"},
            {"metric": "全程接触力峰值", "failure": failure["peak_force"], "recovered": recovered["peak_force"], "unit": "N"},
            {"metric": "最终抓取状态", "failure": failure["final_grasped"], "recovered": recovered["final_grasped"], "unit": "bool"},
        ],
    }


class RecoveryManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.output_root = self.project_root / "output" / "recovery"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._load_existing_jobs()

    def _load_existing_jobs(self) -> None:
        for result_path in self.output_root.glob("recovery-*/result.json"):
            job_id = result_path.parent.name
            if not re.fullmatch(r"recovery-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", job_id):
                continue
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                created_at = result_path.stat().st_mtime
                self._jobs[job_id] = {
                    "id": job_id,
                    "status": "completed",
                    "progress": 1.0,
                    "message": "已恢复历史配对实验",
                    "config": {
                        "scenario": result["scenario"],
                        "seed": result["seed"],
                        "horizon": result["horizon"],
                    },
                    "created_at": created_at,
                    "updated_at": created_at,
                    "result": result,
                    "error": None,
                }
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        scenario = str(payload.get("scenario", "collision"))
        if scenario not in RECOVERY_PROTOCOLS:
            raise ValueError("RecoveryLab 当前支持碰撞、夹爪失效和抓取滑脱")
        seed = int(payload.get("seed", 7))
        horizon = int(payload.get("horizon", 140))
        if not 100 <= horizon <= 160:
            raise ValueError("horizon 必须在 100 到 160 之间")
        if not 0 <= seed <= 2**31 - 1:
            raise ValueError("seed 必须是非负 32 位整数")
        if not runtime_status()["available"]:
            raise ValueError("当前 Python 环境未安装 ManiSkill/SAPIEN")
        with self._lock:
            if any(job["status"] in {"queued", "running"} for job in self._jobs.values()):
                raise ValueError("已有恢复实验正在运行")
            job_id = f"recovery-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0.0,
                "message": "等待配对实验启动",
                "config": {"scenario": scenario, "seed": seed, "horizon": horizon},
                "created_at": time.time(),
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
        threading.Thread(
            target=self._run_job,
            args=(job_id, scenario, seed, horizon),
            daemon=True,
            name=f"EmbodiScope-{job_id}",
        ).start()
        return self.status(job_id)

    def _run_job(self, job_id: str, scenario: str, seed: int, horizon: int) -> None:
        output_dir = self.output_root / job_id
        cancel = self._cancel_events[job_id]
        self._update(job_id, status="running", message="正在运行失败对照组")

        def failure_progress(value: float, message: str) -> None:
            self._update(job_id, progress=round(value * 0.45, 4), message=f"失败组 · {message}")

        def recovered_progress(value: float, message: str) -> None:
            self._update(job_id, progress=round(0.5 + value * 0.46, 4), message=f"恢复组 · {message}")

        try:
            with SIMULATION_EXECUTION_LOCK:
                failure = run_simulation(
                    SimulationConfig(
                        scenario=scenario,
                        seed=seed,
                        steps=horizon,
                        record_video=True,
                        recovery_enabled=False,
                    ),
                    output_dir / "failure",
                    progress=failure_progress,
                    cancelled=cancel.is_set,
                )
                if cancel.is_set():
                    raise InterruptedError("恢复实验已取消")
                self._update(job_id, progress=0.5, message="失败组完成，正在运行局部恢复组")
                recovered = run_simulation(
                    SimulationConfig(
                        scenario=scenario,
                        seed=seed,
                        steps=horizon,
                        record_video=True,
                        recovery_enabled=True,
                    ),
                    output_dir / "recovered",
                    progress=recovered_progress,
                    cancelled=cancel.is_set,
                )
            failure_replay = _load_replay(Path(failure["replay_path"]))
            recovered_replay = _load_replay(Path(recovered["replay_path"]))
            result = build_recovery_result(scenario, seed, horizon, failure_replay, recovered_replay)
            result["downloads"] = {"json": f"/api/recovery/result/{job_id}"}
            result["variants"] = {
                "failure": {
                    "video_url": f"/api/recovery/video/{job_id}/failure",
                    "replay_url": f"/api/recovery/replay/{job_id}/failure",
                    "thumbnail_url": f"/api/recovery/thumbnail/{job_id}/failure",
                },
                "recovered": {
                    "video_url": f"/api/recovery/video/{job_id}/recovered",
                    "replay_url": f"/api/recovery/replay/{job_id}/recovered",
                    "thumbnail_url": f"/api/recovery/thumbnail/{job_id}/recovered",
                },
            }
            (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            self._update(job_id, status="completed", progress=1.0, message="A/B 恢复实验完成", result=result)
        except InterruptedError as error:
            self._update(job_id, status="cancelled", message=str(error), error=str(error))
        except Exception as error:
            self._update(job_id, status="failed", message="恢复实验失败", error=f"{type(error).__name__}: {error}")

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)
            self._jobs[job_id]["updated_at"] = time.time()

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if job_id:
                if job_id not in self._jobs:
                    raise ValueError("找不到恢复实验")
                return json.loads(json.dumps(self._jobs[job_id]))
            jobs = sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return {
                "jobs": json.loads(json.dumps(jobs[:8])),
                "active_job": next((job["id"] for job in jobs if job["status"] in {"queued", "running"}), None),
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError("找不到恢复实验")
            if self._jobs[job_id]["status"] not in {"queued", "running"}:
                raise ValueError("该恢复实验当前不可取消")
            self._cancel_events[job_id].set()
        return self.status(job_id)

    def artifact(self, job_id: str, variant: str, name: str) -> Path:
        if not re.fullmatch(r"recovery-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", job_id):
            raise ValueError("非法恢复实验编号")
        if variant not in {"failure", "recovered"}:
            raise ValueError("非法恢复实验分组")
        if name not in {"episode.mp4", "replay.json", "trajectory.h5", "thumbnail.jpg"}:
            raise ValueError("非法恢复实验文件")
        path = (self.output_root / job_id / variant / name).resolve()
        if not path.is_relative_to(self.output_root.resolve()) or not path.is_file():
            raise ValueError("恢复实验文件不存在")
        return path

    def result_path(self, job_id: str) -> Path:
        if not re.fullmatch(r"recovery-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", job_id):
            raise ValueError("非法恢复实验编号")
        path = (self.output_root / job_id / "result.json").resolve()
        if not path.is_relative_to(self.output_root.resolve()) or not path.is_file():
            raise ValueError("恢复实验结果不存在")
        return path
