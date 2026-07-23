from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from embodiscope.adapters import load_dataset
from embodiscope.analysis import analyze_episode, timeseries_payload
from embodiscope.dataset_library import DatasetLibrary
from embodiscope.server import DataStore


ROOT = Path(__file__).resolve().parents[1]
PUSHT = ROOT / "data" / "open_source" / "lerobot_pusht"


def test_dataset_library_exposes_multi_source_corpus():
    library = DatasetLibrary(ROOT)
    catalog = library.catalog(ROOT / "data" / "demo_pick_place.csv")
    entries = {item["id"]: item for item in catalog["datasets"]}
    assert catalog["available_count"] == 4
    assert catalog["episode_count"] == 214
    assert catalog["row_count"] == 30450
    assert entries["lerobot-pusht"]["episode_count"] == 206
    assert entries["lerobot-pusht"]["license"] == "MIT"
    assert entries["lerobot-pusht"]["active"] is False


def test_lerobot_pusht_has_rich_semantics_and_video_segments():
    loaded = load_dataset(PUSHT)
    assert len(loaded.frame) == 25650
    assert loaded.frame["episode_id"].nunique() == 206
    assert {"state_1", "state_2", "action_1", "action_2", "reward", "done", "success_known", "task"}.issubset(loaded.frame.columns)
    assert "success" not in loaded.frame.columns
    assert "joint_1" not in loaded.frame.columns
    assert loaded.metadata["dataset_version"] == "v3.0"
    assert loaded.metadata["video_files"] == 1
    assert len(loaded.metadata["video_segments"]) == 206
    assert loaded.metadata["provenance"]["revision"] == "7628202a2180972f291ba1bc6723834921e72c19"

    analysis = analyze_episode(loaded.frame, "0")
    assert analysis["success_known"] is False
    assert analysis["root_causes"] == []
    issue_codes = {item["code"] for item in analysis["issues"]}
    assert "JOINT_JUMP" not in issue_codes
    assert "ROBOT_STUCK" not in issue_codes
    signals = timeseries_payload(loaded.frame, "0")["signals"]
    assert {"state_speed", "state_1", "state_2", "action_1", "action_2", "reward"}.issubset(signals)
    assert "ee_speed" not in signals


def test_open_source_files_match_pinned_checksums():
    manifest = json.loads((PUSHT / "SOURCE.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = PUSHT / relative
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_data_store_switches_library_dataset_and_resolves_episode_video():
    store = DataStore(ROOT, ROOT / "data" / "demo_pick_place.csv")
    overview = store.load_library("lerobot-pusht")
    assert overview["dataset_id"] == "lerobot-pusht"
    assert overview["episode_count"] == 206
    media = store.media("1")
    assert media["path"].name == "file-000.mp4"
    assert media["start"] == 16.1
    assert media["end"] == pytest.approx(27.9)
    assert store.media_payload("1")["url"] == "/api/dataset/video/1"
