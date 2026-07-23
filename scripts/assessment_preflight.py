from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAL_RECOVERY_BENCH = (
    ROOT
    / "output"
    / "recovery-benchmark"
    / "recovery-bench-20260723-002311-2c10fb"
    / "result.json"
)
FORMAL_FAULT_BENCH = ROOT / "output" / "benchmark" / "faultbench-v1.0.json"
FORMAL_REPAIR_BENCH = ROOT / "output" / "benchmark" / "repairbench-v1.0.json"


def request_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def matches(actual: object, expected: float, *, tolerance: float = 1e-6) -> bool:
    return isinstance(actual, (int, float)) and math.isclose(
        float(actual), expected, rel_tol=0.0, abs_tol=tolerance
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the EmbodiScope assessment package.")
    parser.add_argument("--url", default="http://127.0.0.1:8876")
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    print("EmbodiScope assessment preflight", flush=True)

    required = [
        ROOT / "README.md",
        ROOT / "docs" / "EmbodiScope_Assessment_Deck_v2.3.pptx",
        ROOT / "docs" / "EmbodiScope_Assessment_Demo_v2.3.mp4",
        ROOT / "docs" / "demo_script.md",
        ROOT / "docs" / "assessment_qa.md",
        ROOT / "docs" / "assessment_submission.md",
        ROOT / "docs" / "technical_report.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "CHANGELOG.md",
        ROOT / "CITATION.cff",
        FORMAL_FAULT_BENCH,
        FORMAL_REPAIR_BENCH,
        FORMAL_RECOVERY_BENCH,
    ]
    for item in required:
        if not item.is_file() or item.stat().st_size == 0:
            failures.append(f"missing artifact: {item.relative_to(ROOT)}")

    if FORMAL_FAULT_BENCH.is_file():
        result = json.loads(FORMAL_FAULT_BENCH.read_text(encoding="utf-8"))
        metrics = result.get("metrics", {})
        expected = {
            "macro F1": (metrics.get("macro_f1"), 0.9841),
            "macro recall": (metrics.get("macro_recall"), 1.0),
            "nominal false positive rate": (
                metrics.get("nominal_false_positive_rate"),
                0.0,
            ),
        }
        for name, (actual, target) in expected.items():
            if not matches(actual, target):
                failures.append(f"formal FaultBench {name} changed")

    if FORMAL_REPAIR_BENCH.is_file():
        result = json.loads(FORMAL_REPAIR_BENCH.read_text(encoding="utf-8"))
        metrics = result.get("metrics", {})
        expected = {
            "repair success rate": (metrics.get("repair_success_rate"), 1.0),
            "reconstruction RMSE": (metrics.get("reconstruction_rmse"), 0.00013753),
            "sync residual": (metrics.get("sync_residual_mae_ms"), 0.0),
            "nominal overcorrection rate": (
                metrics.get("nominal_overcorrection_rate"),
                0.0,
            ),
        }
        for name, (actual, target) in expected.items():
            if not matches(actual, target):
                failures.append(f"formal RepairBench {name} changed")
        gates = result.get("quality_gates", [])
        if len(gates) != 6 or not all(gate.get("passed") is True for gate in gates):
            failures.append("formal RepairBench quality gates are not 6/6")

    if FORMAL_RECOVERY_BENCH.is_file():
        result = json.loads(FORMAL_RECOVERY_BENCH.read_text(encoding="utf-8"))
        summary = result.get("summary", {})
        if summary.get("trials") != 9:
            failures.append("formal RecoveryBench trial count is not 9")
        expected = {
            "task recovery": (summary.get("task_recovery", {}).get("rate"), 0.8889),
            "episode safety": (summary.get("episode_safety", {}).get("rate"), 0.6667),
            "post safety": (
                summary.get("post_intervention_safety", {}).get("rate"),
                1.0,
            ),
            "pair integrity": (summary.get("pair_integrity", {}).get("rate"), 1.0),
            "online trigger coverage": (
                summary.get("online_trigger_coverage", {}).get("rate"),
                1.0,
            ),
            "recovery latency p95": (
                summary.get("recovery_latency_s", {}).get("p95"),
                3.93,
            ),
        }
        for name, (actual, target) in expected.items():
            if not matches(actual, target):
                failures.append(f"formal RecoveryBench {name} changed")

        ci95 = summary.get("task_recovery", {}).get("ci95", {})
        if not matches(ci95.get("lower"), 0.565) or not matches(ci95.get("upper"), 0.9801):
            failures.append("formal RecoveryBench Wilson interval changed")

        protocol = result.get("protocol", {})
        if protocol.get("seeds") != [7, 9, 10]:
            failures.append("formal RecoveryBench admitted seeds changed")
        excluded = protocol.get("excluded_seeds", [])
        if (
            len(excluded) != 1
            or excluded[0].get("seed") != 8
            or "fault" not in str(excluded[0].get("reason", "")).lower()
        ):
            failures.append("formal RecoveryBench seed 8 exclusion changed")

        strict_failures = [
            (trial.get("scenario"), trial.get("seed"))
            for trial in result.get("trials", [])
            if trial.get("task_recovery") is False
        ]
        if strict_failures != [("collision", 10)]:
            failures.append("formal RecoveryBench strict failure set changed")

    if sys.version_info < (3, 10):
        failures.append(f"Python 3.10+ required, found {sys.version.split()[0]}")

    if importlib.util.find_spec("mani_skill") is None:
        warnings.append("ManiSkill is unavailable; live simulation will be disabled")
    if importlib.util.find_spec("sapien") is None:
        warnings.append("SAPIEN is unavailable; live simulation will be disabled")

    if not args.skip_http:
        base = args.url.rstrip("/")
        endpoints = [
            "/api/health",
            "/api/task-reasoning",
            "/api/recovery/status",
            "/api/recovery-benchmark/status",
        ]
        for endpoint in endpoints:
            try:
                payload = request_json(base + endpoint)
                if not isinstance(payload, dict):
                    failures.append(f"invalid JSON response: {endpoint}")
            except (OSError, ValueError, urllib.error.URLError) as error:
                failures.append(f"HTTP check failed for {endpoint}: {error}")

    if args.run_tests:
        print("  Running pytest...", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0:
            failures.append("pytest failed")

        node = shutil.which("node")
        if node is None:
            failures.append("node is required for the JavaScript syntax check")
        else:
            print("  Running JavaScript syntax check...", flush=True)
            completed = subprocess.run(
                [node, "--check", str(ROOT / "static" / "app.js")],
                cwd=ROOT,
                check=False,
            )
            if completed.returncode != 0:
                failures.append("static/app.js syntax check failed")

    print(f"  Python: {sys.version.split()[0]}")
    print(f"  Root: {ROOT}")
    print(f"  URL: {args.url if not args.skip_http else 'skipped'}")
    for warning in warnings:
        print(f"  WARN: {warning}")
    for failure in failures:
        print(f"  FAIL: {failure}")
    if failures:
        print(f"Preflight failed with {len(failures)} issue(s).")
        return 1
    print("Preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
