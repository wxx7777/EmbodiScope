from __future__ import annotations

import importlib.util
import json
import platform
import re
import threading
import time
import uuid
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np


SCENARIOS = {
    "nominal": {
        "name": "完整抓取到目标",
        "description": "数值雅可比闭环控制 Panda 接近、抓取并将方块移动到目标位姿。",
        "fault": None,
        "category": "baseline",
        "category_name": "基准任务",
        "expected": ["TASK_SUCCESS", "持续抓取"],
        "recommended_steps": 80,
    },
    "collision": {
        "name": "碰撞注入",
        "description": "在接近阶段注入向下执行器指令，制造真实接触力峰值。",
        "fault": "ACTUATOR_SURGE",
        "category": "contact",
        "category_name": "接触与安全",
        "expected": ["FORCE_SPIKE", "碰撞位置"],
        "recommended_steps": 80,
    },
    "grasp-slip": {
        "name": "抓取滑脱",
        "description": "方块被抓起后施加横向外力，验证抓取保持、物体脱落与恢复条件。",
        "fault": "EXTERNAL_OBJECT_FORCE",
        "category": "task",
        "category_name": "任务执行",
        "expected": ["GRASP_SLIP", "抓取状态丢失"],
        "recommended_steps": 80,
    },
    "gripper-failure": {
        "name": "夹爪执行器失效",
        "description": "策略持续发出闭合意图，但夹爪保持张开，形成动作-执行反馈不一致。",
        "fault": "GRIPPER_RESPONSE_FAILURE",
        "category": "control",
        "category_name": "控制与执行器",
        "expected": ["GRIPPER_RESPONSE_FAILURE", "抓取未建立"],
        "recommended_steps": 80,
    },
    "actuator-stall": {
        "name": "执行器卡滞",
        "description": "在到达预抓取阶段冻结控制增量 1.5 秒，再观察恢复后的任务状态。",
        "fault": "ACTUATOR_STALL",
        "category": "control",
        "category_name": "控制与执行器",
        "expected": ["ROBOT_STUCK", "技能进展中断"],
        "recommended_steps": 80,
    },
    "object-perturbation": {
        "name": "动态目标扰动",
        "description": "接近过程中对方块施加侧向速度，测试闭环控制对目标移动的在线适应。",
        "fault": "OBJECT_PERTURBATION",
        "category": "task",
        "category_name": "任务执行",
        "expected": ["目标位姿变化", "闭环重定位"],
        "recommended_steps": 90,
    },
    "sensor-delay": {
        "name": "视觉延迟注入",
        "description": "将 RGB 帧延迟 200 ms，验证视觉与状态互相关诊断。",
        "fault": "SENSOR_DELAY",
        "category": "perception",
        "category_name": "感知与时序",
        "expected": ["SENSOR_DESYNC", "200 ms 延迟"],
        "recommended_steps": 80,
    },
    "frame-drop": {
        "name": "连续丢帧注入",
        "description": "重复上一帧并标记视觉信号缺失，模拟相机采集阻塞。",
        "fault": "FRAME_DROP",
        "category": "perception",
        "category_name": "感知与时序",
        "expected": ["FRAME_DROP", "连续 6 帧无效"],
        "recommended_steps": 80,
    },
    "camera-occlusion": {
        "name": "相机遮挡",
        "description": "在抓取窗口遮挡 RGB 画面并保留物理执行，区分感知失效与机器人状态。",
        "fault": "CAMERA_OCCLUSION",
        "category": "perception",
        "category_name": "感知与时序",
        "expected": ["FRAME_DROP", "遮挡窗口"],
        "recommended_steps": 80,
    },
    "compound-failure": {
        "name": "碰撞 + 延迟 + 丢帧",
        "description": "同时注入执行器向下冲击、200 ms 视觉延迟和连续丢帧，验证多故障分离。",
        "fault": "COMPOUND_FAILURE",
        "category": "compound",
        "category_name": "复合压力测试",
        "expected": ["FORCE_SPIKE", "SENSOR_DESYNC", "FRAME_DROP"],
        "recommended_steps": 80,
    },
}


SIMULATION_EXECUTION_LOCK = threading.Lock()


@dataclass(frozen=True)
class SimulationConfig:
    env_id: str = "PickCube-v1"
    scenario: str = "collision"
    seed: int = 7
    steps: int = 80
    fps: int = 20
    width: int = 320
    height: int = 240
    record_video: bool = True
    recovery_enabled: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SimulationConfig":
        config = cls(
            env_id=str(payload.get("env_id", cls.env_id)),
            scenario=str(payload.get("scenario", cls.scenario)),
            seed=int(payload.get("seed", cls.seed)),
            steps=int(payload.get("steps", cls.steps)),
            fps=int(payload.get("fps", cls.fps)),
            width=int(payload.get("width", cls.width)),
            height=int(payload.get("height", cls.height)),
            record_video=bool(payload.get("record_video", True)),
            recovery_enabled=bool(payload.get("recovery_enabled", False)),
        )
        if config.env_id != "PickCube-v1":
            raise ValueError("当前闭环运行器仅开放 PickCube-v1")
        if config.scenario not in SCENARIOS:
            raise ValueError(f"未知仿真场景: {config.scenario}")
        if not 30 <= config.steps <= 160:
            raise ValueError("steps 必须在 30 到 160 之间")
        if not 0 <= config.seed <= 2**31 - 1:
            raise ValueError("seed 必须是非负 32 位整数")
        if config.fps not in {10, 20, 30}:
            raise ValueError("fps 仅支持 10、20 或 30")
        if config.width not in {256, 320, 384} or config.height not in {192, 240, 288}:
            raise ValueError("视频尺寸不在允许范围内")
        return config


@dataclass
class RecoveryMonitor:
    trigger_step: int | None = None
    violation_step: int | None = None
    restored_step: int | None = None
    success_step: int | None = None
    predicate: str | None = None
    trigger_type: str | None = None
    evidence: str | None = None
    gripper_mismatch_frames: int = 0
    was_grasped: bool = False

    def payload(self, fps: int) -> dict[str, Any]:
        return {
            "enabled": True,
            "trigger_source": "online-predicate-monitor",
            "trigger_step": self.trigger_step,
            "trigger_time": None if self.trigger_step is None else round(self.trigger_step / fps, 3),
            "violation_step": self.violation_step,
            "violation_time": None if self.violation_step is None else round(self.violation_step / fps, 3),
            "predicate": self.predicate,
            "trigger_type": self.trigger_type,
            "evidence": self.evidence,
            "predicate_restored_step": self.restored_step,
            "predicate_restored_time": None if self.restored_step is None else round(self.restored_step / fps, 3),
            "success_step": self.success_step,
            "success_time": None if self.success_step is None else round(self.success_step / fps, 3),
        }


def runtime_status() -> dict[str, Any]:
    available = importlib.util.find_spec("mani_skill") is not None and importlib.util.find_spec("sapien") is not None
    payload: dict[str, Any] = {
        "available": available,
        "python": platform.python_version(),
        "sim_backend": "physx_cpu",
        "render_backend": "sapien_cpu",
        "gpu_render_supported": False,
        "gpu_render_note": "Windows 上使用稳定的 SAPIEN CPU 相机路径录制；物理仍由 PhysX 执行。",
    }
    if not available:
        payload["error"] = "未安装 ManiSkill/SAPIEN，可运行 pip install -e .[simulation]"
        return payload
    try:
        from importlib.metadata import version

        payload["mani_skill_version"] = version("mani-skill")
        payload["sapien_version"] = version("sapien")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pinnochio package is not installed.*", category=UserWarning)
            import sapien

        payload["devices"] = sapien.render.get_device_summary().strip().splitlines()
    except Exception as error:  # Runtime probing should not prevent the web app from starting.
        payload["probe_warning"] = str(error)
    return payload


def simulation_catalog() -> dict[str, Any]:
    return {
        "runtime": runtime_status(),
        "environments": [
            {
                "id": "PickCube-v1",
                "name": "Panda Pick Cube",
                "robot": "Franka Emika Panda",
                "engine": "ManiSkill 3 + SAPIEN 3 + PhysX",
                "control": "pd_joint_delta_pos / numerical Jacobian feedback",
                "max_steps": 160,
            }
        ],
        "scenarios": [{"id": key, **value} for key, value in SCENARIOS.items()],
    }


def _array(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    result = np.asarray(value)
    return result[0].copy() if result.ndim > 1 else result.copy()


def _numerical_position_jacobian(env: Any, epsilon: float = 1e-3) -> np.ndarray:
    robot = env.agent.robot
    qpos = robot.get_qpos().clone()
    origin = _array(env.agent.tcp.pose.p)
    jacobian = np.zeros((3, 7), dtype=np.float64)
    for joint in range(7):
        perturbed = qpos.clone()
        perturbed[0, joint] += epsilon
        robot.set_qpos(perturbed)
        jacobian[:, joint] = (_array(env.agent.tcp.pose.p) - origin) / epsilon
    robot.set_qpos(qpos)
    return jacobian


def _policy_action(
    env: Any,
    step: int,
    config: SimulationConfig,
    recovery_start: int | None = None,
) -> tuple[np.ndarray, str]:
    cube = _array(env.cube.pose.p)
    tcp = _array(env.agent.tcp.pose.p)
    scenario = config.scenario

    recovery_target: tuple[np.ndarray, float, str, float, float] | None = None
    recovery_step = None if recovery_start is None else step - recovery_start
    if config.recovery_enabled and scenario == "collision" and recovery_step is not None and recovery_step >= 0:
        if recovery_step < 10:
            recovery_target = (cube + np.array([0.0, 0.0, 0.14]), 1.0, "recovery_retreat", 0.70, 0.08)
        elif recovery_step < 26:
            recovery_target = (cube + np.array([0.0, 0.0, 0.10]), 1.0, "recovery_reobserve", 0.78, 0.09)
        elif recovery_step < 42:
            recovery_target = (cube + np.array([0.0, 0.0, 0.004]), 1.0, "recovery_reach", 0.78, 0.09)
        elif recovery_step < 58:
            recovery_target = (cube + np.array([0.0, 0.0, 0.004]), -1.0, "recovery_grasp", 0.50, 0.04)
        else:
            recovery_target = (_array(env.goal_site.pose.p).astype(np.float64), -1.0, "recovery_transport", 0.62, 0.07)
    elif config.recovery_enabled and scenario == "gripper-failure" and recovery_step is not None and recovery_step >= 0:
        if recovery_step < 12:
            recovery_target = (cube + np.array([0.0, 0.0, 0.10]), 1.0, "recovery_retreat", 0.78, 0.09)
        elif recovery_step < 28:
            recovery_target = (cube + np.array([0.0, 0.0, 0.004]), 1.0, "recovery_reach", 0.78, 0.09)
        elif recovery_step < 44:
            recovery_target = (cube + np.array([0.0, 0.0, 0.004]), -1.0, "recovery_grasp", 0.50, 0.04)
        else:
            recovery_target = (_array(env.goal_site.pose.p).astype(np.float64), -1.0, "recovery_transport", 0.62, 0.07)
    elif config.recovery_enabled and scenario == "grasp-slip" and recovery_step is not None and recovery_step >= 0:
        if recovery_step < 9:
            recovery_target = (tcp.copy(), 1.0, "recovery_hold", 0.0, 0.0)
        elif recovery_step < 23:
            recovery_target = (cube + np.array([0.0, 0.0, 0.10]), 1.0, "recovery_reobserve", 0.68, 0.075)
        elif recovery_step < 39:
            recovery_target = (cube + np.array([0.0, 0.0, 0.012]), 1.0, "recovery_reach", 0.68, 0.075)
        elif recovery_step < 55:
            recovery_target = (cube + np.array([0.0, 0.0, 0.012]), -1.0, "recovery_grasp", 0.45, 0.035)
        else:
            recovery_target = (_array(env.goal_site.pose.p).astype(np.float64), -1.0, "recovery_transport", 0.62, 0.07)

    if recovery_target is not None:
        target, gripper, phase, gain, limit = recovery_target
    elif scenario == "actuator-stall" and 24 <= step < 54:
        return np.zeros(8, dtype=np.float32), "fault_stall"
    elif scenario in {"collision", "compound-failure"} and 24 <= step < 40:
        target = np.array([cube[0], cube[1], -0.035], dtype=np.float64)
        gripper, phase, gain, limit = 1.0, "fault_collision", 0.95, 0.1
    elif step < 18:
        target = cube + np.array([0.0, 0.0, 0.10])
        gripper, phase, gain, limit = 1.0, "approach", 0.80, 0.10
    elif step < 34:
        target = cube + np.array([0.0, 0.0, 0.004])
        gripper, phase, gain, limit = 1.0, "reach", 0.80, 0.10
    elif step < 50:
        target = cube + np.array([0.0, 0.0, 0.004])
        gripper, phase, gain, limit = -1.0, "grasp", 0.50, 0.04
    else:
        target = _array(env.goal_site.pose.p).astype(np.float64)
        gripper, phase, gain, limit = -1.0, "transport", 0.62, 0.07

    error = target - tcp
    jacobian = _numerical_position_jacobian(env)
    damping = 0.03
    delta_q = jacobian.T @ np.linalg.solve(jacobian @ jacobian.T + damping * np.eye(3), error * gain)
    delta_q = np.clip(delta_q, -limit, limit)
    action = np.concatenate([np.clip(delta_q / 0.1, -1.0, 1.0), [gripper]]).astype(np.float32)
    return action, phase


def _observe_recovery_violation(
    config: SimulationConfig,
    monitor: RecoveryMonitor,
    step: int,
    force: float,
    gripper_command: float,
    gripper_actual: float,
    is_grasped: bool,
) -> dict[str, Any] | None:
    if not config.recovery_enabled or monitor.trigger_step is not None:
        monitor.was_grasped = monitor.was_grasped or is_grasped
        return None

    trigger: tuple[str, str, str] | None = None
    if config.scenario == "collision" and force > 36.0:
        trigger = (
            "collision_free",
            "force-threshold",
            f"contact_force={force:.1f} N > 36.0 N",
        )
    elif config.scenario == "gripper-failure":
        mismatch = gripper_command < -0.5 and gripper_actual > 0.75
        monitor.gripper_mismatch_frames = monitor.gripper_mismatch_frames + 1 if mismatch else 0
        if monitor.gripper_mismatch_frames >= 4:
            trigger = (
                "object_attached",
                "gripper-command-response-mismatch",
                f"close command unmatched for {monitor.gripper_mismatch_frames} frames",
            )
    elif config.scenario == "grasp-slip" and monitor.was_grasped and not is_grasped:
        trigger = (
            "object_attached",
            "grasp-state-transition",
            "is_grasped changed true -> false",
        )

    monitor.was_grasped = monitor.was_grasped or is_grasped
    if trigger is None:
        return None

    predicate, trigger_type, evidence = trigger
    monitor.violation_step = step
    monitor.trigger_step = step + 1
    monitor.predicate = predicate
    monitor.trigger_type = trigger_type
    monitor.evidence = evidence
    return {
        "time": step / config.fps,
        "end_time": step / config.fps,
        "type": "predicate-violated",
        "severity": "critical",
        "label": f"{predicate}=false · {evidence}",
        "source": "recovery-monitor",
    }


def _apply_scene_disturbance(env: Any, step: int, config: SimulationConfig) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    scenario = config.scenario
    if scenario == "object-perturbation" and step == 12:
        env.cube.set_linear_velocity([0.0, 1.0, 0.18])
        events.append({
            "time": step / config.fps,
            "end_time": (step + 5) / config.fps,
            "type": "object-perturbation",
            "severity": "warning",
            "label": "方块受到侧向动态扰动",
        })
    if scenario == "grasp-slip" and step == 52:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="component.pose can be ambiguous.*", category=DeprecationWarning)
            env.cube.apply_force([60.0, 0.0, -6.0])
        events.append({
            "time": step / config.fps,
            "end_time": 56 / config.fps,
            "type": "grasp-slip-force",
            "severity": "critical",
            "label": "抓取后施加 60 N 横向外力",
        })
    if scenario in {"collision", "compound-failure"} and step == 24:
        events.append({
            "time": step / config.fps,
            "end_time": 39 / config.fps,
            "type": "collision-command",
            "severity": "critical",
            "label": "执行器向下冲击指令",
        })
    if scenario == "actuator-stall" and step == 24:
        events.append({
            "time": step / config.fps,
            "end_time": 53 / config.fps,
            "type": "actuator-stall",
            "severity": "critical",
            "label": "控制增量冻结 1.5 秒",
        })
    if scenario == "gripper-failure" and step == 34:
        events.append({
            "time": step / config.fps,
            "end_time": (config.steps - 1) / config.fps,
            "type": "gripper-failure",
            "severity": "critical",
            "label": "夹爪闭合指令未执行",
        })
    return events


def _frame_motion(frames: list[np.ndarray]) -> np.ndarray:
    motion = np.zeros(len(frames), dtype=np.float32)
    for index in range(1, len(frames)):
        current = frames[index].astype(np.float32)
        previous = frames[index - 1].astype(np.float32)
        motion[index] = float(np.mean(np.abs(current - previous)) / 255.0)
    return motion


def _inject_visual_faults(
    frames: list[np.ndarray], scenario: str, fps: int
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, list[dict[str, Any]]]:
    output = [frame.copy() for frame in frames]
    valid = np.ones(len(output), dtype=np.uint8)
    events: list[dict[str, Any]] = []
    if scenario in {"sensor-delay", "compound-failure"} and output:
        delay_frames = max(2, round(0.2 * fps))
        output = [output[max(0, index - delay_frames)].copy() for index in range(len(output))]
        events.append({
            "time": delay_frames / fps,
            "end_time": (len(output) - 1) / fps,
            "type": "sensor-delay",
            "severity": "critical",
            "label": f"视觉延迟 {delay_frames / fps * 1000:.0f} ms",
        })
    if scenario in {"frame-drop", "compound-failure"} and len(output) > 12:
        start = min(max(10, len(output) // 2 - 3), len(output) - 7)
        end = min(len(output), start + 6)
        for index in range(start, end):
            output[index] = output[start - 1].copy()
            valid[index] = 0
        events.append({
            "time": start / fps,
            "end_time": (end - 1) / fps,
            "type": "frame-drop",
            "severity": "critical",
            "label": f"连续丢帧 {end - start} 帧",
        })
    if scenario == "camera-occlusion" and len(output) > 16:
        start = min(max(12, len(output) // 2 - 5), len(output) - 11)
        end = min(len(output), start + 10)
        for index in range(start, end):
            output[index] = np.full_like(output[index], 12)
            valid[index] = 0
        events.append({
            "time": start / fps,
            "end_time": (end - 1) / fps,
            "type": "camera-occlusion",
            "severity": "critical",
            "label": f"相机遮挡 {end - start} 帧",
        })
    motion = _frame_motion(output)
    motion[valid == 0] = np.nan
    return output, motion, valid, events


def _write_video(path: Path, frames: list[np.ndarray], fps: int) -> None:
    import imageio.v2 as imageio

    path.parent.mkdir(parents=True, exist_ok=True)
    with imageio.get_writer(
        path,
        fps=fps,
        codec="libx264",
        quality=7,
        macro_block_size=None,
        ffmpeg_log_level="error",
    ) as writer:
        for frame in frames:
            writer.append_data(frame)


def run_simulation(
    config: SimulationConfig,
    output_dir: Path,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if not runtime_status()["available"]:
        raise RuntimeError("ManiSkill/SAPIEN 运行时不可用")

    import gymnasium as gym
    import h5py
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="pinnochio package is not installed.*", category=UserWarning)
        import mani_skill.envs  # noqa: F401 - registers Gymnasium environments

    output_dir.mkdir(parents=True, exist_ok=True)
    update = progress or (lambda value, message: None)
    update(0.02, "正在初始化 ManiSkill 与 PhysX")
    env = gym.make(
        config.env_id,
        num_envs=1,
        obs_mode="state",
        render_mode="rgb_array" if config.record_video else None,
        sim_backend="physx_cpu",
        render_backend="sapien_cpu" if config.record_video else "none",
        max_episode_steps=config.steps,
        human_render_camera_configs={
            "width": config.width,
            "height": config.height,
            "shader_pack": "minimal",
        },
    )

    records: dict[str, list[Any]] = {
        "timestamps": [], "qpos": [], "actions": [], "tcp_pose": [], "goal_pos": [],
        "obj_pose": [], "link_positions": [], "force": [], "gripper": [], "gripper_command": [], "applied_actions": [],
        "is_grasped": [], "reward": [], "success": [], "phase": [],
    }
    frames: list[np.ndarray] = []
    injected_events: list[dict[str, Any]] = []
    recovery_monitor = RecoveryMonitor()
    started = time.perf_counter()
    try:
        _, info = env.reset(seed=config.seed)
        raw = env.unwrapped
        link_names = [link.name for link in raw.agent.robot.get_links()]
        for step in range(config.steps):
            if cancelled and cancelled():
                raise InterruptedError("仿真作业已取消")
            if config.recovery_enabled and recovery_monitor.trigger_step == step:
                injected_events.append({
                    "time": step / config.fps,
                    "end_time": step / config.fps,
                    "type": "recovery-start",
                    "severity": "info",
                    "label": f"在线谓词触发局部恢复 · {recovery_monitor.trigger_type}",
                    "source": "recovery-monitor",
                })
            injected_events.extend(_apply_scene_disturbance(raw, step, config))
            action, phase = _policy_action(raw, step, config, recovery_monitor.trigger_step)
            applied_action = action.copy()
            if config.scenario == "gripper-failure" and step >= 34 and (
                not config.recovery_enabled
                or recovery_monitor.trigger_step is None
                or step < recovery_monitor.trigger_step
            ):
                applied_action[-1] = 1.0
                phase = "fault_gripper_open" if step < 50 else "transport_without_grasp"
            _, reward, terminated, truncated, info = env.step(applied_action)
            contact = _array(raw.agent.robot.get_net_contact_forces(link_names))
            force_magnitude = float(np.linalg.norm(contact, axis=-1).max(initial=0.0))
            qpos = _array(raw.agent.robot.get_qpos())
            tcp_pose = np.concatenate([_array(raw.agent.tcp.pose.p), _array(raw.agent.tcp.pose.q)])
            obj_pose = np.concatenate([_array(raw.cube.pose.p), _array(raw.cube.pose.q)])
            gripper_actual = float(np.clip(np.mean(qpos[-2:]) / 0.04, 0.0, 1.0))
            is_grasped = bool(_array(info.get("is_grasped", False)).reshape(-1)[0])
            success_value = bool(_array(info["success"]).reshape(-1)[0])
            records["timestamps"].append(step / config.fps)
            records["qpos"].append(qpos)
            records["actions"].append(action)
            records["applied_actions"].append(applied_action)
            records["tcp_pose"].append(tcp_pose)
            records["goal_pos"].append(_array(raw.goal_site.pose.p))
            records["obj_pose"].append(obj_pose)
            records["link_positions"].append([_array(link.pose.p).tolist() for link in raw.agent.robot.get_links()])
            records["force"].append([0.0, 0.0, force_magnitude])
            records["gripper"].append(gripper_actual)
            records["gripper_command"].append(float(action[-1]))
            records["is_grasped"].append(is_grasped)
            records["reward"].append(float(_array(reward).reshape(-1)[0]))
            records["success"].append(success_value)
            records["phase"].append(phase)
            violation_event = _observe_recovery_violation(
                config,
                recovery_monitor,
                step,
                force_magnitude,
                float(action[-1]),
                gripper_actual,
                is_grasped,
            )
            if violation_event is not None:
                injected_events.append(violation_event)
            if config.record_video:
                frame = _array(env.render()).astype(np.uint8)
                frames.append(frame)
            update(0.08 + 0.72 * (step + 1) / config.steps, f"执行 PhysX 步进 {step + 1}/{config.steps}")
            if bool(_array(terminated).reshape(-1)[0]):
                break
            if bool(_array(truncated).reshape(-1)[0]) and step + 1 >= config.steps:
                break
    finally:
        env.close()

    rows = len(records["timestamps"])
    if rows == 0:
        raise RuntimeError("仿真未产生有效轨迹")
    if config.record_video:
        frames, camera_motion, frame_valid, visual_events = _inject_visual_faults(frames, config.scenario, config.fps)
        injected_events.extend(visual_events)
    else:
        camera_motion = np.zeros(rows, dtype=np.float32)
        frame_valid = np.ones(rows, dtype=np.uint8)

    update(0.84, "正在写入 ManiSkill HDF5 轨迹")
    trajectory_path = output_dir / "trajectory.h5"
    with h5py.File(trajectory_path, "w") as handle:
        group = handle.create_group("traj_0")
        group.create_dataset("timestamps", data=np.asarray(records["timestamps"], dtype=np.float64))
        group.create_dataset("actions", data=np.asarray(records["actions"], dtype=np.float32))
        agent = group.create_group("obs").create_group("agent")
        agent.create_dataset("qpos", data=np.asarray(records["qpos"], dtype=np.float32))
        extra = group["obs"].create_group("extra")
        extra.create_dataset("tcp_pose", data=np.asarray(records["tcp_pose"], dtype=np.float32))
        extra.create_dataset("goal_pos", data=np.asarray(records["goal_pos"], dtype=np.float32))
        extra.create_dataset("obj_pose", data=np.asarray(records["obj_pose"], dtype=np.float32))
        extra.create_dataset("link_positions", data=np.asarray(records["link_positions"], dtype=np.float32))
        extra.create_dataset("force", data=np.asarray(records["force"], dtype=np.float32))
        if config.record_video:
            extra.create_dataset("camera_motion", data=camera_motion)
            extra.create_dataset("frame_valid", data=frame_valid)
        extra.create_dataset("gripper", data=np.asarray(records["gripper"], dtype=np.float32))
        extra.create_dataset("gripper_command", data=np.asarray(records["gripper_command"], dtype=np.float32))
        extra.create_dataset("applied_actions", data=np.asarray(records["applied_actions"], dtype=np.float32))
        extra.create_dataset("is_grasped", data=np.asarray(records["is_grasped"], dtype=np.uint8))
        extra.create_dataset("phase", data=np.asarray(records["phase"], dtype=h5py.string_dtype(encoding="utf-8")))
        group.create_dataset("success", data=np.asarray(records["success"], dtype=np.uint8))
        group.create_dataset("reward", data=np.asarray(records["reward"], dtype=np.float32))

    video_path = output_dir / "episode.mp4"
    if config.record_video:
        update(0.9, "正在编码 H.264 回放视频")
        _write_video(video_path, frames, config.fps)
        try:
            import imageio.v3 as iio

            iio.imwrite(output_dir / "thumbnail.jpg", frames[min(len(frames) - 1, max(0, len(frames) // 3))])
        except Exception:
            pass

    duration = records["timestamps"][-1] if rows > 1 else 0.0
    peak_force = float(np.max(np.asarray(records["force"])[:, 2]))
    success = bool(records["success"][-1])
    recovery_start = recovery_monitor.trigger_step if config.recovery_enabled else None
    if recovery_start is not None:
        if config.scenario == "collision":
            force_values = [float(item[2]) for item in records["force"]]
            restored_index = next((
                index
                for index in range(recovery_start, max(recovery_start, rows - 2))
                if records["phase"][index].startswith("recovery_")
                and all(value <= 36.0 for value in force_values[index:index + 3])
            ), None)
        else:
            restored_index = next(
                (index for index in range(recovery_start, rows) if records["is_grasped"][index]),
                None,
            )
        if restored_index is not None:
            recovery_monitor.restored_step = restored_index
            injected_events.append({
                "time": records["timestamps"][restored_index],
                "end_time": records["timestamps"][restored_index],
                "type": "predicate-restored",
                "severity": "info",
                "label": f"{recovery_monitor.predicate} 已恢复",
                "source": "recovery-monitor",
            })
        success_index = next(
            (index for index in range(recovery_start, rows) if records["success"][index]),
            None,
        )
        if success_index is not None:
            recovery_monitor.success_step = success_index
            injected_events.append({
                "time": records["timestamps"][success_index],
                "end_time": records["timestamps"][success_index],
                "type": "recovery-success",
                "severity": "info",
                "label": "恢复后任务成功",
                "source": "recovery-monitor",
            })
    metadata = {
        "env_info": {"env_id": config.env_id, "fps": config.fps},
        "fps": config.fps,
        "episodes": [{"episode_id": 0, "success": success}],
        "simulation": {
            **asdict(config),
            "engine": "ManiSkill 3 / SAPIEN 3 / PhysX CPU",
            "controller": "numerical Jacobian + pd_joint_delta_pos",
            "duration_seconds": duration,
            "wall_time_seconds": round(time.perf_counter() - started, 3),
            "peak_contact_force": round(peak_force, 3),
            "events": injected_events,
            "recovery": recovery_monitor.payload(config.fps) if config.recovery_enabled else {"enabled": False},
            "video_file": video_path.name if config.record_video else None,
        },
    }
    trajectory_path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    diagnosis: dict[str, Any] = {"quality_score": None, "issues": [], "events": []}
    try:
        from .adapters import load_dataset
        from .analysis import analyze_episode

        analysis = analyze_episode(load_dataset(trajectory_path).frame, "0")
        diagnosis = {
            "quality_score": analysis["quality_score"],
            "grade": analysis["grade"],
            "issues": [
                {"code": issue["code"], "severity": issue["severity"], "title": issue["title"]}
                for issue in analysis["issues"]
            ],
            "events": [{**event, "source": "diagnosis"} for event in analysis["events"]],
        }
    except Exception as error:
        diagnosis["warning"] = str(error)

    replay = {
        "env_id": config.env_id,
        "scenario": config.scenario,
        "scenario_name": SCENARIOS[config.scenario]["name"],
        "seed": config.seed,
        "config": asdict(config),
        "recovery": recovery_monitor.payload(config.fps) if config.recovery_enabled else {"enabled": False},
        "fps": config.fps,
        "duration": duration,
        "rows": rows,
        "success": success,
        "peak_force": round(peak_force, 3),
        "wall_time": metadata["simulation"]["wall_time_seconds"],
        "timestamps": records["timestamps"],
        "tcp": [pose[:3].tolist() for pose in records["tcp_pose"]],
        "link_names": link_names,
        "links": records["link_positions"],
        "object": [pose[:3].tolist() for pose in records["obj_pose"]],
        "goal": [pose.tolist() for pose in records["goal_pos"]],
        "force": [item[2] for item in records["force"]],
        "action_norm": [float(np.linalg.norm(item)) for item in records["actions"]],
        "applied_action_norm": [float(np.linalg.norm(item)) for item in records["applied_actions"]],
        "gripper": records["gripper"],
        "gripper_command": records["gripper_command"],
        "is_grasped": [int(value) for value in records["is_grasped"]],
        "success_trace": [int(value) for value in records["success"]],
        "frame_valid": frame_valid.astype(int).tolist(),
        "phases": records["phase"],
        "events": [{"source": event.get("source", "injection"), **event} for event in injected_events] + diagnosis["events"],
        "diagnosis": diagnosis,
        "video_available": config.record_video,
    }
    (output_dir / "replay.json").write_text(json.dumps(replay, ensure_ascii=False), encoding="utf-8")
    update(1.0, "仿真、录制与轨迹导出完成")
    return {
        "trajectory_path": str(trajectory_path),
        "video_path": str(video_path) if config.record_video else None,
        "replay_path": str(output_dir / "replay.json"),
        "summary": {key: replay[key] for key in ("env_id", "scenario", "rows", "duration", "success", "peak_force", "wall_time")},
    }


class SimulationManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.output_root = self.project_root / "output" / "simulations"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = SimulationConfig.from_payload(payload)
        if not runtime_status()["available"]:
            raise ValueError("当前 Python 环境未安装 ManiSkill/SAPIEN")
        with self._lock:
            if any(job["status"] in {"queued", "running"} for job in self._jobs.values()):
                raise ValueError("已有仿真作业正在运行，请等待完成或取消")
            job_id = f"sim-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0.0,
                "message": "等待仿真线程启动",
                "config": asdict(config),
                "created_at": time.time(),
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
        threading.Thread(target=self._run_job, args=(job_id, config), daemon=True, name=f"EmbodiScope-{job_id}").start()
        return self.status(job_id)

    def _run_job(self, job_id: str, config: SimulationConfig) -> None:
        self._update(job_id, status="running", message="正在启动真实仿真环境")
        output_dir = self.output_root / job_id

        def progress(value: float, message: str) -> None:
            self._update(job_id, progress=round(max(0.0, min(1.0, value)), 4), message=message)

        try:
            with SIMULATION_EXECUTION_LOCK:
                result = run_simulation(
                    config,
                    output_dir,
                    progress=progress,
                    cancelled=self._cancel_events[job_id].is_set,
                )
            result["video_url"] = f"/api/simulation/video/{job_id}"
            result["replay_url"] = f"/api/simulation/replay/{job_id}"
            self._update(job_id, status="completed", progress=1.0, message="可开始同步回放", result=result)
        except InterruptedError as error:
            self._update(job_id, status="cancelled", message=str(error), error=str(error))
        except Exception as error:
            self._update(job_id, status="failed", message="仿真执行失败", error=f"{type(error).__name__}: {error}")

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)
            self._jobs[job_id]["updated_at"] = time.time()

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if job_id:
                if job_id not in self._jobs:
                    raise ValueError("找不到仿真作业")
                return json.loads(json.dumps(self._jobs[job_id]))
            jobs = sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return {"jobs": json.loads(json.dumps(jobs[:12])), "active_job": next((job["id"] for job in jobs if job["status"] in {"queued", "running"}), None)}

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError("找不到仿真作业")
            if self._jobs[job_id]["status"] not in {"queued", "running"}:
                raise ValueError("该作业当前不可取消")
            self._cancel_events[job_id].set()
        return self.status(job_id)

    def artifact(self, job_id: str, name: str) -> Path:
        if not re.fullmatch(r"sim-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", job_id):
            raise ValueError("非法作业编号")
        allowed = {"episode.mp4", "replay.json", "trajectory.h5", "trajectory.json", "thumbnail.jpg"}
        if name not in allowed:
            raise ValueError("非法仿真文件")
        path = (self.output_root / job_id / name).resolve()
        if not path.is_relative_to(self.output_root.resolve()) or not path.is_file():
            raise ValueError("仿真文件不存在")
        return path

    def trajectory(self, job_id: str) -> Path:
        return self.artifact(job_id, "trajectory.h5")
