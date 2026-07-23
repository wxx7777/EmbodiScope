from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "data" / "open_source" / "lerobot_pusht"
REVISION = "7628202a2180972f291ba1bc6723834921e72c19"
ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(relative: str, destination: Path) -> None:
    errors: list[str] = []
    for endpoint in ENDPOINTS:
        url = f"{endpoint}/datasets/lerobot/pusht/resolve/{REVISION}/{relative}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "EmbodiScope/1.8"})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except (OSError, urllib.error.URLError) as error:
            errors.append(f"{endpoint}: {error}")
    raise RuntimeError(f"无法下载 {relative}: {'; '.join(errors)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="下载并校验 EmbodiScope 使用的 LeRobot PushT 开源数据")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest_path = DESTINATION / "SOURCE.json"
    if not manifest_path.is_file():
        raise SystemExit("缺少 SOURCE.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        target = DESTINATION / relative
        if not args.verify_only and (not target.is_file() or sha256(target) != expected):
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False, dir=target.parent) as handle:
                temporary = Path(handle.name)
            try:
                download(relative, temporary)
                if sha256(temporary) != expected:
                    raise RuntimeError(f"哈希校验失败: {relative}")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if not target.is_file() or sha256(target) != expected:
            raise SystemExit(f"文件校验失败: {relative}")
        print(f"OK  {relative}")


if __name__ == "__main__":
    main()
