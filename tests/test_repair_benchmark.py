from __future__ import annotations

from pathlib import Path

import pandas as pd

from embodiscope.repair_benchmark import run_repair_benchmark


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "demo_pick_place.csv"
DATA = pd.read_csv(DATA_PATH)


def test_repair_benchmark_passes_all_quality_gates():
    result = run_repair_benchmark(DATA, DATA_PATH, seed_count=2)
    metrics = result["metrics"]
    assert result["status"] == "passed"
    assert result["protocol"]["sample_count"] == 32
    assert len(result["matrix"]) == 15
    assert metrics["repair_success_rate"] == 1.0
    assert metrics["reconstruction_rmse"] < 0.01
    assert metrics["sync_residual_mae_ms"] <= 20.0
    assert metrics["nominal_overcorrection_rate"] == 0.0
    assert metrics["nominal_false_quarantine_rate"] == 0.0
    assert metrics["risk_isolation_recall"] == 1.0
    assert metrics["physical_measurement_preservation"] == 1.0
    assert metrics["segmentation_recall"] == 1.0
    assert all(gate["passed"] for gate in result["quality_gates"])


def test_repair_benchmark_reports_each_governance_mode():
    result = run_repair_benchmark(DATA, DATA_PATH, seed_count=2)
    classes = {item["repair_id"]: item for item in result["per_class"]}
    assert set(classes) == {"short-gap", "joint-spike", "sensor-delay", "timestamp-gap", "force-spike"}
    assert classes["short-gap"]["reconstruction_rmse"] is not None
    assert classes["joint-spike"]["reconstruction_rmse"] is not None
    assert classes["force-spike"]["preservation_rate"] == 1.0
    assert classes["timestamp-gap"]["isolation_recall"] == 1.0
