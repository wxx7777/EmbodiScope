from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisProfile:
    profile_id: str
    name: str
    description: str
    robot: str
    joint_velocity_mad_scale: float = 7.0
    joint_velocity_floor: float = 3.5
    stuck_speed_threshold: float = 0.004
    stuck_min_duration: float = 1.2
    sync_max_lag_seconds: float = 0.5
    sync_warning_seconds: float = 0.08
    sync_critical_seconds: float = 0.15
    sync_min_confidence: float = 0.35
    force_mad_scale: float = 8.0
    force_floor: float = 35.0
    gap_period_multiplier: float = 2.5
    gap_extra_seconds: float = 0.04
    frame_drop_min_duration: float = 0.04
    slip_gripper_closed: float = 0.3
    slip_distance_threshold: float = 0.1
    slip_step_threshold: float = 0.002
    workspace_x: tuple[float, float] = (-1.2, 1.2)
    workspace_y: tuple[float, float] = (-1.2, 1.2)
    workspace_z: tuple[float, float] = (-0.05, 1.8)

    @property
    def workspace_bounds(self) -> dict[str, tuple[float, float]]:
        return {"ee_x": self.workspace_x, "ee_y": self.workspace_y, "ee_z": self.workspace_z}

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["workspace_bounds"] = {key: list(value) for key, value in self.workspace_bounds.items()}
        return payload


PROFILES = {
    "generic-manipulator": AnalysisProfile(
        profile_id="generic-manipulator",
        name="通用操作机器人",
        description="面向未知机器人日志的保守默认值，统计阈值与物理下限联合生效。",
        robot="Generic manipulator",
    ),
    "franka-panda": AnalysisProfile(
        profile_id="franka-panda",
        name="Franka Panda 实验配置",
        description="针对 Panda 操作任务收紧工作空间，并保留真实接触的安全余量。",
        robot="Franka Emika Panda",
        joint_velocity_floor=3.0,
        stuck_speed_threshold=0.005,
        workspace_x=(-0.9, 0.9),
        workspace_y=(-0.9, 0.9),
        workspace_z=(-0.05, 1.25),
    ),
}


def resolve_profile(profile: str | AnalysisProfile | None = None) -> AnalysisProfile:
    if isinstance(profile, AnalysisProfile):
        return profile
    profile_id = str(profile or "generic-manipulator")
    if profile_id not in PROFILES:
        raise ValueError(f"未知诊断配置: {profile_id}")
    return PROFILES[profile_id]


def profile_catalog() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in PROFILES.values()]
