"""Asynchronous multi-scenario benchmark for physical recovery policies."""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from .recovery_lab import RECOVERY_PROTOCOLS, _load_replay, build_recovery_result
from .simulation import SIMULATION_EXECUTION_LOCK, SimulationConfig, run_simulation, runtime_status


BENCHMARK_SCENARIOS = tuple(RECOVERY_PROTOCOLS)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> dict[str, Any]:
    if total <= 0:
        return {"method": "Wilson score", "confidence": 0.95, "lower": None, "upper": None}
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = (proportion + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) / denominator
    return {
        "method": "Wilson score",
        "confidence": 0.95,
        "lower": round(max(0.0, centre - margin), 4),
        "upper": round(min(1.0, centre + margin), 4),
    }


def _rate(successes: int, total: int) -> dict[str, Any]:
    return {
        "successes": int(successes),
        "total": int(total),
        "rate": round(successes / total, 4) if total else 0.0,
    }


def _numeric_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p95": None}
    return {
        "mean": round(float(np.mean(values)), 4),
        "p95": round(float(np.percentile(values, 95)), 4),
    }


def _trial_row(result: dict[str, Any]) -> dict[str, Any]:
    verdicts = result["verdicts"]
    trigger = result.get("trigger", {})
    return {
        "scenario": result["scenario"],
        "scenario_name": result["scenario_name"],
        "seed": result["seed"],
        "task_recovery": bool(verdicts["task_recovery"]["passed"]),
        "episode_safety": bool(verdicts["episode_safety"]["passed"]),
        "post_intervention_safety": bool(verdicts["post_intervention_safety"]["passed"]),
        "pair_integrity": bool(result["pair_integrity"]["passed"]),
        "online_trigger": bool(
            trigger.get("trigger_source") == "online-predicate-monitor"
            and trigger.get("trigger_step") is not None
        ),
        "trigger_type": trigger.get("trigger_type"),
        "recovery_latency": result["metrics"].get("recovery_latency"),
        "path_overhead": result["metrics"].get("path_overhead"),
        "operator_completion_rate": result["metrics"].get("operator_completion_rate"),
        "episode_peak_force_n": verdicts["episode_safety"].get("observed_peak_force_n"),
        "post_intervention_peak_force_n": verdicts["post_intervention_safety"].get("observed_peak_force_n"),
        "fingerprint": result["pair_integrity"].get("fingerprint"),
    }


def _aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    task_successes = sum(row["task_recovery"] for row in rows)
    latency = [float(row["recovery_latency"]) for row in rows if row["recovery_latency"] is not None]
    path = [float(row["path_overhead"]) for row in rows if row["path_overhead"] is not None]
    operator = [float(row["operator_completion_rate"]) for row in rows if row["operator_completion_rate"] is not None]
    return {
        "trials": total,
        "task_recovery": {
            **_rate(task_successes, total),
            "ci95": wilson_interval(task_successes, total),
        },
        "pair_integrity": _rate(sum(row["pair_integrity"] for row in rows), total),
        "online_trigger_coverage": _rate(sum(row["online_trigger"] for row in rows), total),
        "episode_safety": _rate(sum(row["episode_safety"] for row in rows), total),
        "post_intervention_safety": _rate(sum(row["post_intervention_safety"] for row in rows), total),
        "recovery_latency_s": _numeric_summary(latency),
        "path_overhead_m": _numeric_summary(path),
        "operator_completion_rate": {
            "mean": round(float(np.mean(operator)), 2) if operator else None,
        },
    }


def aggregate_recovery_benchmark(
    results: list[dict[str, Any]],
    *,
    seeds: list[int],
    scenarios: list[str],
    horizon: int,
    excluded_seeds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [_trial_row(result) for result in results]
    per_scenario = []
    for scenario in scenarios:
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        per_scenario.append({
            "scenario": scenario,
            "scenario_name": next((row["scenario_name"] for row in scenario_rows), scenario),
            **_aggregate_rows(scenario_rows),
        })

    matrix = []
    for seed in seeds:
        cells: dict[str, Any] = {}
        for scenario in scenarios:
            row = next((item for item in rows if item["seed"] == seed and item["scenario"] == scenario), None)
            if row:
                cells[scenario] = {
                    key: row[key]
                    for key in (
                        "task_recovery", "episode_safety", "post_intervention_safety",
                        "online_trigger", "recovery_latency", "trigger_type",
                    )
                }
        matrix.append({"seed": seed, "scenarios": cells})

    return {
        "benchmark": "RecoveryBench",
        "version": "1.0",
        "created_at": time.time(),
        "protocol": {
            "design": "paired counterfactual physical simulation",
            "variants_per_trial": ["failure", "recovered"],
            "scenarios": scenarios,
            "seeds": seeds,
            "horizon": horizon,
            "video_recording": False,
            "safety_invariant": "force <= 36.0 N for every evaluated frame",
            "claim_boundary": "Task recovery and safety are reported independently.",
            "seed_admission": "All paired trials must contain the controlled fault signature.",
            "excluded_seeds": excluded_seeds or [],
        },
        "summary": _aggregate_rows(rows),
        "per_scenario": per_scenario,
        "matrix": matrix,
        "trials": rows,
    }


class RecoveryBenchmarkManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.output_root = self.project_root / "output" / "recovery-benchmark"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._load_existing_jobs()

    def _load_existing_jobs(self) -> None:
        for result_path in self.output_root.glob("recovery-bench-*/result.json"):
            job_id = result_path.parent.name
            if not re.fullmatch(r"recovery-bench-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", job_id):
                continue
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                created_at = result_path.stat().st_mtime
                self._jobs[job_id] = {
                    "id": job_id,
                    "status": "completed",
                    "progress": 1.0,
                    "message": "已恢复历史 RecoveryBench",
                    "config": {
                        "scenarios": result["protocol"]["scenarios"],
                        "seeds": result["protocol"]["seeds"],
                        "horizon": result["protocol"]["horizon"],
                    },
                    "created_at": created_at,
                    "updated_at": created_at,
                    "result": result,
                    "error": None,
                }
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        seed_count = int(payload.get("seed_count", 3))
        base_seed = int(payload.get("base_seed", 7))
        horizon = int(payload.get("horizon", 140))
        raw_scenarios = payload.get("scenarios", list(BENCHMARK_SCENARIOS))
        scenarios = list(dict.fromkeys(str(item) for item in raw_scenarios))
        if not 2 <= seed_count <= 8:
            raise ValueError("seed_count 必须在 2 到 8 之间")
        if not 0 <= base_seed <= 2**31 - seed_count:
            raise ValueError("base_seed 必须是有效的非负 32 位整数")
        if not 100 <= horizon <= 160:
            raise ValueError("horizon 必须在 100 到 160 之间")
        if not scenarios or any(scenario not in BENCHMARK_SCENARIOS for scenario in scenarios):
            raise ValueError("RecoveryBench 场景必须来自碰撞、夹爪失效和抓取滑脱")
        if not runtime_status()["available"]:
            raise ValueError("当前 Python 环境未安装 ManiSkill/SAPIEN")
        candidate_seeds = list(range(base_seed, base_seed + seed_count))
        with self._lock:
            if any(job["status"] in {"queued", "running"} for job in self._jobs.values()):
                raise ValueError("已有 RecoveryBench 正在运行")
            job_id = f"recovery-bench-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0.0,
                "message": "等待 RecoveryBench 启动",
                "config": {
                    "scenarios": scenarios,
                    "requested_seed_count": seed_count,
                    "base_seed": base_seed,
                    "candidate_seeds": candidate_seeds,
                    "horizon": horizon,
                },
                "created_at": time.time(),
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
        threading.Thread(
            target=self._run_job,
            args=(job_id, scenarios, seed_count, base_seed, horizon),
            daemon=True,
            name=f"EmbodiScope-{job_id}",
        ).start()
        return self.status(job_id)

    def _run_job(
        self,
        job_id: str,
        scenarios: list[str],
        seed_count: int,
        base_seed: int,
        horizon: int,
    ) -> None:
        output_dir = self.output_root / job_id
        cancel = self._cancel_events[job_id]
        target_trials = len(scenarios) * seed_count
        results: list[dict[str, Any]] = []
        valid_seeds: list[int] = []
        excluded_seeds: list[dict[str, Any]] = []
        reported_progress = 0.0
        self._update(job_id, status="running", message="正在启动多场景配对仿真")
        try:
            seed = base_seed
            max_candidate = base_seed + seed_count + 12
            while len(valid_seeds) < seed_count and seed < max_candidate:
                seed_results: list[dict[str, Any]] = []
                seed_exclusion: dict[str, Any] | None = None
                for scenario_index, scenario in enumerate(scenarios):
                    if cancel.is_set():
                        raise InterruptedError("RecoveryBench 已取消")
                    trial_dir = output_dir / f"seed-{seed}" / scenario

                    def variant_progress(value: float, message: str, variant: str) -> None:
                        nonlocal reported_progress
                        variant_offset = 0.0 if variant == "Failure" else 0.5
                        candidate = (
                            len(results) + scenario_index + variant_offset + value * 0.5
                        ) / target_trials * 0.96
                        reported_progress = max(reported_progress, min(0.96, candidate))
                        self._update(
                            job_id,
                            progress=round(reported_progress, 4),
                            message=f"seed {seed} · {scenario} · {variant} · {message}",
                        )

                    with SIMULATION_EXECUTION_LOCK:
                        failure = run_simulation(
                            SimulationConfig(
                                scenario=scenario,
                                seed=seed,
                                steps=horizon,
                                record_video=False,
                                recovery_enabled=False,
                            ),
                            trial_dir / "failure",
                            progress=lambda value, message: variant_progress(value, message, "Failure"),
                            cancelled=cancel.is_set,
                        )
                        if cancel.is_set():
                            raise InterruptedError("RecoveryBench 已取消")
                        recovered = run_simulation(
                            SimulationConfig(
                                scenario=scenario,
                                seed=seed,
                                steps=horizon,
                                record_video=False,
                                recovery_enabled=True,
                            ),
                            trial_dir / "recovered",
                            progress=lambda value, message: variant_progress(value, message, "Recovered"),
                            cancelled=cancel.is_set,
                        )
                    result = build_recovery_result(
                        scenario,
                        seed,
                        horizon,
                        _load_replay(Path(failure["replay_path"])),
                        _load_replay(Path(recovered["replay_path"])),
                    )
                    if not result["pair_integrity"]["passed"]:
                        failed_checks = [
                            check["key"]
                            for check in result["pair_integrity"]["checks"]
                            if not check["passed"]
                        ]
                        seed_exclusion = {
                            "seed": seed,
                            "scenario": scenario,
                            "reason": "paired trial did not contain an admissible controlled fault",
                            "failed_pair_checks": failed_checks,
                            "failure_rows": result["failure"]["rows"],
                            "recovered_rows": result["recovered"]["rows"],
                            "failure_success": result["failure"]["success"],
                            "recovered_success": result["recovered"]["success"],
                        }
                        break
                    seed_results.append(result)
                    self._update(
                        job_id,
                        progress=round(reported_progress, 4),
                        message=f"seed {seed} 已完成 {len(seed_results)}/{len(scenarios)} 个有效场景",
                    )

                if seed_exclusion:
                    excluded_seeds.append(seed_exclusion)
                    self._update(
                        job_id,
                        message=f"seed {seed} 未出现受控故障，已记录排除并顺延候选种子",
                    )
                elif len(seed_results) == len(scenarios):
                    results.extend(seed_results)
                    valid_seeds.append(seed)
                    reported_progress = max(reported_progress, len(results) / target_trials * 0.96)
                    self._update(
                        job_id,
                        progress=round(reported_progress, 4),
                        message=f"已接受 {len(valid_seeds)}/{seed_count} 个有效种子",
                    )
                seed += 1

            if len(valid_seeds) < seed_count:
                raise RuntimeError(f"候选范围内只有 {len(valid_seeds)} 个种子满足故障准入条件")

            benchmark = aggregate_recovery_benchmark(
                results,
                seeds=valid_seeds,
                scenarios=scenarios,
                horizon=horizon,
                excluded_seeds=excluded_seeds,
            )
            benchmark["downloads"] = {"json": f"/api/recovery-benchmark/result/{job_id}"}
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(
                json.dumps(benchmark, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._update(
                job_id,
                status="completed",
                progress=1.0,
                message="RecoveryBench 多场景统计评测完成",
                result=benchmark,
            )
        except InterruptedError as error:
            self._update(job_id, status="cancelled", message=str(error), error=str(error))
        except Exception as error:
            self._update(
                job_id,
                status="failed",
                message="RecoveryBench 执行失败",
                error=f"{type(error).__name__}: {error}",
            )

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)
            self._jobs[job_id]["updated_at"] = time.time()

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if job_id:
                if job_id not in self._jobs:
                    raise ValueError("找不到 RecoveryBench 作业")
                return json.loads(json.dumps(self._jobs[job_id]))
            jobs = sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return {
                "jobs": json.loads(json.dumps(jobs[:8])),
                "active_job": next(
                    (job["id"] for job in jobs if job["status"] in {"queued", "running"}),
                    None,
                ),
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError("找不到 RecoveryBench 作业")
            if self._jobs[job_id]["status"] not in {"queued", "running"}:
                raise ValueError("该 RecoveryBench 作业当前不可取消")
            self._cancel_events[job_id].set()
        return self.status(job_id)

    def result_path(self, job_id: str) -> Path:
        job = self.status(job_id)
        if job["status"] != "completed":
            raise ValueError("RecoveryBench 结果尚未生成")
        path = (self.output_root / job_id / "result.json").resolve()
        if self.output_root not in path.parents or not path.is_file():
            raise ValueError("RecoveryBench 结果不存在")
        return path
