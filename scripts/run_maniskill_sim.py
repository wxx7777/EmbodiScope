from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from embodiscope.simulation import SCENARIOS, SimulationConfig, run_simulation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="运行真实 ManiSkill 仿真并导出 EmbodiScope 回放")
    parser.add_argument("--env", dest="env_id", default="PickCube-v1")
    parser.add_argument("--scenario", choices=SCENARIOS, default="collision")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--steps", type=int, default=80)
    parser.add_argument("--fps", type=int, choices=(10, 20, 30), default=20)
    parser.add_argument("--width", type=int, choices=(256, 320, 384), default=320)
    parser.add_argument("--height", type=int, choices=(192, 240, 288), default=240)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "simulations" / "cli-latest")
    args = parser.parse_args()
    config = SimulationConfig.from_payload({
        "env_id": args.env_id,
        "scenario": args.scenario,
        "seed": args.seed,
        "steps": args.steps,
        "fps": args.fps,
        "width": args.width,
        "height": args.height,
        "record_video": not args.no_video,
    })

    def progress(value: float, message: str) -> None:
        print(f"[{value * 100:5.1f}%] {message}", flush=True)

    result = run_simulation(config, args.output.resolve(), progress=progress)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
