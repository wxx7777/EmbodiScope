from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import re
import shutil
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .analysis import analyze_episode, dataset_overview, timeseries_payload
from .adapters import adapter_catalog, load_dataset
from .benchmark import run_benchmark
from .batch_repair import BatchRepairManager
from .dataset_library import DatasetLibrary
from .embodied import embodied_overview
from .profiles import profile_catalog, resolve_profile
from .repair import RepairArtifact, build_repair_artifact, source_sha256
from .repair_benchmark import run_repair_benchmark
from .recovery_benchmark import RecoveryBenchmarkManager
from .recovery_lab import RecoveryManager, recovery_catalog
from .report import build_markdown_report
from .rerun_export import export_episode_recording, rerun_status
from .simulation import SimulationManager, simulation_catalog
from .task_reasoning import task_reasoning_overview


class DataStore:
    def __init__(self, project_root: Path, dataset_path: Path):
        self.project_root = project_root
        self.static_dir = project_root / "static"
        self.upload_dir = project_root / "data" / "uploads"
        self.demo_path = dataset_path
        self.library = DatasetLibrary(project_root)
        self.dataset_path = dataset_path
        self.dataset_name = dataset_path.name
        self.loaded = load_dataset(dataset_path)
        self.current_dataset_id = self.library.identify(dataset_path)
        self.profile = resolve_profile()
        self._benchmark_cache: dict[tuple[str, int], dict] = {}
        self._repair_benchmark_cache: dict[tuple[str, int], dict] = {}
        self._repair_cache: dict[tuple[str, str, str], RepairArtifact] = {}
        self._source_hash_cache: dict[str, str] = {}

    @property
    def frame(self):
        return self.loaded.frame

    def overview(self) -> dict:
        overview = dataset_overview(self.frame, self.profile)
        overview["dataset_name"] = self.dataset_name
        overview["dataset_id"] = self.current_dataset_id
        overview.update(self.loaded.source_payload())
        return overview

    def source_digest(self) -> str:
        key = str(self.dataset_path.resolve())
        if key not in self._source_hash_cache:
            self._source_hash_cache[key] = source_sha256(self.dataset_path)
        return self._source_hash_cache[key]

    def audit(self) -> dict:
        payload = self.overview()
        payload["provenance"] = {
            "source_name": self.dataset_path.name,
            "source_sha256": self.source_digest(),
            "hash_algorithm": "SHA-256",
            "adapter_id": self.loaded.adapter_id,
            "adapter_name": self.loaded.adapter_name,
            "source_format": self.loaded.source_format,
        }
        payload["downloads"] = {"json": "/api/audit.json", "csv": "/api/audit.csv"}
        return payload

    def embodied(self) -> dict:
        payload = embodied_overview(self.frame)
        payload["dataset_name"] = self.dataset_name
        payload["dataset_id"] = self.current_dataset_id
        payload["source"] = self.loaded.source_payload()
        return payload

    def task_reasoning(self) -> dict:
        payload = task_reasoning_overview(self.frame, self.profile)
        payload["dataset_name"] = self.dataset_name
        payload["dataset_id"] = self.current_dataset_id
        payload["source"] = self.loaded.source_payload()
        return payload

    def audit_csv(self) -> bytes:
        audit = self.audit()
        output = io.StringIO(newline="")
        fields = [
            "episode_id", "quality_score", "grade", "success", "success_known", "duration",
            "missing_rate", "issue_count", "critical_count", "primary_cause", "issue_codes",
            "completeness", "temporal", "motion", "sync", "safety", "source_sha256",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for episode in audit["episodes"]:
            writer.writerow({
                **{key: episode.get(key) for key in fields},
                "issue_codes": ";".join(episode["issue_codes"]),
                **episode["scores"],
                "source_sha256": audit["provenance"]["source_sha256"],
            })
        return output.getvalue().encode("utf-8-sig")

    def repair(self, episode_id: str) -> RepairArtifact:
        key = (str(self.dataset_path.resolve()), self.profile.profile_id, str(episode_id))
        if key not in self._repair_cache:
            self._repair_cache[key] = build_repair_artifact(
                self.frame,
                str(episode_id),
                self.dataset_path,
                self.dataset_name,
                self.loaded.source_payload(),
                self.profile,
            )
        return self._repair_cache[key]

    def library_catalog(self) -> dict:
        return self.library.catalog(self.dataset_path)

    def profiles(self) -> dict:
        return {"profiles": profile_catalog(), "active_profile": self.profile.profile_id}

    def set_profile(self, profile_id: str) -> dict:
        self.profile = resolve_profile(profile_id)
        self._repair_cache.clear()
        return self.overview()

    def benchmark(self, seed_count: int = 8) -> dict:
        key = (self.profile.profile_id, seed_count)
        if key not in self._benchmark_cache:
            baseline = load_dataset(self.demo_path).frame
            self._benchmark_cache[key] = run_benchmark(baseline, self.profile, seed_count)
        return self._benchmark_cache[key]

    def repair_benchmark(self, seed_count: int = 4) -> dict:
        key = (self.profile.profile_id, seed_count)
        if key not in self._repair_benchmark_cache:
            baseline = load_dataset(self.demo_path).frame
            self._repair_benchmark_cache[key] = run_repair_benchmark(
                baseline,
                self.demo_path,
                self.profile,
                seed_count,
            )
        return self._repair_benchmark_cache[key]

    def reset(self) -> None:
        self._cleanup_upload(self.dataset_path)
        self.dataset_path = self.demo_path
        self.dataset_name = self.demo_path.name
        self.loaded = load_dataset(self.demo_path)
        self.current_dataset_id = self.library.identify(self.demo_path)
        self._repair_cache.clear()
        self._source_hash_cache.clear()

    def load_path(self, path: Path, dataset_name: str | None = None) -> dict:
        source = path.resolve()
        loaded = load_dataset(source)
        previous = self.dataset_path
        self.dataset_path = source
        self.dataset_name = dataset_name or source.name
        self.loaded = loaded
        self.current_dataset_id = self.library.identify(source)
        self._repair_cache.clear()
        self._source_hash_cache.clear()
        self._cleanup_upload(previous)
        return self.overview()

    def load_library(self, dataset_id: str) -> dict:
        entry = self.library.entry(dataset_id)
        overview = self.load_path(self.library.resolve(dataset_id), str(entry["name"]))
        self.current_dataset_id = dataset_id
        overview["dataset_id"] = dataset_id
        return overview

    def upload(self, filename: str, content: bytes) -> dict:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", Path(filename).name) or "uploaded.csv"
        if len(content) > 25 * 1024 * 1024:
            raise ValueError("文件超过 25 MB 限制")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        destination = self.upload_dir / safe_name
        destination.write_bytes(content)
        try:
            source = self._extract_archive(destination) if destination.suffix.lower() == ".zip" else destination
            loaded = load_dataset(source)
        except Exception:
            self._cleanup_upload(destination)
            if destination.suffix.lower() == ".zip":
                self._cleanup_upload(destination.with_suffix(""))
            raise
        previous = self.dataset_path
        self.dataset_path, self.dataset_name, self.loaded = source, safe_name, loaded
        self.current_dataset_id = None
        self._repair_cache.clear()
        self._source_hash_cache.clear()
        if source != destination:
            destination.unlink(missing_ok=True)
        self._cleanup_upload(previous)
        return self.overview()

    def media(self, episode_id: str) -> dict:
        metadata = self.loaded.metadata if isinstance(self.loaded.metadata, dict) else {}
        segments = metadata.get("video_segments", {})
        if isinstance(segments, dict) and str(episode_id) in segments:
            segment = segments[str(episode_id)]
            root = self.dataset_path if self.dataset_path.is_dir() else self.dataset_path.parent
            path = (root / str(segment["relative_path"])).resolve()
            if root.resolve() not in path.parents or not path.is_file():
                raise ValueError("数据集视频不存在")
            return {
                "path": path,
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "feature": segment.get("feature", "RGB"),
            }
        simulation = metadata.get("simulation", {})
        if isinstance(simulation, dict) and simulation.get("video_file"):
            root = self.dataset_path.parent
            path = (root / str(simulation["video_file"])).resolve()
            if root.resolve() not in path.parents or not path.is_file():
                raise ValueError("仿真视频不存在")
            return {
                "path": path,
                "start": 0.0,
                "end": float(simulation.get("duration_seconds", 0.0)),
                "feature": "SAPIEN RGB",
            }
        raise ValueError("当前 Episode 没有可回放视频")

    def media_payload(self, episode_id: str) -> dict:
        try:
            media = self.media(episode_id)
        except ValueError:
            return {"available": False}
        return {
            "available": True,
            "url": f"/api/dataset/video/{episode_id}",
            "start": media["start"],
            "end": media["end"],
            "duration": max(0.0, media["end"] - media["start"]),
            "feature": media["feature"],
        }

    def _extract_archive(self, archive: Path) -> Path:
        target = archive.with_suffix("")
        self._cleanup_upload(target)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as handle:
            members = [item for item in handle.infolist() if not item.is_dir()]
            if len(members) > 2000 or sum(item.file_size for item in members) > 150 * 1024 * 1024:
                raise ValueError("ZIP 数据集解压后超过 150 MB 或包含过多文件")
            for item in members:
                output = (target / item.filename).resolve()
                if target.resolve() not in output.parents:
                    raise ValueError("ZIP 数据集包含非法路径")
            handle.extractall(target)
        children = [item for item in target.iterdir() if item.name != "__MACOSX"]
        return children[0] if len(children) == 1 and children[0].is_dir() else target

    def _cleanup_upload(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            if resolved == self.demo_path.resolve() or not resolved.is_relative_to(self.upload_dir.resolve()):
                return
            if resolved.is_dir():
                shutil.rmtree(resolved, ignore_errors=True)
            else:
                resolved.unlink(missing_ok=True)
            parent = resolved.parent
            if parent != self.upload_dir.resolve() and parent.is_relative_to(self.upload_dir.resolve()):
                try:
                    parent.rmdir()
                except OSError:
                    pass
        except OSError:
            pass


def make_handler(
    store: DataStore,
    simulations: SimulationManager | None = None,
    batch_repairs: BatchRepairManager | None = None,
    recoveries: RecoveryManager | None = None,
    recovery_benchmarks: RecoveryBenchmarkManager | None = None,
):
    class EmbodiScopeHandler(BaseHTTPRequestHandler):
        server_version = "EmbodiScope/2.3"

        def log_message(self, format_string: str, *args) -> None:
            print(f"[EmbodiScope] {self.address_string()} - {format_string % args}")

        def _send_bytes(self, content: bytes, content_type: str, status: int = 200, headers: dict | None = None) -> None:
            extra_headers = headers or {}
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            sensitive = content_type.startswith(("application/json", "text/csv", "application/octet-stream"))
            self.send_header("Cache-Control", extra_headers.get("Cache-Control", "no-store" if sensitive else "public, max-age=120"))
            for key, value in extra_headers.items():
                if key.lower() == "cache-control":
                    continue
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(content)

        def _json(self, payload, status: int = 200) -> None:
            self._send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

        def _error(self, message: str, status: int = 400) -> None:
            self._json({"error": message}, status)

        def _send_file(self, path: Path, content_type: str) -> None:
            size = path.stat().st_size
            range_header = self.headers.get("Range", "")
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip()) if range_header else None
            if match:
                start = int(match.group(1) or 0)
                end = int(match.group(2) or size - 1)
                if start >= size or end < start:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                length = end - start + 1
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with path.open("rb") as handle:
                    handle.seek(start)
                    self.wfile.write(handle.read(length))
                return
            self._send_bytes(path.read_bytes(), content_type, headers={"Accept-Ranges": "bytes"})

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/health":
                    self._json({"status": "ok", "dataset": store.dataset_name, "adapter": store.loaded.adapter_id, "rerun": rerun_status()})
                    return
                if path == "/api/adapters":
                    self._json({"adapters": adapter_catalog()})
                    return
                if path == "/api/datasets":
                    self._json(store.library_catalog())
                    return
                if path == "/api/profiles":
                    self._json(store.profiles())
                    return
                if path == "/api/audit":
                    self._json(store.audit())
                    return
                if path == "/api/embodied":
                    self._json(store.embodied())
                    return
                if path == "/api/embodied.json":
                    filename = f"embodiscope_{re.sub(r'[^a-zA-Z0-9_-]', '_', store.dataset_name)}_embodied.json"
                    content = json.dumps(store.embodied(), ensure_ascii=False, indent=2).encode("utf-8")
                    self._send_bytes(
                        content,
                        "application/json; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                    )
                    return
                if path == "/api/task-reasoning":
                    self._json(store.task_reasoning())
                    return
                if path == "/api/task-reasoning.json":
                    filename = f"embodiscope_{re.sub(r'[^a-zA-Z0-9_-]', '_', store.dataset_name)}_task_reasoning.json"
                    content = json.dumps(store.task_reasoning(), ensure_ascii=False, indent=2).encode("utf-8")
                    self._send_bytes(
                        content,
                        "application/json; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                    )
                    return
                if path == "/api/audit.json":
                    filename = f"embodiscope_{re.sub(r'[^a-zA-Z0-9_-]', '_', store.dataset_name)}_audit.json"
                    content = json.dumps(store.audit(), ensure_ascii=False, indent=2).encode("utf-8")
                    self._send_bytes(
                        content,
                        "application/json; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                    )
                    return
                if path == "/api/audit.csv":
                    filename = f"embodiscope_{re.sub(r'[^a-zA-Z0-9_-]', '_', store.dataset_name)}_audit.csv"
                    self._send_bytes(
                        store.audit_csv(),
                        "text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                    )
                    return
                if path.startswith("/api/repair/download/"):
                    episode_id = path.removeprefix("/api/repair/download/").strip("/")
                    artifact = store.repair(episode_id)
                    safe_episode = re.sub(r"[^a-zA-Z0-9_-]", "_", episode_id)
                    self._send_bytes(
                        artifact.csv_bytes,
                        "text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="embodiscope_{safe_episode}_cleaned.csv"'},
                    )
                    return
                if path.startswith("/api/repair/manifest/"):
                    episode_id = path.removeprefix("/api/repair/manifest/").strip("/")
                    artifact = store.repair(episode_id)
                    safe_episode = re.sub(r"[^a-zA-Z0-9_-]", "_", episode_id)
                    self._send_bytes(
                        artifact.manifest_bytes(),
                        "application/json; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="embodiscope_{safe_episode}_repair.json"'},
                    )
                    return
                if path.startswith("/api/repair/"):
                    episode_id = path.removeprefix("/api/repair/").strip("/")
                    self._json(store.repair(episode_id).payload)
                    return
                if path == "/api/batch-repair/status":
                    if batch_repairs is None:
                        raise ValueError("批量清洗服务未初始化")
                    self._json(batch_repairs.status())
                    return
                if path.startswith("/api/batch-repair/status/"):
                    if batch_repairs is None:
                        raise ValueError("批量清洗服务未初始化")
                    job_id = path.removeprefix("/api/batch-repair/status/").strip("/")
                    self._json(batch_repairs.status(job_id))
                    return
                if path.startswith("/api/batch-repair/download/"):
                    if batch_repairs is None:
                        raise ValueError("批量清洗服务未初始化")
                    parts = path.removeprefix("/api/batch-repair/download/").strip("/").split("/")
                    if len(parts) != 2:
                        raise ValueError("批量清洗下载路径无效")
                    job_id, artifact_id = parts
                    artifact_path = batch_repairs.artifact(job_id, artifact_id)
                    content_types = {
                        "package": "application/zip",
                        "parquet": "application/vnd.apache.parquet",
                        "manifest": "application/json; charset=utf-8",
                        "summary": "text/csv; charset=utf-8",
                    }
                    self._send_bytes(
                        artifact_path.read_bytes(),
                        content_types[artifact_id],
                        headers={
                            "Content-Disposition": f'attachment; filename="{artifact_path.name}"',
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if path.startswith("/api/dataset/video/"):
                    episode_id = path.removeprefix("/api/dataset/video/").strip("/")
                    self._send_file(store.media(episode_id)["path"], "video/mp4")
                    return
                if path == "/api/simulation/catalog":
                    self._json(simulation_catalog())
                    return
                if path == "/api/simulation/status":
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    self._json(simulations.status())
                    return
                if path.startswith("/api/simulation/status/"):
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    job_id = path.removeprefix("/api/simulation/status/").strip("/")
                    self._json(simulations.status(job_id))
                    return
                if path.startswith("/api/simulation/replay/"):
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    job_id = path.removeprefix("/api/simulation/replay/").strip("/")
                    self._send_file(simulations.artifact(job_id, "replay.json"), "application/json; charset=utf-8")
                    return
                if path.startswith("/api/simulation/video/"):
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    job_id = path.removeprefix("/api/simulation/video/").strip("/")
                    self._send_file(simulations.artifact(job_id, "episode.mp4"), "video/mp4")
                    return
                if path == "/api/recovery/catalog":
                    self._json(recovery_catalog())
                    return
                if path == "/api/recovery/status":
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    self._json(recoveries.status())
                    return
                if path.startswith("/api/recovery/status/"):
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    job_id = path.removeprefix("/api/recovery/status/").strip("/")
                    self._json(recoveries.status(job_id))
                    return
                if path.startswith("/api/recovery/replay/"):
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    parts = path.removeprefix("/api/recovery/replay/").strip("/").split("/")
                    if len(parts) != 2:
                        raise ValueError("恢复回放路径无效")
                    self._send_file(recoveries.artifact(parts[0], parts[1], "replay.json"), "application/json; charset=utf-8")
                    return
                if path.startswith("/api/recovery/thumbnail/"):
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    parts = path.removeprefix("/api/recovery/thumbnail/").strip("/").split("/")
                    if len(parts) != 2:
                        raise ValueError("恢复缩略图路径无效")
                    self._send_file(recoveries.artifact(parts[0], parts[1], "thumbnail.jpg"), "image/jpeg")
                    return
                if path.startswith("/api/recovery/video/"):
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    parts = path.removeprefix("/api/recovery/video/").strip("/").split("/")
                    if len(parts) != 2:
                        raise ValueError("恢复视频路径无效")
                    self._send_file(recoveries.artifact(parts[0], parts[1], "episode.mp4"), "video/mp4")
                    return
                if path.startswith("/api/recovery/result/"):
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    job_id = path.removeprefix("/api/recovery/result/").strip("/")
                    result_path = recoveries.result_path(job_id)
                    self._send_bytes(
                        result_path.read_bytes(),
                        "application/json; charset=utf-8",
                        headers={
                            "Content-Disposition": f'attachment; filename="{result_path.name}"',
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if path == "/api/recovery-benchmark/status":
                    if recovery_benchmarks is None:
                        raise ValueError("RecoveryBench 服务未初始化")
                    self._json(recovery_benchmarks.status())
                    return
                if path.startswith("/api/recovery-benchmark/status/"):
                    if recovery_benchmarks is None:
                        raise ValueError("RecoveryBench 服务未初始化")
                    job_id = path.removeprefix("/api/recovery-benchmark/status/").strip("/")
                    self._json(recovery_benchmarks.status(job_id))
                    return
                if path.startswith("/api/recovery-benchmark/result/"):
                    if recovery_benchmarks is None:
                        raise ValueError("RecoveryBench 服务未初始化")
                    job_id = path.removeprefix("/api/recovery-benchmark/result/").strip("/")
                    result_path = recovery_benchmarks.result_path(job_id)
                    self._send_bytes(
                        result_path.read_bytes(),
                        "application/json; charset=utf-8",
                        headers={
                            "Content-Disposition": f'attachment; filename="{result_path.name}"',
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if path == "/api/dataset":
                    self._json(store.overview())
                    return
                if path.startswith("/api/episode/"):
                    episode_id = path.removeprefix("/api/episode/").strip("/")
                    analysis = analyze_episode(store.frame, episode_id, store.profile)
                    analysis["timeseries"] = timeseries_payload(store.frame, episode_id)
                    analysis["dataset_name"] = store.dataset_name
                    analysis["media"] = store.media_payload(episode_id)
                    analysis.update(store.loaded.source_payload())
                    self._json(analysis)
                    return
                if path.startswith("/api/report/"):
                    episode_id = path.removeprefix("/api/report/").strip("/")
                    report = build_markdown_report(
                        store.dataset_name,
                        analyze_episode(store.frame, episode_id, store.profile),
                        store.loaded.source_payload(),
                    )
                    filename = f"embodiscope_{re.sub(r'[^a-zA-Z0-9_-]', '_', episode_id)}_report.md"
                    self._send_bytes(
                        report.encode("utf-8"), "text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                    )
                    return
                if path.startswith("/api/rerun/"):
                    episode_id = path.removeprefix("/api/rerun/").strip("/")
                    analysis = analyze_episode(store.frame, episode_id, store.profile)
                    safe_episode = re.sub(r"[^a-zA-Z0-9_-]", "_", episode_id)
                    output = store.project_root / "output" / "rerun" / f"embodiscope_{safe_episode}.rrd"
                    export_episode_recording(store.frame, episode_id, output, store.dataset_name, analysis)
                    self._send_bytes(
                        output.read_bytes(),
                        "application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{output.name}"'},
                    )
                    return
                self._serve_static(path)
            except ValueError as error:
                self._error(str(error), HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self._error(f"服务器处理失败: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 36 * 1024 * 1024:
                    self._error("请求体超过限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    return
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                if parsed.path == "/api/upload":
                    if payload.get("content_base64"):
                        try:
                            content = base64.b64decode(payload["content_base64"], validate=True)
                        except (ValueError, TypeError) as error:
                            raise ValueError("上传内容不是有效的 Base64 数据") from error
                    else:
                        content = str(payload.get("content", "")).encode("utf-8")
                    self._json(store.upload(payload.get("filename", "uploaded.csv"), content))
                    return
                if parsed.path == "/api/reset":
                    store.reset()
                    self._json(store.overview())
                    return
                if parsed.path == "/api/datasets/load":
                    self._json(store.load_library(str(payload.get("dataset_id", ""))))
                    return
                if parsed.path == "/api/profile/load":
                    self._json(store.set_profile(str(payload.get("profile_id", ""))))
                    return
                if parsed.path == "/api/benchmark/run":
                    self._json(store.benchmark(int(payload.get("seed_count", 8))))
                    return
                if parsed.path == "/api/repair-benchmark/run":
                    self._json(store.repair_benchmark(int(payload.get("seed_count", 4))))
                    return
                if parsed.path == "/api/batch-repair/run":
                    if batch_repairs is None:
                        raise ValueError("批量清洗服务未初始化")
                    self._json(batch_repairs.submit(
                        store.frame,
                        store.dataset_path,
                        store.dataset_name,
                        store.loaded.source_payload(),
                        store.source_digest(),
                        store.profile,
                    ), HTTPStatus.ACCEPTED)
                    return
                if parsed.path == "/api/batch-repair/cancel":
                    if batch_repairs is None:
                        raise ValueError("批量清洗服务未初始化")
                    self._json(batch_repairs.cancel(str(payload.get("job_id", ""))))
                    return
                if parsed.path == "/api/simulation/run":
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    self._json(simulations.submit(payload), HTTPStatus.ACCEPTED)
                    return
                if parsed.path == "/api/simulation/cancel":
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    self._json(simulations.cancel(str(payload.get("job_id", ""))))
                    return
                if parsed.path == "/api/simulation/load":
                    if simulations is None:
                        raise ValueError("仿真服务未初始化")
                    job_id = str(payload.get("job_id", ""))
                    job = simulations.status(job_id)
                    if job["status"] != "completed":
                        raise ValueError("仿真作业尚未完成")
                    overview = store.load_path(simulations.trajectory(job_id), f"{job_id}.h5")
                    self._json({"job": job, "dataset": overview})
                    return
                if parsed.path == "/api/recovery/run":
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    self._json(recoveries.submit(payload), HTTPStatus.ACCEPTED)
                    return
                if parsed.path == "/api/recovery/cancel":
                    if recoveries is None:
                        raise ValueError("恢复实验服务未初始化")
                    self._json(recoveries.cancel(str(payload.get("job_id", ""))))
                    return
                if parsed.path == "/api/recovery-benchmark/run":
                    if recovery_benchmarks is None:
                        raise ValueError("RecoveryBench 服务未初始化")
                    self._json(recovery_benchmarks.submit(payload), HTTPStatus.ACCEPTED)
                    return
                if parsed.path == "/api/recovery-benchmark/cancel":
                    if recovery_benchmarks is None:
                        raise ValueError("RecoveryBench 服务未初始化")
                    self._json(recovery_benchmarks.cancel(str(payload.get("job_id", ""))))
                    return
                self._error("接口不存在", HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as error:
                self._error(str(error), HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self._error(f"服务器处理失败: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)

        def _serve_static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            candidate = (store.static_dir / relative).resolve()
            if store.static_dir.resolve() not in candidate.parents and candidate != store.static_dir.resolve():
                self._error("非法路径", HTTPStatus.FORBIDDEN)
                return
            if not candidate.is_file():
                candidate = store.static_dir / "index.html"
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self._send_bytes(candidate.read_bytes(), content_type)

    return EmbodiScopeHandler


def run_server(project_root: Path, dataset_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    store = DataStore(project_root, dataset_path)
    simulations = SimulationManager(project_root)
    batch_repairs = BatchRepairManager(project_root)
    recoveries = RecoveryManager(project_root)
    recovery_benchmarks = RecoveryBenchmarkManager(project_root)
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(store, simulations, batch_repairs, recoveries, recovery_benchmarks),
    )
    print(f"EmbodiScope 已启动: http://{host}:{port}")
    print(f"当前数据集: {dataset_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEmbodiScope 已停止")
    finally:
        server.server_close()
