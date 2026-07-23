from __future__ import annotations

from pathlib import Path
import json
import zipfile

import numpy as np
import pandas as pd

from embodiscope.adapters import adapter_catalog, load_dataset
from embodiscope.analysis import analyze_episode, dataset_overview
from embodiscope.server import DataStore


ROOT = Path(__file__).resolve().parents[1]


def test_lerobot_parquet_adapter_maps_vector_features():
    loaded = load_dataset(ROOT / "data" / "demo_lerobot.parquet")
    assert loaded.adapter_id == "lerobot"
    assert {"joint_1", "joint_6", "action_1", "ee_x", "ee_y", "ee_z"}.issubset(loaded.frame.columns)
    assert dataset_overview(loaded.frame)["episode_count"] == 6
    sync = analyze_episode(loaded.frame, "2")
    assert sync["metrics"]["sync_offset_ms"] == 160.0


def test_binary_upload_uses_adapter_registry(tmp_path):
    store = DataStore(ROOT, ROOT / "data" / "demo_pick_place.csv")
    content = (ROOT / "data" / "demo_lerobot.parquet").read_bytes()
    overview = store.upload("uploaded_lerobot.parquet", content)
    assert overview["adapter_id"] == "lerobot"
    assert overview["source_format"] == "LeRobot Parquet"
    assert overview["episode_count"] == 6
    uploaded = ROOT / "data" / "uploads" / "uploaded_lerobot.parquet"
    uploaded.unlink(missing_ok=True)


def test_adapter_catalog_exposes_open_source_provenance():
    catalog = {adapter["adapter_id"]: adapter for adapter in adapter_catalog()}
    assert {"csv", "lerobot", "rosbag", "maniskill"}.issubset(catalog)
    assert catalog["lerobot"]["license"] == "Apache-2.0"
    assert catalog["rosbag"]["available"] is True
    assert catalog["rosbag"]["license"] == "Apache-2.0 / MIT"
    assert catalog["maniskill"]["license"].startswith("Apache-2.0")


def test_lerobot_directory_ignores_metadata_parquet(tmp_path):
    dataset_root = tmp_path / "lerobot_v3"
    data_dir = dataset_root / "data" / "chunk-000"
    meta_dir = dataset_root / "meta" / "episodes"
    video_dir = dataset_root / "videos" / "chunk-000" / "observation.images.front"
    data_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)
    video_dir.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "episode_index": [0, 0, 0],
            "frame_index": [0, 1, 2],
            "observation.state": [[0.0, 0.1], [0.1, 0.2], [0.2, 0.3]],
            "action": [[0.1, 0.0], [0.1, 0.0], [0.0, 0.0]],
            "observation.ee_pose": [[0.2, 0.0, 0.3], [0.3, 0.0, 0.35], [0.4, 0.0, 0.4]],
            "success": [True, True, True],
        }
    )
    frame.to_parquet(data_dir / "episode_000000.parquet")
    pd.DataFrame({"episode_index": [0], "length": [3]}).to_parquet(meta_dir / "chunk-000.parquet")
    (dataset_root / "meta" / "info.json").write_text(
        json.dumps({"fps": 30, "codebase_version": "v3.0", "features": {"observation.images.front": {"dtype": "video"}}}),
        encoding="utf-8",
    )
    (video_dir / "episode_000000.mp4").write_bytes(b"demo")

    loaded = load_dataset(dataset_root)
    assert loaded.source_format == "LeRobot Dataset v3"
    assert len(loaded.frame) == 3
    assert loaded.metadata["video_files"] == 1
    assert loaded.metadata["visual_features"] == ["observation.images.front"]


def test_zip_upload_loads_lerobot_directory(tmp_path):
    root = tmp_path / "packed"
    data_dir = root / "data" / "chunk-000"
    data_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "episode_index": [0, 0],
            "timestamp": [0.0, 0.05],
            "observation.state": [[0.0], [0.1]],
            "action": [[0.1], [0.0]],
            "success": [True, True],
        }
    ).to_parquet(data_dir / "episode_000000.parquet")
    archive = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for file in root.rglob("*"):
            if file.is_file():
                handle.write(file, Path("lerobot_dataset") / file.relative_to(root))

    store = DataStore(ROOT, ROOT / "data" / "demo_pick_place.csv")
    overview = store.upload("lerobot_dataset.zip", archive.read_bytes())
    assert overview["adapter_id"] == "lerobot"
    assert overview["episode_count"] == 1
    store.reset()


def test_maniskill_hdf5_adapter_reads_demo():
    loaded = load_dataset(ROOT / "data" / "demo_maniskill_collision.h5")
    assert loaded.adapter_id == "maniskill"
    assert loaded.metadata["env_id"] == "PickCube-v1"
    assert {"joint_1", "action_1", "ee_x", "ee_y", "ee_z", "force_z"}.issubset(loaded.frame.columns)
    analysis = analyze_episode(loaded.frame, "0")
    assert any(issue["code"] == "FORCE_SPIKE" for issue in analysis["issues"])


def test_rerun_export_writes_recording(tmp_path):
    from embodiscope.rerun_export import export_episode_recording, rerun_status

    loaded = load_dataset(ROOT / "data" / "demo_pick_place.csv")
    analysis = analyze_episode(loaded.frame, "EP-003")
    output = tmp_path / "episode.rrd"
    assert rerun_status()["available"] is True
    export_episode_recording(loaded.frame, "EP-003", output, "demo_pick_place.csv", analysis)
    assert output.is_file()
    assert output.stat().st_size > 10_000


def test_ros2_mcap_adapter_reads_real_messages(tmp_path):
    from rosbags.rosbag2 import StoragePlugin, Writer
    from rosbags.typesys import Stores, get_typestore

    typestore = get_typestore(Stores.ROS2_HUMBLE)
    types = typestore.types
    Time = types["builtin_interfaces/msg/Time"]
    Header = types["std_msgs/msg/Header"]
    JointState = types["sensor_msgs/msg/JointState"]
    Point = types["geometry_msgs/msg/Point"]
    Quaternion = types["geometry_msgs/msg/Quaternion"]
    Pose = types["geometry_msgs/msg/Pose"]
    PoseStamped = types["geometry_msgs/msg/PoseStamped"]
    Vector3 = types["geometry_msgs/msg/Vector3"]
    Wrench = types["geometry_msgs/msg/Wrench"]
    WrenchStamped = types["geometry_msgs/msg/WrenchStamped"]
    Bool = types["std_msgs/msg/Bool"]

    bag_path = tmp_path / "ros2_mcap_demo"
    with Writer(bag_path, version=9, storage_plugin=StoragePlugin.MCAP) as writer:
        joint_connection = writer.add_connection("/joint_states", "sensor_msgs/msg/JointState", typestore=typestore)
        pose_connection = writer.add_connection("/ee_pose", "geometry_msgs/msg/PoseStamped", typestore=typestore)
        force_connection = writer.add_connection("/wrench", "geometry_msgs/msg/WrenchStamped", typestore=typestore)
        success_connection = writer.add_connection("/task_success", "std_msgs/msg/Bool", typestore=typestore)
        for index in range(80):
            timestamp = 1_000_000_000 + index * 20_000_000
            stamp = Time(sec=timestamp // 1_000_000_000, nanosec=timestamp % 1_000_000_000)
            header = Header(stamp=stamp, frame_id="base")
            phase = index / 79
            joint = JointState(
                header=header,
                name=["shoulder", "elbow"],
                position=np.array([phase, phase * 0.5], dtype=np.float64),
                velocity=np.array([], dtype=np.float64),
                effort=np.array([], dtype=np.float64),
            )
            pose = PoseStamped(
                header=header,
                pose=Pose(
                    position=Point(x=0.2 + phase * 0.2, y=-0.1, z=0.4 + phase * 0.1),
                    orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
            )
            wrench = WrenchStamped(
                header=header,
                wrench=Wrench(
                    force=Vector3(x=0.0, y=0.0, z=2.0),
                    torque=Vector3(x=0.0, y=0.0, z=0.0),
                ),
            )
            writer.write(joint_connection, timestamp, typestore.serialize_cdr(joint, joint.__msgtype__))
            writer.write(pose_connection, timestamp, typestore.serialize_cdr(pose, pose.__msgtype__))
            writer.write(force_connection, timestamp, typestore.serialize_cdr(wrench, wrench.__msgtype__))
        final_timestamp = 1_000_000_000 + 79 * 20_000_000
        success = Bool(data=True)
        writer.write(success_connection, final_timestamp, typestore.serialize_cdr(success, success.__msgtype__))

    loaded = load_dataset(bag_path)
    assert loaded.adapter_id == "rosbag"
    assert loaded.source_format == "ROS bag / MCAP"
    assert {"joint_1", "joint_2", "ee_x", "ee_z", "force_z", "success"}.issubset(loaded.frame.columns)
    assert len(loaded.frame) == 80
    assert loaded.metadata["joint_names"] == ["shoulder", "elbow"]
    assert bool(loaded.frame["success"].iloc[-1]) is True


def test_standalone_mcap_demo_is_web_upload_compatible():
    loaded = load_dataset(ROOT / "data" / "demo_ros2_collision.mcap")
    assert loaded.adapter_id == "rosbag"
    assert loaded.metadata["stream_counts"]["joint"] == 600
    assert loaded.metadata["mcap_summary"]["message_count"] == 4201
    analysis = analyze_episode(loaded.frame, "demo_ros2_collision")
    assert any(issue["code"] == "FORCE_SPIKE" for issue in analysis["issues"])
