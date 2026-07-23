from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from embodiscope.adapters import load_dataset
from embodiscope.analysis import analyze_episode
from embodiscope.simulation import SCENARIOS, SimulationConfig, _inject_visual_faults, run_simulation, simulation_catalog


def test_simulation_config_rejects_unsafe_values():
    with pytest.raises(ValueError, match="steps"):
        SimulationConfig.from_payload({"steps": 500})
    with pytest.raises(ValueError, match="场景"):
        SimulationConfig.from_payload({"scenario": "unknown"})


def test_simulation_catalog_exposes_cross_domain_scenario_matrix():
    catalog = simulation_catalog()

    assert len(catalog["scenarios"]) == 10
    assert set(SCENARIOS) >= {
        "nominal", "collision", "grasp-slip", "gripper-failure", "actuator-stall",
        "object-perturbation", "sensor-delay", "frame-drop", "camera-occlusion", "compound-failure",
    }
    assert {item["category"] for item in catalog["scenarios"]} == {
        "baseline", "contact", "task", "control", "perception", "compound",
    }
    assert all(item["expected"] and item["recommended_steps"] >= 80 for item in catalog["scenarios"])


def test_visual_fault_injection_marks_dropped_frames():
    frames = [np.full((16, 16, 3), index, dtype=np.uint8) for index in range(30)]
    output, motion, valid, events = _inject_visual_faults(frames, "frame-drop", 20)
    assert len(output) == len(frames)
    assert int((valid == 0).sum()) == 6
    assert np.isnan(motion[valid == 0]).all()
    assert events[0]["type"] == "frame-drop"


def test_visual_fault_injection_supports_occlusion_and_compound_faults():
    frames = [np.full((16, 16, 3), index, dtype=np.uint8) for index in range(40)]

    occluded, _, valid, events = _inject_visual_faults(frames, "camera-occlusion", 20)
    assert int((valid == 0).sum()) == 10
    assert np.max(occluded[len(frames) // 2]) <= 12
    assert [event["type"] for event in events] == ["camera-occlusion"]

    _, _, valid, events = _inject_visual_faults(frames, "compound-failure", 20)
    assert int((valid == 0).sum()) == 6
    assert {event["type"] for event in events} == {"sensor-delay", "frame-drop"}


@pytest.mark.skipif(importlib.util.find_spec("mani_skill") is None, reason="ManiSkill optional runtime is not installed")
def test_real_maniskill_collision_run_is_diagnosable(tmp_path: Path):
    result = run_simulation(
        SimulationConfig(scenario="collision", seed=7, steps=40, record_video=False),
        tmp_path / "simulation",
    )
    trajectory = Path(result["trajectory_path"])
    assert trajectory.is_file()
    assert result["summary"]["peak_force"] > 28
    loaded = load_dataset(trajectory)
    analysis = analyze_episode(loaded.frame, "0")
    assert any(issue["code"] == "FORCE_SPIKE" for issue in analysis["issues"])
    assert loaded.metadata["simulation"]["engine"].startswith("ManiSkill 3")


@pytest.mark.skipif(importlib.util.find_spec("mani_skill") is None, reason="ManiSkill optional runtime is not installed")
def test_real_maniskill_nominal_contact_is_not_a_force_spike(tmp_path: Path):
    result = run_simulation(
        SimulationConfig(scenario="nominal", seed=7, steps=80, record_video=False),
        tmp_path / "nominal",
    )
    loaded = load_dataset(Path(result["trajectory_path"]))
    analysis = analyze_episode(loaded.frame, "0")
    assert result["summary"]["success"] is True
    assert loaded.frame["is_grasped"].any()
    assert loaded.frame["phase"].isin(["approach", "reach", "grasp", "transport"]).all()
    assert result["summary"]["peak_force"] < 35
    assert all(issue["code"] != "FORCE_SPIKE" for issue in analysis["issues"])


@pytest.mark.skipif(importlib.util.find_spec("mani_skill") is None, reason="ManiSkill optional runtime is not installed")
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    (
        ("actuator-stall", "ROBOT_STUCK"),
        ("gripper-failure", "GRIPPER_RESPONSE_FAILURE"),
        ("grasp-slip", "GRASP_SLIP"),
    ),
)
def test_real_maniskill_execution_faults_are_diagnosable(tmp_path: Path, scenario: str, expected_code: str):
    result = run_simulation(
        SimulationConfig(scenario=scenario, seed=7, steps=80, record_video=False),
        tmp_path / scenario,
    )
    loaded = load_dataset(Path(result["trajectory_path"]))
    analysis = analyze_episode(loaded.frame, "0")

    assert any(issue["code"] == expected_code for issue in analysis["issues"])
    assert loaded.metadata["simulation"]["events"]


@pytest.mark.skipif(importlib.util.find_spec("mani_skill") is None, reason="ManiSkill optional runtime is not installed")
def test_real_maniskill_object_perturbation_recovers_closed_loop(tmp_path: Path):
    result = run_simulation(
        SimulationConfig(scenario="object-perturbation", seed=7, steps=90, record_video=False),
        tmp_path / "object-perturbation",
    )
    loaded = load_dataset(Path(result["trajectory_path"]))

    assert result["summary"]["success"] is True
    assert loaded.metadata["simulation"]["events"][0]["type"] == "object-perturbation"


@pytest.mark.skipif(importlib.util.find_spec("mani_skill") is None, reason="ManiSkill optional runtime is not installed")
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    (("sensor-delay", "SENSOR_DESYNC"), ("frame-drop", "FRAME_DROP")),
)
def test_real_maniskill_visual_faults_are_diagnosable(tmp_path: Path, scenario: str, expected_code: str):
    result = run_simulation(
        SimulationConfig(scenario=scenario, seed=7, steps=40, record_video=True),
        tmp_path / scenario,
    )
    replay = Path(result["replay_path"]).read_text(encoding="utf-8")
    assert expected_code in replay
