from pathlib import Path

import numpy as np
import pandas as pd

from embodiscope.analysis import analyze_episode, dataset_overview, estimate_sync_offset


ROOT = Path(__file__).resolve().parents[1]
DATA = pd.read_csv(ROOT / "data" / "demo_pick_place.csv")


def issue_codes(episode_id: str) -> set[str]:
    return {issue["code"] for issue in analyze_episode(DATA, episode_id)["issues"]}


def test_dataset_overview_contains_all_scenarios():
    overview = dataset_overview(DATA)
    assert overview["episode_count"] == 6
    assert overview["row_count"] == 3600
    assert overview["critical_episodes"] >= 4
    assert sum(overview["grade_distribution"].values()) == 6
    assert sum(item["count"] for item in overview["score_histogram"]) == 6
    assert set(overview["average_dimension_scores"]) == {"completeness", "temporal", "motion", "sync", "safety"}
    assert {item["code"] for item in overview["issue_code_counts"]}.issuperset({"SENSOR_DESYNC", "FORCE_SPIKE", "JOINT_JUMP"})
    assert all("issue_codes" in episode and "scores" in episode for episode in overview["episodes"])


def test_clean_episode_scores_high():
    analysis = analyze_episode(DATA, "EP-001")
    assert analysis["success"] is True
    assert analysis["quality_score"] >= 90
    assert not any(issue["severity"] == "critical" for issue in analysis["issues"])


def test_sync_offset_is_detected():
    analysis = analyze_episode(DATA, "EP-002")
    assert "SENSOR_DESYNC" in issue_codes("EP-002")
    assert 120 <= analysis["metrics"]["sync_offset_ms"] <= 200


def test_failure_modes_are_classified():
    expected = {
        "EP-003": {"FORCE_SPIKE"},
        "EP-004": {"ROBOT_STUCK"},
        "EP-005": {"JOINT_JUMP", "TIMESTAMP_GAP"},
        "EP-006": {"GRASP_SLIP", "MISSING_VALUES"},
    }
    for episode_id, codes in expected.items():
        analysis = analyze_episode(DATA, episode_id)
        assert codes.issubset({issue["code"] for issue in analysis["issues"]})
        assert analysis["quality_score"] < 80


def test_offset_estimator_sign_convention():
    robot = pd.Series([0.0] * 20 + [1.0, 3.0, 1.0] + [0.0] * 30).to_numpy()
    sensor = pd.Series([0.0] * 24 + [1.0, 3.0, 1.0] + [0.0] * 26).to_numpy()
    offset, confidence = estimate_sync_offset(robot, sensor, 0.02)
    assert round(offset, 2) == 0.08
    assert confidence > 0.9


def test_gripper_command_feedback_mismatch_is_detected():
    rows = 36
    frame = pd.DataFrame({
        "timestamp": np.arange(rows) * 0.05,
        "episode_id": ["gripper-failure"] * rows,
        "joint_1": np.linspace(0.0, 0.2, rows),
        "gripper_command": [-1.0] * rows,
        "gripper": [1.0] * rows,
        "is_grasped": [False] * rows,
        "object_distance": [0.2] * rows,
        "success": [False] * rows,
        "phase": ["grasp"] * rows,
    })

    analysis = analyze_episode(frame, "gripper-failure")

    assert any(issue["code"] == "GRIPPER_RESPONSE_FAILURE" for issue in analysis["issues"])
    assert analysis["root_causes"][0]["label"] == "夹爪执行失败"
