from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = pd.read_csv(ROOT / "data" / "demo_pick_place.csv")
    episode = source[source["episode_id"] == "EP-003"].reset_index(drop=True)
    output = ROOT / "data" / "demo_maniskill_collision.h5"

    qpos_columns = [column for column in episode.columns if column.startswith("joint_")]
    qpos = episode[qpos_columns].to_numpy(dtype=np.float32)
    action = np.column_stack(
        [
            np.gradient(qpos, axis=0),
            episode["gripper"].to_numpy(dtype=np.float32),
        ]
    ).astype(np.float32)
    tcp_pose = np.column_stack(
        [
            episode[["ee_x", "ee_y", "ee_z"]].to_numpy(dtype=np.float32),
            np.zeros((len(episode), 3), dtype=np.float32),
            np.ones(len(episode), dtype=np.float32),
        ]
    )
    goal_pos = np.tile(np.array([0.55, 0.08, 0.48], dtype=np.float32), (len(episode), 1))
    force = np.column_stack(
        [
            np.zeros(len(episode), dtype=np.float32),
            np.zeros(len(episode), dtype=np.float32),
            episode["force_z"].fillna(0).to_numpy(dtype=np.float32),
        ]
    )

    with h5py.File(output, "w") as handle:
        trajectory = handle.create_group("traj_0")
        trajectory.create_dataset("actions", data=action, compression="gzip")
        trajectory.create_dataset("timestamp", data=episode["timestamp"].to_numpy(dtype=np.float64))
        trajectory.create_dataset("success", data=np.array([False], dtype=np.bool_))
        observation = trajectory.create_group("obs")
        agent = observation.create_group("agent")
        agent.create_dataset("qpos", data=qpos, compression="gzip")
        extra = observation.create_group("extra")
        extra.create_dataset("tcp_pose", data=tcp_pose, compression="gzip")
        extra.create_dataset("goal_pos", data=goal_pos, compression="gzip")
        extra.create_dataset("force", data=force, compression="gzip")

    metadata = {
        "env_info": {"env_id": "PickCube-v1", "fps": 50},
        "fps": 50,
        "episodes": [
            {
                "episode_id": 0,
                "episode_seed": 20260712,
                "control_mode": "pd_ee_delta_pose",
                "success": False,
                "failure_mode": "collision_force_spike",
            }
        ],
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {output.name}: {len(episode)} samples")


if __name__ == "__main__":
    main()
