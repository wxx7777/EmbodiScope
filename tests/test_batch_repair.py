from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pandas as pd

from embodiscope.batch_repair import BatchRepairManager, build_batch_repair_package
from embodiscope.profiles import resolve_profile
from embodiscope.repair import source_sha256


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "demo_pick_place.csv"
DATA = pd.read_csv(DATA_PATH)
SOURCE = {"adapter_id": "csv", "adapter_name": "通用 CSV", "source_format": "CSV"}


def test_batch_repair_package_contains_training_artifacts(tmp_path: Path):
    output = tmp_path / "batch"
    result = build_batch_repair_package(
        DATA,
        DATA_PATH,
        DATA_PATH.name,
        SOURCE,
        source_sha256(DATA_PATH),
        None,
        output,
    )
    cleaned = pd.read_parquet(output / "cleaned.parquet")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(output / "embodiscope-cleaned-dataset.zip") as archive:
        assert set(archive.namelist()) == {"cleaned.parquet", "episode_summary.csv", "manifest.json"}

    assert len(cleaned) == len(DATA)
    assert cleaned["episode_id"].nunique() == 6
    assert {"source_row", "quality_valid", "repair_actions", "repair_reason", "segment_id"}.issubset(cleaned.columns)
    assert result["summary"]["episode_count"] == 6
    assert result["summary"]["retained_rows"] == int(cleaned["quality_valid"].sum())
    assert manifest["provenance"]["source_sha256"] == source_sha256(DATA_PATH)
    assert len(result["package_sha256"]) == 64
    original_force = DATA[DATA["episode_id"] == "EP-003"]["force_z"].reset_index(drop=True)
    cleaned_force = cleaned[cleaned["episode_id"] == "EP-003"]["force_z"].reset_index(drop=True)
    assert cleaned_force.equals(original_force)


def test_batch_repair_manager_finishes_background_job(tmp_path: Path):
    manager = BatchRepairManager(tmp_path)
    subset = DATA[DATA["episode_id"].isin(["EP-001", "EP-005"])].copy()
    job = manager.submit(
        subset,
        DATA_PATH,
        "two-episode.csv",
        SOURCE,
        source_sha256(DATA_PATH),
        resolve_profile(),
    )
    deadline = time.time() + 20
    while job["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.05)
        job = manager.status(job["id"])
    assert job["status"] == "completed", job
    assert job["result"]["summary"]["episode_count"] == 2
    assert manager.artifact(job["id"], "package").is_file()
    assert manager.artifact(job["id"], "parquet").is_file()
    assert manager.artifact(job["id"], "manifest").is_file()
