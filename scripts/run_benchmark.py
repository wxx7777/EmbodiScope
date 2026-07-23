from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from embodiscope.benchmark import run_benchmark
from embodiscope.profiles import PROFILES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 EmbodiScope FaultBench 统计评测")
    parser.add_argument("--seeds", type=int, default=8, help="随机种子数量，范围 2-25")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="generic-manipulator")
    parser.add_argument("--output", type=Path, default=Path("output/benchmark/faultbench-v1.0.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = pd.read_csv(ROOT / "data" / "demo_pick_place.csv")
    result = run_benchmark(source, args.profile, args.seeds)
    destination = args.output if args.output.is_absolute() else ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = result["metrics"]
    baseline = result["baseline"]
    print(f"FaultBench: {result['protocol']['sample_count']} 条轨迹")
    print(f"EmbodiScope Macro F1: {metrics['macro_f1'] * 100:.1f}%")
    print(f"固定阈值 Macro F1: {baseline['macro_f1'] * 100:.1f}%")
    print(f"正常轨迹误报率: {metrics['nominal_false_positive_rate'] * 100:.1f}%")
    print(f"结果: {destination}")


if __name__ == "__main__":
    main()
