from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

import pandas as pd

from .profiles import AnalysisProfile, resolve_profile
from .repair import build_repair_artifact


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_batch_repair_package(
    frame: pd.DataFrame,
    source_path: Path,
    dataset_name: str,
    source: dict[str, Any],
    source_digest: str,
    profile: str | AnalysisProfile | None,
    output_dir: Path,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    active_profile = resolve_profile(profile)
    output_dir.mkdir(parents=True, exist_ok=False)
    episode_ids = frame["episode_id"].astype(str).drop_duplicates().tolist()
    cleaned_frames: list[pd.DataFrame] = []
    episode_summaries: list[dict[str, Any]] = []
    action_rows: Counter[str] = Counter()
    action_episodes: Counter[str] = Counter()
    resolved_issues: Counter[str] = Counter()
    unresolved_issues: Counter[str] = Counter()

    for index, episode_id in enumerate(episode_ids):
        if cancelled and cancelled():
            raise InterruptedError("批量清洗已取消")
        if progress:
            progress(index / max(1, len(episode_ids)) * 0.82, f"正在清洗 Episode {index + 1} / {len(episode_ids)}")
        artifact = build_repair_artifact(
            frame,
            episode_id,
            source_path,
            dataset_name,
            source,
            active_profile,
            source_digest=source_digest,
        )
        cleaned_frames.append(artifact.cleaned)
        payload = artifact.payload
        summary = payload["summary"]
        episode_summaries.append({
            "episode_id": episode_id,
            "status": payload["status"],
            "source_rows": summary["source_rows"],
            "modified_rows": summary["modified_rows"],
            "quarantined_rows": summary["quarantined_rows"],
            "retained_rows": summary["retained_rows"],
            "retained_rate": summary["retained_rate"],
            "segment_count": summary["segment_count"],
            "before_issue_count": summary["before_issue_count"],
            "after_issue_count": summary["after_issue_count"],
            "before_quality_score": summary["before_quality_score"],
            "after_quality_score": summary["after_quality_score"],
            "resolved_issues": ";".join(payload["issue_resolution"]["resolved"]),
            "unresolved_issues": ";".join(payload["issue_resolution"]["unresolved"]),
        })
        for action in payload["actions"]:
            action_rows[action["code"]] += int(action["row_count"])
            action_episodes[action["code"]] += 1
        resolved_issues.update(payload["issue_resolution"]["resolved"])
        unresolved_issues.update(payload["issue_resolution"]["unresolved"])

    if cancelled and cancelled():
        raise InterruptedError("批量清洗已取消")
    if progress:
        progress(0.84, "正在合并清洗数据与质量掩码")
    cleaned = pd.concat(cleaned_frames, ignore_index=True, sort=False) if cleaned_frames else pd.DataFrame()
    parquet_path = output_dir / "cleaned.parquet"
    cleaned.to_parquet(parquet_path, index=False, compression="zstd")

    summary_path = output_dir / "episode_summary.csv"
    pd.DataFrame(episode_summaries).to_csv(summary_path, index=False, encoding="utf-8-sig", lineterminator="\n")
    parquet_hash = _file_sha256(parquet_path)
    summary_hash = _file_sha256(summary_path)
    if progress:
        progress(0.92, "正在生成数据血缘 manifest")

    source_rows = int(len(cleaned))
    retained_rows = int(cleaned["quality_valid"].sum()) if "quality_valid" in cleaned.columns else 0
    modified_rows = int(cleaned["repair_actions"].astype(str).ne("").sum()) if "repair_actions" in cleaned.columns else 0
    quarantined_rows = source_rows - retained_rows
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_name": dataset_name,
        "status": "review_required" if quarantined_rows or unresolved_issues else "ready",
        "summary": {
            "episode_count": len(episode_ids),
            "source_rows": source_rows,
            "modified_rows": modified_rows,
            "quarantined_rows": quarantined_rows,
            "retained_rows": retained_rows,
            "retained_rate": round(retained_rows / max(1, source_rows) * 100, 2),
        },
        "actions": [
            {"code": code, "episode_count": action_episodes[code], "row_count": rows}
            for code, rows in sorted(action_rows.items(), key=lambda item: (-item[1], item[0]))
        ],
        "issue_resolution": {
            "resolved": dict(sorted(resolved_issues.items())),
            "unresolved": dict(sorted(unresolved_issues.items())),
        },
        "policy": {
            "physical_events_are_preserved": True,
            "timestamp_samples_are_not_synthesized": True,
            "training_gate": "quality_valid == true",
            "short_gap_limit": 3,
        },
        "analysis_profile": active_profile.to_dict(),
        "provenance": {
            "source_name": source_path.name,
            "source_sha256": source_digest,
            "hash_algorithm": "SHA-256",
            "adapter_id": source.get("adapter_id"),
            "adapter_name": source.get("adapter_name"),
            "source_format": source.get("source_format"),
        },
        "artifacts": {
            "cleaned.parquet": {"sha256": parquet_hash, "bytes": parquet_path.stat().st_size},
            "episode_summary.csv": {"sha256": summary_hash, "bytes": summary_path.stat().st_size},
        },
        "episodes": episode_summaries,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_hash = _file_sha256(manifest_path)

    if progress:
        progress(0.97, "正在打包训练数据产物")
    package_path = output_dir / "embodiscope-cleaned-dataset.zip"
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for path in (parquet_path, summary_path, manifest_path):
            archive.write(path, arcname=path.name)
    package_hash = _file_sha256(package_path)
    if progress:
        progress(1.0, "批量清洗与训练数据打包完成")
    return {
        "summary": manifest["summary"],
        "status": manifest["status"],
        "source_sha256": source_digest,
        "package_sha256": package_hash,
        "artifact_sha256": {
            "parquet": parquet_hash,
            "episode_summary": summary_hash,
            "manifest": manifest_hash,
        },
    }


class BatchRepairManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.output_root = self.project_root / "output" / "batch-repair"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def submit(
        self,
        frame: pd.DataFrame,
        source_path: Path,
        dataset_name: str,
        source: dict[str, Any],
        source_digest: str,
        profile: AnalysisProfile,
    ) -> dict[str, Any]:
        with self._lock:
            if any(job["status"] in {"queued", "running"} for job in self._jobs.values()):
                raise ValueError("已有批量清洗作业正在运行，请等待完成或取消")
            job_id = f"clean-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0.0,
                "message": "等待批量清洗线程启动",
                "dataset_name": dataset_name,
                "episode_count": int(frame["episode_id"].astype(str).nunique()),
                "row_count": len(frame),
                "profile_id": profile.profile_id,
                "created_at": time.time(),
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
        threading.Thread(
            target=self._run_job,
            args=(job_id, frame.copy(deep=True), source_path, dataset_name, dict(source), source_digest, profile),
            daemon=True,
            name=f"EmbodiScope-{job_id}",
        ).start()
        return self.status(job_id)

    def _run_job(
        self,
        job_id: str,
        frame: pd.DataFrame,
        source_path: Path,
        dataset_name: str,
        source: dict[str, Any],
        source_digest: str,
        profile: AnalysisProfile,
    ) -> None:
        self._update(job_id, status="running", message="正在逐 Episode 生成清洗方案")
        output_dir = self.output_root / job_id

        def progress(value: float, message: str) -> None:
            self._update(job_id, progress=round(max(0.0, min(1.0, value)), 4), message=message)

        try:
            result = build_batch_repair_package(
                frame,
                source_path,
                dataset_name,
                source,
                source_digest,
                profile,
                output_dir,
                progress=progress,
                cancelled=self._cancel_events[job_id].is_set,
            )
            result["downloads"] = {
                "package": f"/api/batch-repair/download/{job_id}/package",
                "parquet": f"/api/batch-repair/download/{job_id}/parquet",
                "manifest": f"/api/batch-repair/download/{job_id}/manifest",
                "summary": f"/api/batch-repair/download/{job_id}/summary",
            }
            self._update(job_id, status="completed", progress=1.0, message="训练数据包已生成", result=result)
        except InterruptedError as error:
            self._update(job_id, status="cancelled", message=str(error), error=str(error))
        except Exception as error:
            self._update(job_id, status="failed", message="批量清洗执行失败", error=f"{type(error).__name__}: {error}")

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)
            self._jobs[job_id]["updated_at"] = time.time()

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if job_id:
                if job_id not in self._jobs:
                    raise ValueError("找不到批量清洗作业")
                return json.loads(json.dumps(self._jobs[job_id]))
            jobs = sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return {
                "jobs": json.loads(json.dumps(jobs[:12])),
                "active_job": next((job["id"] for job in jobs if job["status"] in {"queued", "running"}), None),
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError("找不到批量清洗作业")
            if self._jobs[job_id]["status"] not in {"queued", "running"}:
                raise ValueError("该批量清洗作业当前不可取消")
            self._cancel_events[job_id].set()
        return self.status(job_id)

    def artifact(self, job_id: str, artifact_id: str) -> Path:
        if not re.fullmatch(r"clean-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", job_id):
            raise ValueError("非法批量清洗作业编号")
        names = {
            "package": "embodiscope-cleaned-dataset.zip",
            "parquet": "cleaned.parquet",
            "manifest": "manifest.json",
            "summary": "episode_summary.csv",
        }
        if artifact_id not in names:
            raise ValueError("非法批量清洗产物")
        path = (self.output_root / job_id / names[artifact_id]).resolve()
        if not path.is_relative_to(self.output_root.resolve()) or not path.is_file():
            raise ValueError("批量清洗产物不存在")
        return path
