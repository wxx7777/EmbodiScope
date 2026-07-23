from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from embodiscope.profiles import PROFILES
from embodiscope.repair_benchmark import run_repair_benchmark


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 EmbodiScope RepairBench 清洗效果评测")
    parser.add_argument("--source", type=Path, default=Path("data/demo_pick_place.csv"))
    parser.add_argument("--seeds", type=int, default=4, help="随机种子数量，范围 2-12")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="generic-manipulator")
    parser.add_argument("--output", type=Path, default=Path("output/benchmark/repairbench-v1.0.json"))
    return parser.parse_args()


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    args = parse_args()
    source_path = project_path(args.source).resolve()
    destination = project_path(args.output).resolve()
    source = pd.read_csv(source_path)
    result = run_repair_benchmark(source, source_path, args.profile, args.seeds)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    metrics = result["metrics"]
    print(f"RepairBench: {result['protocol']['sample_count']} 条轨迹")
    print(f"修复动作成功率: {metrics['repair_success_rate'] * 100:.1f}%")
    print(f"重建 RMSE: {metrics['reconstruction_rmse']:.8f}")
    print(f"同步残差 MAE: {metrics['sync_residual_mae_ms']:.1f} ms")
    print(f"正常轨迹过度修复率: {metrics['nominal_overcorrection_rate'] * 100:.1f}%")
    print(f"质量门: {sum(gate['passed'] for gate in result['quality_gates'])}/{len(result['quality_gates'])} 通过")
    print(f"结果: {destination}")


if __name__ == "__main__":
    main()
