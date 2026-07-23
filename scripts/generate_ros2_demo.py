from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from rosbags.rosbag2 import StoragePlugin, Writer
from rosbags.typesys import Stores, get_typestore


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = pd.read_csv(project_root / "data" / "demo_pick_place.csv")
    source = source[source["episode_id"] == "EP-003"].reset_index(drop=True)
    bag_path = project_root / "data" / "demo_ros2_mcap"
    standalone_path = project_root / "data" / "demo_ros2_collision.mcap"
    if bag_path.exists():
        shutil.rmtree(bag_path)
    standalone_path.unlink(missing_ok=True)

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
    Float32 = types["std_msgs/msg/Float32"]
    String = types["std_msgs/msg/String"]
    Bool = types["std_msgs/msg/Bool"]

    with Writer(bag_path, version=9, storage_plugin=StoragePlugin.MCAP) as writer:
        connections = {
            "joint": writer.add_connection("/joint_states", "sensor_msgs/msg/JointState", typestore=typestore),
            "pose": writer.add_connection("/ee_pose", "geometry_msgs/msg/PoseStamped", typestore=typestore),
            "wrench": writer.add_connection("/wrench", "geometry_msgs/msg/WrenchStamped", typestore=typestore),
            "camera": writer.add_connection("/camera_motion", "std_msgs/msg/Float32", typestore=typestore),
            "gripper": writer.add_connection("/gripper", "std_msgs/msg/Float32", typestore=typestore),
            "distance": writer.add_connection("/object_distance", "std_msgs/msg/Float32", typestore=typestore),
            "phase": writer.add_connection("/task_phase", "std_msgs/msg/String", typestore=typestore),
            "success": writer.add_connection("/task_success", "std_msgs/msg/Bool", typestore=typestore),
        }
        start_ns = 1_000_000_000
        joint_names = [f"joint_{index}" for index in range(1, 7)]
        for _, row in source.iterrows():
            timestamp = start_ns + int(float(row["timestamp"]) * 1e9)
            stamp = Time(sec=timestamp // 1_000_000_000, nanosec=timestamp % 1_000_000_000)
            header = Header(stamp=stamp, frame_id="base")
            messages = {
                "joint": JointState(
                    header=header,
                    name=joint_names,
                    position=np.array([row[name] for name in joint_names], dtype=np.float64),
                    velocity=np.array([], dtype=np.float64),
                    effort=np.array([], dtype=np.float64),
                ),
                "pose": PoseStamped(
                    header=header,
                    pose=Pose(
                        position=Point(x=float(row["ee_x"]), y=float(row["ee_y"]), z=float(row["ee_z"])),
                        orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                ),
                "wrench": WrenchStamped(
                    header=header,
                    wrench=Wrench(
                        force=Vector3(x=0.0, y=0.0, z=float(row["force_z"])),
                        torque=Vector3(x=0.0, y=0.0, z=0.0),
                    ),
                ),
                "camera": Float32(data=float(row["camera_motion"])),
                "gripper": Float32(data=float(row["gripper"])),
                "distance": Float32(data=float(row["object_distance"])),
                "phase": String(data=str(row["phase"])),
            }
            for name, message in messages.items():
                writer.write(connections[name], timestamp, typestore.serialize_cdr(message, message.__msgtype__))
        success = Bool(data=False)
        final_timestamp = start_ns + int(float(source["timestamp"].iloc[-1]) * 1e9)
        writer.write(connections["success"], final_timestamp, typestore.serialize_cdr(success, success.__msgtype__))

    mcap_file = next(bag_path.glob("*.mcap"))
    shutil.copy2(mcap_file, standalone_path)
    print(f"生成 ROS2 MCAP bag -> {bag_path}")
    print(f"生成可上传 MCAP -> {standalone_path}")


if __name__ == "__main__":
    main()
