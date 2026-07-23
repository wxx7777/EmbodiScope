from pathlib import Path

import numpy as np
import pandas as pd

from embodiscope.analysis import analyze_episode
from embodiscope.benchmark import run_benchmark
from embodiscope.profiles import profile_catalog, resolve_profile
from embodiscope.server import DataStore


ROOT = Path(__file__).resolve().parents[1]


def _joint_ramp(sample_rate: int) -> pd.DataFrame:
    timestamps = np.arange(sample_rate * 3, dtype=float) / sample_rate
    return pd.DataFrame({
        "timestamp": timestamps,
        "episode_id": "ramp",
        "phase": "transport",
        "joint_1": timestamps * 0.1,
        "success": True,
    })


def test_joint_speed_is_invariant_to_sample_rate():
    slow = analyze_episode(_joint_ramp(10))
    fast = analyze_episode(_joint_ramp(100))
    assert all(issue["code"] != "ROBOT_STUCK" for issue in slow["issues"])
    assert all(issue["code"] != "ROBOT_STUCK" for issue in fast["issues"])
    assert slow["scores"]["motion"] == fast["scores"]["motion"]


def test_profile_catalog_exposes_threshold_evidence():
    catalog = {item["profile_id"]: item for item in profile_catalog()}
    assert {"generic-manipulator", "franka-panda"}.issubset(catalog)
    assert catalog["franka-panda"]["force_floor"] == 35.0
    assert resolve_profile("franka-panda").workspace_z[1] == 1.25


def test_faultbench_reports_statistical_metrics():
    source = pd.read_csv(ROOT / "data" / "demo_pick_place.csv")
    result = run_benchmark(source, "generic-manipulator", seed_count=2)
    assert result["protocol"]["sample_count"] == 44
    assert result["protocol"]["fault_classes"] == 7
    assert result["metrics"]["macro_f1"] >= 0.85
    assert result["metrics"]["macro_f1"] >= result["baseline"]["macro_f1"]
    assert result["metrics"]["nominal_false_positive_rate"] <= 0.1
    assert result["performance"]["sync_offset_mae_ms"] <= 30
    assert len(result["matrix"]) == 21


def test_data_store_applies_profile_and_caches_benchmark():
    store = DataStore(ROOT, ROOT / "data" / "demo_pick_place.csv")
    overview = store.set_profile("franka-panda")
    assert overview["analysis_profile"]["profile_id"] == "franka-panda"
    first = store.benchmark(seed_count=2)
    second = store.benchmark(seed_count=2)
    assert first is second
    assert first["protocol"]["profile"]["profile_id"] == "franka-panda"
