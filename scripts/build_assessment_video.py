from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg


ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "docs" / "assets" / "assessment-slides"
CLIPS = ROOT / "docs" / "assets" / "assessment-video-clips"
DEFAULT_OUTPUT = ROOT / "docs" / "EmbodiScope_Assessment_Demo_v2.3.mp4"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def encode_still(ffmpeg: str, image: Path, duration: float, output: Path) -> None:
    run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            str(duration),
            "-i",
            str(image),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=white,format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-r",
            "30",
            str(output),
        ]
    )


def encode_clip(ffmpeg: str, source: Path, duration: float, output: Path) -> None:
    run(
        [
            ffmpeg,
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-t",
            str(duration),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-r",
            "30",
            str(output),
        ]
    )


def encode_pair(ffmpeg: str, duration: float, output: Path) -> None:
    failure = CLIPS / "recovery-failure.mp4"
    recovered = CLIPS / "recovery-recovered.mp4"
    font = Path("C:/Windows/Fonts/arialbd.ttf")
    font_arg = str(font).replace("\\", "/").replace(":", "\\:")
    filter_graph = (
        "[0:v]scale=640:480:force_original_aspect_ratio=decrease,"
        "pad=640:720:(ow-iw)/2:(oh-ih)/2:black,"
        f"drawtext=fontfile='{font_arg}':text='FAILURE':x=28:y=28:"
        "fontsize=34:fontcolor=white:box=1:boxcolor=black@0.65[f];"
        "[1:v]scale=640:480:force_original_aspect_ratio=decrease,"
        "pad=640:720:(ow-iw)/2:(oh-ih)/2:black,"
        f"drawtext=fontfile='{font_arg}':text='RECOVERED':x=28:y=28:"
        "fontsize=34:fontcolor=white:box=1:boxcolor=0x147D73@0.85[r];"
        "[f][r]hstack=inputs=2,format=yuv420p[v]"
    )
    run(
        [
            ffmpeg,
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(failure),
            "-stream_loop",
            "-1",
            "-i",
            str(recovered),
            "-t",
            str(duration),
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-r",
            "30",
            str(output),
        ]
    )


def build(output: Path) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    work = ROOT / "tmp" / "assessment-video"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    timeline: list[tuple[str, Path, float]] = [
        ("still", SLIDES / "slide-01.png", 10),
        ("still", SLIDES / "slide-02.png", 12),
        ("still", SLIDES / "slide-03.png", 12),
        ("still", SLIDES / "slide-04.png", 12),
        ("clip", CLIPS / "collision-simulation.mp4", 14),
        ("still", SLIDES / "slide-06.png", 10),
        ("still", SLIDES / "slide-07.png", 14),
        ("still", SLIDES / "slide-05.png", 8),
        ("pair", CLIPS / "recovery-failure.mp4", 24),
        ("still", SLIDES / "slide-08.png", 14),
        ("still", ROOT / "docs" / "assets" / "recoverybench-v23-desktop.png", 14),
        ("still", SLIDES / "slide-09.png", 15),
        ("still", SLIDES / "slide-10.png", 12),
        ("still", SLIDES / "slide-11.png", 12),
        ("still", SLIDES / "slide-12.png", 15),
    ]

    segments: list[Path] = []
    for index, (kind, source, duration) in enumerate(timeline, start=1):
        if not source.exists():
            raise FileNotFoundError(source)
        segment = work / f"segment-{index:02d}.mp4"
        if kind == "still":
            encode_still(ffmpeg, source, duration, segment)
        elif kind == "clip":
            encode_clip(ffmpeg, source, duration, segment)
        else:
            encode_pair(ffmpeg, duration, segment)
        segments.append(segment)

    concat_file = work / "concat.txt"
    concat_file.write_text(
        "".join(f"file '{segment.as_posix()}'\n" for segment in segments),
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(output))
    print(f"Created {output} ({seconds:.1f}s, {frames} frames)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the offline EmbodiScope assessment demo video.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == "__main__":
    main()
