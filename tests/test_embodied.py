from pathlib import Path

import numpy as np
import pandas as pd

from embodiscope.embodied import embodied_overview


ROOT = Path(__file__).resolve().parents[1]


def test_demo_dataset_exposes_closed_loop_gaps():
    frame = pd.read_csv(ROOT / "data" / "demo_pick_place.csv")
    result = embodied_overview(frame)

    assert result["dataset"]["episode_count"] == 6
    assert result["dataset"]["row_count"] == 3600
    assert result["status"] == "review"
    assert result["status_counts"]["review"] + result["status_counts"]["blocked"] == 6
    assert any(item["code"] == "ACTION_PROXY" for item in result["top_blockers"])
    assert all(item["action_source"] == "proxy" for item in result["episodes"])
    assert all(item["metrics"]["action_state_correlation"] is None for item in result["episodes"])
    assert all(item["metrics"]["response_lag_ms"] is None for item in result["episodes"])
    assert set(result["dimension_averages"]) == {
        "observability", "controllability", "temporal_grounding", "contact_grounding", "behavior_diversity",
    }


def test_explicit_action_channels_are_measured_separately_from_proxy():
    timestamps = np.arange(80, dtype=float) * 0.02
    action = np.sin(np.arange(80, dtype=float) * 0.18) * 0.6
    state = np.cumsum(action) * 0.02
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "episode_id": "episode-1",
        "joint_1": state,
        "action_1": action,
        "phase": np.where(np.arange(80) < 40, "reach", "place"),
        "gripper": np.where(np.arange(80) < 40, 1.0, 0.2),
        "object_distance": np.where(np.arange(80) < 40, 0.16, 0.03),
        "force_z": np.where(np.arange(80) < 40, 0.2, 2.0),
        "success": True,
    })

    result = embodied_overview(frame)["episodes"][0]

    assert result["action_source"] == "explicit"
    assert result["metrics"]["action_channels"] == 1
    assert result["metrics"]["action_out_of_bounds_rate"] == 0.0
    assert result["metrics"]["phase_count"] == 2
    assert result["metrics"]["action_state_correlation"] > 0.5
