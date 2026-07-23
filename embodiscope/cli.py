from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import load_dataset
from .analysis import analyze_episode, dataset_overview
from .report import build_markdown_report


def main() -> None:
    parser = argparse.ArgumentParser(description="EmbodiScope 具身数据质量诊断 CLI")
    parser.add_argument("source", type=Path, help="CSV、LeRobot Dataset、ManiSkill HDF5、ROS bag 或 MCAP 数据源")
    parser.add_argument("--episode", help="只分析指定 episode；默认分析全部")
    parser.add_argument("--output", type=Path, default=Path("output"), help="报告输出目录")
    args = parser.parse_args()

    loaded = load_dataset(args.source)
    frame = loaded.frame
    args.output.mkdir(parents=True, exist_ok=True)
    overview = dataset_overview(frame)
    episode_ids = [args.episode] if args.episode else [episode["episode_id"] for episode in overview["episodes"]]

    results = []
    for episode_id in episode_ids:
        analysis = analyze_episode(frame, episode_id)
        results.append(analysis)
        report_path = args.output / f"episode_{episode_id}_report.md"
        report_path.write_text(build_markdown_report(args.source.name, analysis, loaded.source_payload()), encoding="utf-8")
        print(f"{episode_id}: {analysis['quality_score']:.1f}/100, {len(analysis['issues'])} 个问题 -> {report_path}")

    json_path = args.output / "analysis.json"
    json_path.write_text(json.dumps({
        "source": loaded.source_payload(), "overview": overview, "episodes": results
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结构化结果 -> {json_path}")


if __name__ == "__main__":
    main()
