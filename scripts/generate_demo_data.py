from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


RATE = 50
DURATION = 12
SAMPLES = RATE * DURATION


def make_episode(episode_id: str, anomaly: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(SAMPLES) / RATE
    phase = np.select(
        [t < 1.2, t < 4.2, t < 6.4, t < 9.5],
        ["approach", "reach", "grasp", "transport"],
        default="place",
    )

    progress = 1 / (1 + np.exp(-(t - 4.8) * 0.75))
    oscillation = 0.018 * np.sin(t * 2.2)
    ee_x = 0.22 + 0.42 * progress + oscillation
    ee_y = -0.18 + 0.32 * progress + 0.012 * np.sin(t * 1.7 + 0.5)
    ee_z = 0.48 - 0.18 * np.exp(-((t - 4.4) ** 2) / 1.5) + 0.17 * progress
    ee_x += rng.normal(0, 0.00015, SAMPLES)
    ee_y += rng.normal(0, 0.00015, SAMPLES)
    ee_z += rng.normal(0, 0.00012, SAMPLES)

    joints = {}
    for index in range(1, 7):
        joints[f"joint_{index}"] = (
            0.25 * np.sin(t * (0.22 + index * 0.025) + index * 0.5)
            + progress * (index - 3.5) * 0.08
            + rng.normal(0, 0.0015, SAMPLES)
        )

    gripper = np.ones(SAMPLES)
    gripper[(t >= 4.6) & (t < 9.7)] = 0.18
    gripper[t >= 9.7] = 0.92
    object_distance = np.full(SAMPLES, 0.16)
    object_distance[(t >= 4.1) & (t < 4.6)] = np.linspace(0.16, 0.025, np.sum((t >= 4.1) & (t < 4.6)))
    object_distance[(t >= 4.6) & (t < 9.7)] = 0.025 + rng.normal(0, 0.001, np.sum((t >= 4.6) & (t < 9.7)))
    object_distance[t >= 9.7] = 0.14
    force_z = 2.0 + 1.1 * np.sin(t * 1.4) + rng.normal(0, 0.28, SAMPLES)
    force_z[(t >= 4.5) & (t < 4.9)] += 6.5

    timestamps = t.copy()
    success = anomaly in {"clean", "sync"}

    if anomaly == "collision":
        hit = (t >= 7.18) & (t <= 7.28)
        force_z[hit] = 44 + 6 * np.sin(np.linspace(0, np.pi, hit.sum()))
        ee_x[t >= 7.25] -= 0.08
    elif anomaly == "stuck":
        stalled = (t >= 5.3) & (t <= 8.0)
        for signal in (ee_x, ee_y, ee_z):
            signal[stalled] = signal[np.flatnonzero(stalled)[0]]
        for key in joints:
            joints[key][stalled] = joints[key][np.flatnonzero(stalled)[0]]
    elif anomaly == "jump_gap":
        joints["joint_3"][318] += 1.7
        joints["joint_5"][405] -= 1.35
        timestamps[260:] += 0.22
    elif anomaly == "slip_missing":
        slipping = (t >= 7.1) & (t < 8.8)
        object_distance[slipping] = np.linspace(0.03, 0.21, slipping.sum())
        object_distance[t >= 8.8] = 0.2

    position = np.column_stack([ee_x, ee_y, ee_z])
    speed = np.linalg.norm(np.diff(position, axis=0, prepend=position[[0]]), axis=1) * RATE
    lag_samples = 8 if anomaly == "sync" else 0
    camera_motion = np.roll(speed, lag_samples) + rng.normal(0, 0.001, SAMPLES)
    if lag_samples:
        camera_motion[:lag_samples] = camera_motion[lag_samples]

    data = pd.DataFrame({
        "timestamp": timestamps,
        "episode_id": episode_id,
        "phase": phase,
        **joints,
        "ee_x": ee_x,
        "ee_y": ee_y,
        "ee_z": ee_z,
        "camera_motion": camera_motion,
        "force_z": force_z,
        "gripper": gripper,
        "object_distance": object_distance,
        "success": success,
    })
    if anomaly == "slip_missing":
        data.loc[180:195, "joint_2"] = np.nan
        data.loc[420:428, "camera_motion"] = np.nan
    return data


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    scenarios = [
        ("EP-001", "clean", 11),
        ("EP-002", "sync", 22),
        ("EP-003", "collision", 33),
        ("EP-004", "stuck", 44),
        ("EP-005", "jump_gap", 55),
        ("EP-006", "slip_missing", 66),
    ]
    frame = pd.concat([make_episode(*scenario) for scenario in scenarios], ignore_index=True)
    destination = project_root / "data" / "demo_pick_place.csv"
    frame.to_csv(destination, index=False, float_format="%.6f")
    print(f"生成 {len(frame)} 行演示数据 -> {destination}")

    lerobot = pd.DataFrame({
        "timestamp": frame["timestamp"],
        "episode_index": frame["episode_id"].str.extract(r"(\d+)")[0].astype(int),
        "frame_index": frame.groupby("episode_id").cumcount(),
        "observation.state": frame[[f"joint_{index}" for index in range(1, 7)]].apply(
            lambda row: [float(value) for value in row], axis=1
        ),
        "action": frame[[f"joint_{index}" for index in range(1, 7)]].apply(
            lambda row: [float(value) for value in row], axis=1
        ),
        "observation.ee_pose": frame[["ee_x", "ee_y", "ee_z"]].apply(
            lambda row: [float(value) for value in row], axis=1
        ),
        "observation.gripper": frame["gripper"],
        "observation.force_z": frame["force_z"],
        "observation.camera_motion": frame["camera_motion"],
        "observation.object_distance": frame["object_distance"],
        "task_phase": frame["phase"],
        "success": frame["success"],
    })
    lerobot_destination = project_root / "data" / "demo_lerobot.parquet"
    lerobot.to_parquet(lerobot_destination, index=False)
    print(f"生成 LeRobot 风格 Parquet -> {lerobot_destination}")


if __name__ == "__main__":
    main()
