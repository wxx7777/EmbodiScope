from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from embodiscope.batch_repair import build_batch_repair_package
from embodiscope.profiles import PROFILES
from embodiscope.repair import source_sha256


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量清洗全部 Episode 并生成训练数据包")
    parser.add_argument("--source", type=Path, default=Path("data/demo_pick_place.csv"))
    parser.add_argument("--profile", choices=sorted(PROFILES), default="generic-manipulator")
    parser.add_argument("--output", type=Path, help="产物目录；默认按当前时间创建")
    return parser.parse_args()


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    args = parse_args()
    source_path = project_path(args.source).resolve()
    default_output = Path("output/batch-repair") / f"cli-{datetime.now():%Y%m%d-%H%M%S}"
    output_dir = project_path(args.output or default_output).resolve()
    frame = pd.read_csv(source_path)
    source = {"adapter_id": "csv", "adapter_name": "通用 CSV", "source_format": "CSV"}

    def progress(value: float, message: str) -> None:
        print(f"[{value * 100:5.1f}%] {message}")

    result = build_batch_repair_package(
        frame,
        source_path,
        source_path.name,
        source,
        source_sha256(source_path),
        args.profile,
        output_dir,
        progress=progress,
    )
    summary = result["summary"]
    print(f"Episodes: {summary['episode_count']}")
    print(f"输入/保留/隔离: {summary['source_rows']} / {summary['retained_rows']} / {summary['quarantined_rows']}")
    print(f"保留率: {summary['retained_rate']:.2f}%")
    print(f"ZIP SHA-256: {result['package_sha256']}")
    print(f"产物目录: {output_dir}")


if __name__ == "__main__":
    main()
