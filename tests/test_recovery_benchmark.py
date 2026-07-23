from __future__ import annotations

import json
from pathlib import Path

import pytest

from embodiscope.recovery_benchmark import (
    RecoveryBenchmarkManager,
    aggregate_recovery_benchmark,
    wilson_interval,
)


def _result(scenario: str, seed: int, *, episode_safe: bool) -> dict:
    return {
        "scenario": scenario,
        "scenario_name": scenario,
        "seed": seed,
        "trigger": {
            "trigger_source": "online-predicate-monitor",
            "trigger_step": 25,
            "trigger_type": "physical-test",
        },
        "pair_integrity": {"passed": True, "fingerprint": f"{scenario}-{seed}"},
        "metrics": {
            "recovery_latency": 2.0 + seed / 10,
            "path_overhead": 0.1,
            "operator_completion_rate": 100.0,
        },
        "verdicts": {
            "task_recovery": {"passed": True},
            "episode_safety": {"passed": episode_safe, "observed_peak_force_n": 20.0 if episode_safe else 90.0},
            "post_intervention_safety": {"passed": True, "observed_peak_force_n": 20.0},
        },
    }


def test_wilson_interval_is_bounded_and_non_degenerate():
    interval = wilson_interval(9, 9)

    assert 0.69 < interval["lower"] < 0.71
    assert interval["upper"] == 1.0
    assert wilson_interval(0, 0)["lower"] is None


def test_aggregate_separates_recovery_from_episode_safety():
    scenarios = ["collision", "gripper-failure", "grasp-slip"]
    seeds = [7, 8]
    results = [
        _result(scenario, seed, episode_safe=scenario != "collision")
        for seed in seeds
        for scenario in scenarios
    ]

    excluded = [{"seed": 6, "reason": "fault signature missing"}]
    benchmark = aggregate_recovery_benchmark(
        results,
        seeds=seeds,
        scenarios=scenarios,
        horizon=140,
        excluded_seeds=excluded,
    )

    assert benchmark["summary"]["task_recovery"]["rate"] == 1.0
    assert benchmark["summary"]["episode_safety"]["rate"] == pytest.approx(4 / 6, abs=1e-4)
    assert benchmark["summary"]["post_intervention_safety"]["rate"] == 1.0
    collision = next(item for item in benchmark["per_scenario"] if item["scenario"] == "collision")
    assert collision["task_recovery"]["rate"] == 1.0
    assert collision["episode_safety"]["rate"] == 0.0
    assert benchmark["matrix"][0]["scenarios"]["collision"]["episode_safety"] is False
    assert benchmark["protocol"]["excluded_seeds"] == excluded


def test_manager_rehydrates_completed_benchmark(tmp_path: Path):
    job_id = "recovery-bench-20260722-120000-abcdef"
    result_path = tmp_path / "output" / "recovery-benchmark" / job_id / "result.json"
    result_path.parent.mkdir(parents=True)
    result = {
        "protocol": {"scenarios": ["collision"], "seeds": [7, 8], "horizon": 140},
        "summary": {},
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")

    manager = RecoveryBenchmarkManager(tmp_path)

    assert manager.status(job_id)["status"] == "completed"
    assert manager.result_path(job_id) == result_path.resolve()
