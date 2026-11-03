"""Keyframe calibration evidence (dwamianm/prism#389): still-scene false keeps and the spike corpus.

Two measurements, both local, without a model call and without a network:

``still``
    One or more video files of a still scene, such as a webcam on a desk with
    nothing moving, in normal and in low light. Nothing changes in such a
    clip, so every frame kept for change is a false keep; a heartbeat keep
    (one every ``max_gap_seconds``) is by design and is counted apart. The
    report gives keeps per minute and the false-keep rate under the 0.16.1
    policy (``vision.calibrate_keyframes=false``) and under the calibrated
    default, with the still-frame scores and the calibrated threshold, and
    optionally under fixed thresholds of the 0.16.1 score (``--fixed``; the
    camera demo's sensitivities were 0.30, 0.45 and 0.55).

``corpus``
    The spike corpus videos (``meeting.mp4`` and the Tahoe aerial video; see
    ``benchmarks/vision/corpus.py``): the frames each policy keeps, whether
    the 0.16.1 policy reproduces the keyframes the recorded run kept, and
    whether every frame the spike's questions depend on is still kept.

Frames are decoded and sampled as ``observe()`` does (``vision.fps``,
``vision.frame_width``); ``--fps 2`` measures the rate a live camera sends.

Usage::

    uv run python -m benchmarks.vision.keyframe_calibration still normal-light.mov low-light.mov
    uv run python -m benchmarks.vision.keyframe_calibration still clip.mov --fps 2 --fixed 0.30 0.45 --json out.json
    uv run python -m benchmarks.vision.keyframe_calibration corpus --run-dir benchmarks/vision/runs/phase1-store
"""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prme.config import VisionConfig
from prme.vision.frames import decode_video_frames
from prme.vision.keyframes import KeyframeDecision, KeyframePolicy, frame_signature

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.json"
DEFAULT_RUN = HERE / "runs" / "phase1-store"

# The meeting scenes (benchmarks/vision/corpus.py meeting_scenes, in order) that show each question's answer:
# 0 title, 1 whiteboard, 2 site photo, 3 whiteboard with the new date and budget, 4 action items, 5 chart,
# 6 risk register, 7 wrap up.
MEETING_QUESTION_SCENES: dict[str, tuple[int, ...]] = {
    "m01": (0,), "m02": (3,), "m03": (1, 3), "m04": (3,), "m05": (1,), "m06": (1, 3), "m07": (4,), "m08": (4,),
    "m09": (5,), "m10": (5,), "m11": (6,), "m12": (6,), "m13": (7,), "m14": (7,), "m15": (4,), "m16": (4,),
}


@dataclass(frozen=True)
class Setting:
    """One keyframe policy to measure."""

    name: str
    calibrate: bool
    threshold: float


def settings_for(config: VisionConfig, fixed: Sequence[float] = ()) -> list[Setting]:
    settings = [Setting("0.16.1 (calibrate_keyframes=false)", False, config.change_threshold),
                Setting("calibrated (default)", True, config.change_threshold)]
    settings += [Setting(f"0.16.1 score, fixed {value:.2f}", False, value) for value in fixed]
    return settings


def run_policies(frames: Iterable[Any], settings: Sequence[Setting], config: VisionConfig,
                 fps: float) -> dict[str, list[KeyframeDecision]]:
    """Every setting's decisions over *frames*, each frame's signature computed once."""
    policies = {setting.name: KeyframePolicy(threshold=setting.threshold, max_gap=config.max_gap_seconds,
                                             calibrate=setting.calibrate, min_threshold=config.min_change_threshold,
                                             noise_multiplier=config.noise_multiplier)
                for setting in settings}
    decisions: dict[str, list[KeyframeDecision]] = {name: [] for name in policies}
    for index, frame in enumerate(frames):
        signature = frame_signature(frame)
        for name, policy in policies.items():
            decisions[name].append(policy.decide(signature, index=index, time_s=index / fps))
    return decisions


def decode(path: Path, workdir: Path, config: VisionConfig, fps: float) -> list[Path]:
    return decode_video_frames(path, workdir, fps=fps, width=config.frame_width)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def still_summary(decisions: list[KeyframeDecision], fps: float) -> dict[str, Any]:
    """Keeps and false keeps of one setting over a clip of a still scene."""
    frames = len(decisions)
    minutes = frames / fps / 60
    kept = [decision for decision in decisions if decision.kept]
    false_keeps = sum(decision.reason == "change" for decision in kept)
    heartbeats = sum(decision.reason == "heartbeat" for decision in kept)
    still = [decision.change for decision in decisions if decision.change is not None and decision.reason != "change"]
    # From the third frame on: the second is compared before any still frame, at the configured threshold.
    thresholds = [decision.threshold for decision in decisions[2:] if decision.threshold is not None]
    per_minute = (lambda count: round(count / minutes, 2)) if minutes else (lambda count: None)
    return {
        "frames": frames,
        "minutes": round(minutes, 2),
        "keeps": len(kept),
        "keeps_per_minute": per_minute(len(kept)),
        "false_keeps": false_keeps,
        "false_keeps_per_minute": per_minute(false_keeps),
        "false_keep_rate": round(false_keeps / (frames - 1), 4) if frames > 1 else None,
        "heartbeats": heartbeats,
        "still_score_median": round(statistics.median(still), 3) if still else None,
        "still_score_p95": _percentile(still, 0.95),
        "still_score_max": max(still) if still else None,
        "false_keep_scores": [decision.change for decision in kept if decision.reason == "change"],
        "threshold_min": min(thresholds) if thresholds else None,
        "threshold_median": round(statistics.median(thresholds), 3) if thresholds else None,
        "threshold_max": max(thresholds) if thresholds else None,
        "kept_s": [decision.time_s for decision in kept],
    }


def measure_still(paths: Sequence[Path], *, config: VisionConfig, fps: float,
                  fixed: Sequence[float] = ()) -> dict[str, Any]:
    settings = settings_for(config, fixed)
    clips = []
    for path in paths:
        with tempfile.TemporaryDirectory(prefix="prme-keyframe-calibration-") as tmp:
            decisions = run_policies(decode(path, Path(tmp), config, fps), settings, config, fps)
        clips.append({
            "path": str(path),
            "settings": {name: still_summary(entries, fps) for name, entries in decisions.items()},
            "decisions": {name: [entry.to_dict() for entry in entries] for name, entries in decisions.items()},
        })
    return {"measurement": "still", "fps": fps, "config": _config_values(config),
            "settings": [setting.__dict__ for setting in settings], "clips": clips}


def still_markdown(report: dict[str, Any]) -> str:
    lines = [f"Sampled at {report['fps']} frames a second; heartbeat every {report['config']['max_gap_seconds']} s. "
             "Every keep for change in a still scene is a false keep.", ""]
    for clip in report["clips"]:
        lines += [f"### {clip['path']}", "",
                  "| Setting | Frames | Minutes | Keeps | Keeps per minute | False keeps | False keeps per minute "
                  "| False-keep rate | Still score median / p95 / max | Threshold min / median / max |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for name, row in clip["settings"].items():
            lines.append(
                f"| {name} | {row['frames']} | {row['minutes']} | {row['keeps']} | {row['keeps_per_minute']} "
                f"| {row['false_keeps']} | {row['false_keeps_per_minute']} | {row['false_keep_rate']} "
                f"| {row['still_score_median']} / {row['still_score_p95']} / {row['still_score_max']} "
                f"| {row['threshold_min']} / {row['threshold_median']} / {row['threshold_max']} |")
        lines.append("")
    return "\n".join(lines)


def _scene_ranges(manifest: dict[str, Any]) -> list[tuple[int, int]]:
    return [(int(scene["start_s"]), int(scene["end_s"])) for scene in manifest["meeting"]["scenes"]]


def measure_corpus(run_dir: Path, *, config: VisionConfig, fps: float) -> dict[str, Any]:
    manifest = json.loads((run_dir / "corpus" / "manifest.json").read_text())
    questions = json.loads(QUESTIONS.read_text())
    unmapped = [q["id"] for q in questions if q["source"] == "meeting" and q["id"] not in MEETING_QUESTION_SCENES]
    if unmapped:
        raise ValueError(f"questions without a meeting scene: {unmapped}")
    settings = settings_for(config)
    videos: dict[str, Any] = {}
    for name in ("meeting", "tahoe"):
        path = Path(manifest[name]["path"])
        with tempfile.TemporaryDirectory(prefix="prme-keyframe-calibration-") as tmp:
            decisions = run_policies(decode(path, Path(tmp), config, fps), settings, config, fps)
        recorded_path = run_dir / f"keyframes_{name}.json"
        recorded = ([entry["index"] for entry in json.loads(recorded_path.read_text()) if entry["kept"]]
                    if recorded_path.exists() else None)
        videos[name] = {"path": str(path), "frames": len(next(iter(decisions.values()))), "recorded_kept": recorded,
                        "settings": {}}
        for setting_name, entries in decisions.items():
            kept = [entry for entry in entries if entry.kept]
            videos[name]["settings"][setting_name] = {
                "keeps": len(kept),
                "kept": [[entry.index, entry.reason] for entry in kept],
                "threshold_min": min((e.threshold for e in entries[2:] if e.threshold is not None), default=None),
                "threshold_max": max((e.threshold for e in entries[2:] if e.threshold is not None), default=None),
            }
    scenes = _scene_ranges(manifest)
    dependencies = []
    for question in questions:
        if question["source"] == "meeting":
            ranges = [scenes[index] for index in MEETING_QUESTION_SCENES[question["id"]]]
            video = "meeting"
        elif question["source"] == "tahoe":
            ranges = [(0, videos["tahoe"]["frames"])]
            video = "tahoe"
        else:
            continue  # a photo is always its own keyframe
        row: dict[str, Any] = {"id": question["id"], "video": video, "frames": [list(r) for r in ranges]}
        for setting_name, result in videos[video]["settings"].items():
            row[setting_name] = [index for index, _ in result["kept"] if any(start <= index < end
                                                                             for start, end in ranges)]
        dependencies.append(row)
    scene_starts = {setting_name: [start for start, _ in scenes
                                   if start in {index for index, _ in result["kept"]}]
                    for setting_name, result in videos["meeting"]["settings"].items()}
    return {"measurement": "corpus", "fps": fps, "config": _config_values(config), "run_dir": str(run_dir),
            "videos": videos, "meeting_scene_starts": [start for start, _ in scenes],
            "meeting_scene_starts_kept": scene_starts, "questions": dependencies}


def corpus_markdown(report: dict[str, Any]) -> str:
    lines = ["| Video | Setting | Keeps | Kept frames (index, reason) | Threshold min / max |", "|---|---|---|---|---|"]
    for name, video in report["videos"].items():
        for setting_name, result in video["settings"].items():
            kept = ", ".join(f"{index} {reason}" for index, reason in result["kept"])
            lines.append(f"| {name} | {setting_name} | {result['keeps']} | {kept} "
                         f"| {result['threshold_min']} / {result['threshold_max']} |")
    lines.append("")
    for name, video in report["videos"].items():
        if video["recorded_kept"] is not None:
            old = [index for index, _ in video["settings"]["0.16.1 (calibrate_keyframes=false)"]["kept"]]
            lines.append(f"- {name}: the 0.16.1 policy keeps the recorded run's frames exactly: "
                         f"{'yes' if old == video['recorded_kept'] else 'no'} ({len(video['recorded_kept'])} recorded).")
    starts = report["meeting_scene_starts"]
    for setting_name, kept in report["meeting_scene_starts_kept"].items():
        lines.append(f"- meeting scene starts kept under {setting_name}: {len(kept)} of {len(starts)}.")
    lines += ["", "| Question | Video | Answer frames | " + " | ".join(report["videos"]["meeting"]["settings"]) + " |",
              "|---|---|---|" + "---|" * len(report["videos"]["meeting"]["settings"])]
    for row in report["questions"]:
        cells = [", ".join(str(index) for index in row[name]) or "none"
                 for name in report["videos"]["meeting"]["settings"]]
        frames = ", ".join(f"{start} to {end - 1}" for start, end in row["frames"])
        lines.append(f"| {row['id']} | {row['video']} | {frames} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _config_values(config: VisionConfig) -> dict[str, Any]:
    return {name: getattr(config, name) for name in (
        "fps", "frame_width", "change_threshold", "calibrate_keyframes", "min_change_threshold", "noise_multiplier",
        "max_gap_seconds")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.vision.keyframe_calibration",
                                     description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    still = sub.add_parser("still", help="false keeps on video files of a still scene")
    still.add_argument("paths", nargs="+", type=Path, help="video files of a still scene")
    still.add_argument("--fixed", nargs="*", type=float, default=[],
                       help="also measure the 0.16.1 score at these fixed thresholds")
    corpus = sub.add_parser("corpus", help="keyframes kept on the spike corpus videos")
    corpus.add_argument("--run-dir", type=Path, default=DEFAULT_RUN,
                        help="a spike run directory with corpus/manifest.json (default: %(default)s)")
    for command in (still, corpus):
        command.add_argument("--fps", type=float, default=None, help="frames sampled per second (vision.fps)")
        command.add_argument("--json", type=Path, default=None, help="write the full report here")
    return parser


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    config = VisionConfig()
    fps = args.fps or config.fps
    if args.command == "still":
        report = measure_still(args.paths, config=config, fps=fps, fixed=args.fixed)
        print(still_markdown(report))
    else:
        report = measure_corpus(args.run_dir, config=config, fps=fps)
        print(corpus_markdown(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + "\n")
    return report


if __name__ == "__main__":
    main()
