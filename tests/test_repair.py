from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from embodiscope.repair import build_repair_artifact, source_sha256
from embodiscope.server import DataStore


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "demo_pick_place.csv"
DATA = pd.read_csv(DATA_PATH)
SOURCE = {"adapter_id": "csv", "adapter_name": "通用 CSV", "source_format": "CSV"}


def repair(episode_id: str):
    return build_repair_artifact(DATA, episode_id, DATA_PATH, DATA_PATH.name, SOURCE)


def test_sync_repair_is_traceable_and_resolves_detected_offset():
    artifact = repair("EP-002")
    payload = artifact.payload
    assert "SENSOR_DESYNC" in payload["issue_resolution"]["resolved"]
    assert payload["summary"]["sync_corrected_rows"] > 500
    assert payload["summary"]["quarantined_rows"] > 0
    assert "camera_motion__original" in artifact.cleaned.columns
    assert artifact.cleaned.loc[artifact.cleaned["quality_valid"], "camera_motion"].notna().all()
    assert payload["provenance"]["source_sha256"] == source_sha256(DATA_PATH)
    assert payload["provenance"]["artifact_sha256"] == hashlib.sha256(artifact.csv_bytes).hexdigest()


def test_isolated_joint_jump_is_repaired_without_filling_timestamp_gap():
    artifact = repair("EP-005")
    payload = artifact.payload
    assert payload["summary"]["joint_jump_cells"] >= 1
    assert payload["summary"]["segment_count"] == 2
    assert "JOINT_JUMP" in payload["issue_resolution"]["resolved"]
    assert "TIMESTAMP_GAP" in payload["issue_resolution"]["unresolved"]
    assert any(column.endswith("__original") for column in artifact.cleaned.columns)
    assert set(artifact.cleaned["segment_id"]) == {0, 1}


def test_physical_faults_are_quarantined_but_measurements_are_preserved():
    artifact = repair("EP-003")
    payload = artifact.payload
    assert payload["summary"]["quarantined_rows"] > 0
    assert "FORCE_SPIKE" in payload["issue_resolution"]["quarantined"]
    assert "FORCE_SPIKE" in payload["issue_resolution"]["unresolved"]
    assert artifact.cleaned["force_z"].equals(DATA[DATA["episode_id"] == "EP-003"]["force_z"].reset_index(drop=True))
    force_rows = artifact.cleaned["repair_actions"].str.contains("FORCE_SPIKE")
    assert force_rows.any()
    assert not artifact.cleaned.loc[force_rows, "quality_valid"].any()


def test_short_bounded_missing_gap_is_interpolated_with_original_backup():
    frame = pd.DataFrame({
        "timestamp": [0.0, 0.1, 0.2, 0.3, 0.4],
        "episode_id": ["short-gap"] * 5,
        "ee_x": [0.0, 0.1, float("nan"), 0.3, 0.4],
        "ee_y": [0.0, 0.1, 0.2, 0.3, 0.4],
        "success": [True] * 5,
    })
    artifact = build_repair_artifact(frame, "short-gap", DATA_PATH, "synthetic.csv", SOURCE)
    assert artifact.payload["summary"]["interpolated_cells"] == 1
    assert artifact.cleaned.at[2, "ee_x"] == 0.2
    assert pd.isna(artifact.cleaned.at[2, "ee_x__original"])
    assert artifact.cleaned.at[2, "quality_valid"]
    assert "MISSING_VALUES" in artifact.cleaned.at[2, "repair_actions"]


def test_long_missing_gap_is_not_invented_and_audit_export_has_provenance():
    artifact = repair("EP-006")
    assert artifact.payload["summary"]["interpolated_cells"] == 0
    original = DATA[DATA["episode_id"] == "EP-006"].reset_index(drop=True)
    assert artifact.cleaned["camera_motion"].isna().sum() == original["camera_motion"].isna().sum()
    assert artifact.cleaned["force_z"].isna().sum() == original["force_z"].isna().sum()
    assert "MISSING_VALUES_UNRESOLVED" in artifact.payload["issue_resolution"]["quarantined"]

    store = DataStore(ROOT, DATA_PATH)
    audit = store.audit()
    assert audit["downloads"] == {"json": "/api/audit.json", "csv": "/api/audit.csv"}
    assert len(audit["provenance"]["source_sha256"]) == 64
    csv_bytes = store.audit_csv()
    assert csv_bytes.startswith(b"\xef\xbb\xbfepisode_id,")
    assert b"source_sha256" in csv_bytes.splitlines()[0]
