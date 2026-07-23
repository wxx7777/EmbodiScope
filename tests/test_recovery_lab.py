from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from embodiscope.recovery_lab import (
    RECOVERY_PROTOCOLS,
    RecoveryManager,
    build_recovery_result,
    recovery_catalog,
)
from embodiscope.simulation import SimulationConfig, run_simulation


ROOT = Path(__file__).resolve().parents[1]


def test_recovery_catalog_exposes_paired_protocol():
    catalog = recovery_catalog()

    assert catalog["protocol"]["variants"] == ["failure", "recovered"]
    assert set(RECOVERY_PROTOCOLS) == {"collision", "gripper-failure", "grasp-slip"}
    assert all(item["predicate"] in {"collision_free", "object_attached"} for item in catalog["scenarios"])


def test_recovery_manager_rejects_unsupported_or_short_experiments(tmp_path: Path):
    manager = RecoveryManager(tmp_path)

    with pytest.raises(ValueError, match="当前支持"):
        manager.submit({"scenario": "sensor-delay", "horizon": 140})
    with pytest.raises(ValueError, match="horizon"):
        manager.submit({"scenario": "collision", "horizon": 80})


def test_recovery_manager_rehydrates_completed_results(tmp_path: Path):
    job_id = "recovery-20260721-120000-abcdef"
    result_path = tmp_path / "output" / "recovery" / job_id / "result.json"
    result_path.parent.mkdir(parents=True)
    result = {"scenario": "collision", "seed": 7, "horizon": 140, "passed": True}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    thumbnail = result_path.parent / "failure" / "thumbnail.jpg"
    thumbnail.parent.mkdir()
    thumbnail.write_bytes(b"thumbnail")

    manager = RecoveryManager(tmp_path)

    restored = manager.status(job_id)
    assert restored["status"] == "completed"
    assert restored["message"] == "已恢复历史配对实验"
    assert restored["result"] == result
    assert manager.artifact(job_id, "failure", "thumbnail.jpg") == thumbnail.resolve()
    with pytest.raises(ValueError, match="非法恢复实验文件"):
        manager.artifact(job_id, "failure", "unexpected.txt")


@pytest.mark.skipif(importlib.util.find_spec("mani_skill") is None, reason="ManiSkill optional runtime is not installed")
@pytest.mark.parametrize("scenario", ["collision", "gripper-failure", "grasp-slip"])
def test_real_recovery_protocol_restores_task_and_predicate(tmp_path: Path, scenario: str):
    failure = run_simulation(
        SimulationConfig(scenario=scenario, seed=7, steps=140, record_video=False),
        tmp_path / scenario / "failure",
    )
    recovered = run_simulation(
        SimulationConfig(scenario=scenario, seed=7, steps=140, record_video=False, recovery_enabled=True),
        tmp_path / scenario / "recovered",
    )
    failure_replay = json.loads(Path(failure["replay_path"]).read_text(encoding="utf-8"))
    recovered_replay = json.loads(Path(recovered["replay_path"]).read_text(encoding="utf-8"))

    result = build_recovery_result(scenario, 7, 140, failure_replay, recovered_replay)

    assert result["failure"]["success"] is False
    assert result["recovered"]["success"] is True
    assert result["metrics"]["success_delta"] == 1
    assert result["metrics"]["predicate_restored"] is True
    assert result["metrics"]["operator_completion_rate"] == 100.0
    assert result["verdicts"]["task_recovery"]["passed"] is True
    assert result["verdicts"]["post_intervention_safety"]["passed"] is True
    assert result["passed"] is True
    assert all(step["status"] == "completed" for step in result["plan"])
    assert result["trigger"]["trigger_source"] == "online-predicate-monitor"
    assert result["trigger"]["trigger_step"] <= {"collision": 26, "gripper-failure": 38, "grasp-slip": 53}[scenario]
    assert [event["type"] for event in recovered_replay["events"] if event["type"] in {
        "predicate-violated", "recovery-start", "predicate-restored", "recovery-success"
    }] == ["predicate-violated", "recovery-start", "predicate-restored", "recovery-success"]
    assert result["pair_integrity"]["passed"] is True
    assert {gate["key"] for gate in result["quality_gates"]} >= {"pair_integrity", "failure_control"}
    operator_times = [step["completed_at"] for step in result["plan"]]
    assert operator_times == sorted(operator_times)

    if scenario == "collision":
        assert result["verdicts"]["episode_safety"]["passed"] is False
        assert result["verdicts"]["episode_safety"]["observed_peak_force_n"] > 36.0
        assert result["metrics"]["task_recovery"] is True
        assert result["metrics"]["episode_safety"] is False
        assert result["metrics"]["post_intervention_safety"] is True
        mismatched = copy.deepcopy(recovered_replay)
        mismatched["config"]["seed"] = 999
        invalid_result = build_recovery_result(scenario, 7, 140, failure_replay, mismatched)
        assert invalid_result["pair_integrity"]["passed"] is False
        assert invalid_result["passed"] is False
