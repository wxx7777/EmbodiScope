from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..analysis import validate_dataframe
from .base import AdapterInfo, LoadedDataset


def _attr(value: Any, path: str, default: Any = None) -> Any:
    current = value
    for part in path.split("."):
        if not hasattr(current, part):
            return default
        current = getattr(current, part)
    return current


def _image_gray(message: Any) -> np.ndarray | None:
    try:
        import cv2
    except ImportError:
        return None
    msgtype = getattr(message, "__msgtype__", "")
    data = np.asarray(getattr(message, "data", []), dtype=np.uint8)
    if not data.size:
        return None
    if msgtype.endswith("CompressedImage"):
        return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    height = int(getattr(message, "height", 0))
    width = int(getattr(message, "width", 0))
    encoding = str(getattr(message, "encoding", "")).lower()
    if not height or not width:
        return None
    channels = 1 if "mono" in encoding else 3 if any(token in encoding for token in ("rgb", "bgr")) else 0
    if not channels or data.size < height * width * channels:
        return None
    image = data[: height * width * channels].reshape(height, width, channels) if channels > 1 else data[: height * width].reshape(height, width)
    if channels == 1:
        return image
    conversion = cv2.COLOR_RGB2GRAY if encoding.startswith("rgb") else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(image, conversion)


def _merge_streams(streams: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    priorities = ("joint", "pose", "camera", "wrench", "scalar")
    primary_name = next((name for name in priorities if streams.get(name)), None)
    if primary_name is None:
        raise ValueError("ROS 日志中没有可分析的关节、位姿、图像、力觉或标量话题")
    base = pd.DataFrame(streams[primary_name]).sort_values("timestamp")
    base = base.groupby("timestamp", as_index=False).last()
    for name, records in streams.items():
        if name == primary_name or not records:
            continue
        other = pd.DataFrame(records).sort_values("timestamp").groupby("timestamp", as_index=False).last()
        periods = np.diff(other["timestamp"].to_numpy(dtype=float))
        periods = periods[periods > 0]
        tolerance = max(0.1, float(np.median(periods)) * 3 if periods.size else 0.1)
        overlapping = set(base.columns).intersection(other.columns).difference({"timestamp"})
        other = other.drop(columns=list(overlapping), errors="ignore")
        base = pd.merge_asof(base.sort_values("timestamp"), other, on="timestamp", direction="nearest", tolerance=tolerance)
    return base


def _mcap_summary(path: Path) -> dict[str, Any] | None:
    candidate = path if path.is_file() and path.suffix.lower() == ".mcap" else None
    if candidate is None and path.is_dir():
        candidate = next(path.glob("*.mcap"), None)
    if candidate is None:
        return None
    try:
        from mcap.reader import make_reader

        with candidate.open("rb") as stream:
            summary = make_reader(stream).get_summary()
        if summary is None:
            return None
        return {
            "message_count": int(summary.statistics.message_count) if summary.statistics else None,
            "channel_count": len(summary.channels),
            "schema_count": len(summary.schemas),
            "channels": [channel.topic for channel in summary.channels.values()],
        }
    except (ImportError, OSError, ValueError):
        return None


class RosbagAdapter:
    info = AdapterInfo(
        adapter_id="rosbag",
        name="ROS / ROS2 / MCAP",
        formats=(".bag", ".db3", ".mcap", "ROS2 bag directory"),
        description="通过 rosbags 读取 ROS1、ROS2 SQLite 与 MCAP，并自动映射常见机器人消息。",
        dependency="rosbags + mcap",
        project_url="https://gitlab.com/ternaris/rosbags",
        license="Apache-2.0 / MIT",
    )

    def __init__(self) -> None:
        try:
            import rosbags  # noqa: F401
            available = True
        except ImportError:
            available = False
        if not available:
            object.__setattr__(self, "info", AdapterInfo(**{**self.info.__dict__, "available": False}))

    def can_load(self, path: Path) -> bool:
        if not self.info.available:
            return False
        if path.is_file():
            return path.suffix.lower() in {".bag", ".db3", ".mcap"}
        return path.is_dir() and ((path / "metadata.yaml").exists() or any(path.glob("*.db3")) or any(path.glob("*.mcap")))

    def load(self, path: Path) -> LoadedDataset:
        if not self.info.available:
            raise ValueError("读取 ROS 日志需要安装可选依赖 rosbags")
        from rosbags.highlevel import AnyReader
        from rosbags.typesys import Stores, get_typestore

        reader_paths = [path]
        if path.is_file() and path.suffix.lower() == ".db3" and (path.parent / "metadata.yaml").exists():
            reader_paths = [path.parent]
        typestore = get_typestore(Stores.ROS2_HUMBLE)
        streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        topics: dict[str, str] = {}
        warnings: list[str] = []
        joint_names: list[str] = []
        last_image: dict[str, np.ndarray] = {}
        success_value: bool | None = None
        first_timestamp: int | None = None

        with AnyReader(reader_paths, default_typestore=typestore) as reader:
            for connection in reader.connections:
                topics[connection.topic] = connection.msgtype
            for connection, timestamp_ns, rawdata in reader.messages():
                if first_timestamp is None:
                    first_timestamp = timestamp_ns
                timestamp = (timestamp_ns - first_timestamp) / 1e9
                try:
                    message = reader.deserialize(rawdata, connection.msgtype)
                except Exception as error:
                    warnings.append(f"跳过无法反序列化的话题 {connection.topic}: {error}")
                    continue
                msgtype = connection.msgtype
                topic = connection.topic.lower()

                if msgtype.endswith("JointState"):
                    positions = np.asarray(getattr(message, "position", []), dtype=float).reshape(-1)
                    names = [str(name) for name in getattr(message, "name", [])]
                    if names and not joint_names:
                        joint_names = names
                    record = {"timestamp": timestamp}
                    record.update({f"joint_{index + 1}": float(value) for index, value in enumerate(positions)})
                    streams["joint"].append(record)
                    continue

                position = None
                if msgtype.endswith("PoseStamped") or msgtype.endswith("PoseWithCovarianceStamped"):
                    position = _attr(message, "pose.position") or _attr(message, "pose.pose.position")
                elif msgtype.endswith("Odometry"):
                    position = _attr(message, "pose.pose.position")
                elif msgtype.endswith("TransformStamped"):
                    position = _attr(message, "transform.translation")
                if position is not None:
                    streams["pose"].append({
                        "timestamp": timestamp,
                        "ee_x": float(position.x), "ee_y": float(position.y), "ee_z": float(position.z),
                    })
                    continue

                wrench = _attr(message, "wrench") if msgtype.endswith("WrenchStamped") else message if msgtype.endswith("Wrench") else None
                if wrench is not None and hasattr(wrench, "force"):
                    streams["wrench"].append({
                        "timestamp": timestamp,
                        "force_x": float(wrench.force.x), "force_y": float(wrench.force.y), "force_z": float(wrench.force.z),
                    })
                    continue

                if msgtype.endswith("Image") or msgtype.endswith("CompressedImage"):
                    gray = _image_gray(message)
                    if gray is not None:
                        try:
                            import cv2
                            gray = cv2.resize(gray, (96, 72), interpolation=cv2.INTER_AREA)
                        except ImportError:
                            pass
                        previous = last_image.get(connection.topic)
                        motion = 0.0 if previous is None else float(np.mean(np.abs(gray.astype(float) - previous.astype(float))) / 255.0)
                        last_image[connection.topic] = gray
                        streams["camera"].append({"timestamp": timestamp, "camera_motion": motion})
                    continue

                if hasattr(message, "data") and np.isscalar(message.data):
                    value = message.data.item() if hasattr(message.data, "item") else message.data
                    if "success" in topic and isinstance(value, (bool, np.bool_)):
                        success_value = bool(value)
                    elif "gripper" in topic:
                        streams["scalar"].append({"timestamp": timestamp, "gripper": float(value)})
                    elif "object_distance" in topic or "target_distance" in topic:
                        streams["scalar"].append({"timestamp": timestamp, "object_distance": float(value)})
                    elif "camera_motion" in topic:
                        streams["camera"].append({"timestamp": timestamp, "camera_motion": float(value)})
                    elif "phase" in topic or "task_state" in topic:
                        streams["scalar"].append({"timestamp": timestamp, "phase": str(value)})

        frame = _merge_streams(streams)
        frame["episode_id"] = path.stem or path.name
        if success_value is not None:
            frame["success"] = success_value
        if not streams.get("joint"):
            warnings.append("未发现 JointState，运动诊断将使用末端位姿或其他可用信号。")
        if not streams.get("pose"):
            warnings.append("未发现 Pose/Odometry，无法执行工作空间检查。")
        frame = validate_dataframe(frame)
        return LoadedDataset(
            frame=frame,
            source_format="ROS bag / MCAP",
            adapter_id=self.info.adapter_id,
            adapter_name=self.info.name,
            metadata={
                "topics": topics,
                "joint_names": joint_names,
                "stream_counts": {name: len(records) for name, records in streams.items()},
                "mcap_summary": _mcap_summary(path),
            },
            warnings=list(dict.fromkeys(warnings))[:20],
        )
