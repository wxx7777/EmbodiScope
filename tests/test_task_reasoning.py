from pathlib import Path

import pandas as pd

from embodiscope.task_reasoning import task_reasoning_overview
from embodiscope.server import DataStore


ROOT = Path(__file__).resolve().parents[1]


def test_demo_task_graph_finds_first_violated_invariant_and_recovery():
    frame = pd.read_csv(ROOT / "data" / "demo_pick_place.csv")
    result = task_reasoning_overview(frame)
    episodes = {item["episode_id"]: item for item in result["episodes"]}

    assert result["dataset"]["task_template"] == "pick-and-place-v1"
    assert result["status_counts"] == {
        "verified": 1,
        "degraded": 1,
        "recoverable": 4,
        "blocked": 0,
    }
    assert episodes["EP-001"]["status"] == "verified"
    assert all(step["status"] == "completed" for step in episodes["EP-001"]["trace"])

    collision = episodes["EP-003"]
    assert collision["first_violation"]["predicate"] == "collision_free"
    assert collision["first_violation"]["skill"] == "transport"
    assert any(step["operator"] == "ReplanCollisionFreePath" for step in collision["recovery_plan"])

    slip = episodes["EP-006"]
    assert slip["first_violation"]["predicate"] == "object_attached"
    assert any(step["operator"] == "CloseAndVerifyAttachment" for step in slip["recovery_plan"])


def test_task_reasoning_preserves_unknown_when_physical_evidence_is_missing():
    frame = pd.DataFrame({
        "timestamp": [0.0, 0.1, 0.2, 0.3],
        "episode_id": ["episode-1"] * 4,
        "phase": ["approach", "reach", "grasp", "place"],
        "success": [False] * 4,
    })

    episode = task_reasoning_overview(frame)["episodes"][0]
    predicate_states = [
        predicate["state"]
        for step in episode["trace"]
        for predicate in step["preconditions"] + step["effects"]
    ]

    assert episode["status"] == "blocked"
    assert episode["grounding_coverage"] < 50
    assert "unknown" in predicate_states


def test_data_store_exposes_task_reasoning_with_dataset_provenance():
    store = DataStore(ROOT, ROOT / "data" / "demo_pick_place.csv")

    result = store.task_reasoning()

    assert result["dataset_name"] == "demo_pick_place.csv"
    assert result["dataset_id"] == "embodiscope-benchmark"
    assert result["source"]["adapter_id"] == "csv"
