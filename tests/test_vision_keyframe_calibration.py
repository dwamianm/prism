"""Calibrated keyframe selection for a fixed camera (dwamianm/prism#389).

The scenes are ported from the Nenavi camera demo's watch tests
(``apps/vision-demo/server/tests/test_watch.py`` in dwamianm/prism-monorepo):
a desk seen by a fixed webcam with sensor noise on every frame, a phone put
down, a person sitting down, and a hand that holds up one more finger every
two seconds. Under the 0.16.1 policy (``frame_change`` against 0.55) no
finger count is kept, because one more finger scores 0.34 to 0.48. The
calibrated default must keep every count, and must keep nothing for noise, a
12% exposure step or a few pixels of shake. With
``vision.calibrate_keyframes`` off, selection must be the 0.16.1 policy
exactly, which a frozen copy of the 0.16.1 code below checks decision by
decision. The scores asserted here were measured on these scenes
(``benchmarks/results/research/2026-11-02/KEYFRAME-CALIBRATION.md``).
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance

from benchmarks.vision import keyframe_calibration
from prme import MemoryEngine, PRMEConfig
from prme.config import VisionConfig
from prme.vision import keyframes as keyframes_module
from prme.vision import observe_source
from prme.vision.frames import decode_video_frames
from prme.vision.keyframes import (
    KeyframePolicy,
    NoiseFloor,
    aligned_frame_change,
    frame_change,
    frame_signature,
    select_keyframes,
)
from prme.vision.observe import ADMITTED
from tests.test_vision_observe import (  # noqa: F401  (isolated and quiet_structlog are autouse fixtures)
    NEEDS_FFMPEG,
    START,
    USER,
    directory_config,
    isolated,
    quiet_structlog,
)

WIDTH, HEIGHT = 640, 360  # the demo's sample size
SIZE = (1280, 720)  # the size a clip is decoded at (vision.frame_width)
SKIN = (205, 160, 135)


def desk(*, phone: bool = False, person_x: int | None = None, fingers: int | None = None) -> Image.Image:
    """The demo's desk: a shaded wall, a laptop, a red ball and a white note, at 1280x720."""
    shade = np.linspace(90, 170, WIDTH)[None, :].repeat(HEIGHT, 0)
    image = Image.fromarray(shade.astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 190, 260, 350), fill=(60, 60, 70))
    draw.ellipse((450, 210, 520, 280), fill=(200, 60, 50))
    draw.rectangle((300, 60, 550, 165), fill=(235, 235, 230))
    if phone:
        draw.rounded_rectangle((300, 225, 350, 320), 6, fill=(20, 20, 20))
    if person_x is not None:
        draw.ellipse((person_x, 75, person_x + 130, 215), fill=(180, 140, 120))
        draw.rectangle((person_x - 30, 215, person_x + 160, HEIGHT), fill=(40, 70, 120))
    if fingers is not None:
        draw.rounded_rectangle((330, 230, 430, 330), 12, fill=SKIN)  # the palm, held up
        for finger in range(fingers):
            left = 332 + finger * 20
            draw.rounded_rectangle((left, 160, left + 15, 232), 7, fill=SKIN)
    return image.resize(SIZE, Image.Resampling.BICUBIC)


def camera(image: Image.Image, rng: np.random.Generator, *, shift: tuple[int, int] = (0, 0),
           exposure: float = 1.0, noise: float = 4.0) -> Image.Image:
    """What a fixed webcam sends: the scene, moved by *shift*, at *exposure*, with sensor noise, as a JPEG."""
    canvas = Image.new("RGB", image.size)
    canvas.paste(image, shift)
    canvas = ImageEnhance.Brightness(canvas).enhance(exposure)
    noisy = np.asarray(canvas, dtype=np.float32) + rng.normal(0, noise, (image.size[1], image.size[0], 3))
    buffer = io.BytesIO()
    Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8)).save(buffer, format="JPEG")
    return Image.open(io.BytesIO(buffer.getvalue()))


def finger_frames(rng: np.random.Generator, *, per_count: int) -> list[Image.Image]:
    """The owner's test of 2026-11-02: 1 to 5 fingers, each held for *per_count* samples."""
    return [camera(desk(fingers=count), rng) for count in range(1, 6) for _ in range(per_count)]


SHAKES = [(3, 2), (-3, 2), (2, 3), (1, 1), (4, 4), (6, -4)]


def nuisance_frames(rng: np.random.Generator) -> list[Image.Image]:
    """The demo's still desk, then exposure steps of 12% each way and shakes of 1 to 6 pixels, with noise."""
    still = desk()
    frames = [camera(still, rng) for _ in range(3)]
    frames += [camera(still, rng, exposure=1.12), camera(still, rng), camera(still, rng, exposure=0.88)]
    frames += [camera(still, rng, shift=shift) for shift in SHAKES]
    return frames + [camera(still, rng)]


def kept(decisions) -> list[int]:
    return [decision.index for decision in decisions if decision.kept]


def signatures(frames) -> list[np.ndarray]:
    return [frame_signature(frame) for frame in frames]


def select(frames, *, fps: float = 1.0, **options):
    return select_keyframes(frames, fps=fps, threshold=0.55, max_gap=30.0, load=lambda signature: signature,
                            **options)


# --- the 0.16.1 policy, frozen ------------------------------------------------
# A copy of prme.vision.keyframes as 0.16.1 shipped it, so the restore test
# compares with the code that ran, not with the code under test.


def _aligned_0161(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    cross = np.fft.fft2(reference - reference.mean()) * np.conj(np.fft.fft2(candidate - candidate.mean()))
    cross /= np.abs(cross) + 1e-6
    peak = np.fft.ifft2(cross).real
    dy, dx = (int(value) for value in np.unravel_index(np.argmax(peak), peak.shape))
    if dy > reference.shape[0] // 2:
        dy -= reference.shape[0]
    if dx > reference.shape[1] // 2:
        dx -= reference.shape[1]
    return np.roll(candidate, (dy, dx), axis=(0, 1))


def frame_change_0161(reference: np.ndarray, candidate: np.ndarray) -> float:
    margin = 12
    a = reference[margin:-margin, margin:-margin]
    b = _aligned_0161(reference, candidate)[margin:-margin, margin:-margin]
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    difference = np.abs(a - b)
    rows, cols = (13, 37)
    block_h, block_w = difference.shape[0] // rows, difference.shape[1] // cols
    blocks = difference[: block_h * rows, : block_w * cols].reshape(rows, block_h, cols, block_w).mean(axis=(1, 3))
    return float(blocks.max())


def select_keyframes_0161(frames, *, fps: float, threshold: float, max_gap: float, load) -> list[tuple]:
    decisions = []
    last_signature = None
    last_time = 0.0
    for index, frame in enumerate(frames):
        time_s = index / fps
        signature = load(frame)
        if last_signature is None:
            change, reason = None, "first"
        else:
            change = round(frame_change_0161(last_signature, signature), 3)
            if change >= threshold:
                reason = "change"
            elif time_s - last_time >= max_gap:
                reason = "heartbeat"
            else:
                reason = None
        decisions.append((index, time_s, change, reason is not None, reason))
        if reason is not None:
            last_signature, last_time = signature, time_s
    return decisions


def as_0161(decisions) -> list[tuple]:
    return [(d.index, d.time_s, d.change, d.kept, d.reason) for d in decisions]


# --- the score ----------------------------------------------------------------


class TestAlignedFrameChange:
    def test_frame_change_is_still_the_0161_score(self):
        rng = np.random.default_rng(0)
        reference = frame_signature(camera(desk(), rng))
        for frame in [*nuisance_frames(rng), camera(desk(fingers=2), rng), camera(desk(phone=True), rng)]:
            candidate = frame_signature(frame)
            assert frame_change(reference, candidate) == frame_change_0161(reference, candidate)

    @pytest.mark.parametrize("seed", range(4))
    def test_noise_exposure_steps_and_shake_stay_far_under_the_floor(self, seed):
        rng = np.random.default_rng(seed)
        still = desk(fingers=3) if seed % 2 else desk()
        reference = frame_signature(camera(still, rng))
        variants = [{}, {"exposure": 1.12}, {"exposure": 0.88}, *({"shift": shift} for shift in SHAKES)]
        scores = [aligned_frame_change(reference, frame_signature(camera(still, rng, **variant)))
                  for variant in variants]
        # Measured at most 0.07 (the 12% step up); the floor is 0.30.
        assert max(scores) < 0.12, scores

    def test_the_0161_score_reads_shake_as_change_when_the_alignment_slips(self):
        # Why calibration needs the checked alignment: phase correlation locks onto
        # no shift on some noise draws, and a whole-pixel shift leaves half a pixel.
        # A slip is a matter of the noise draw: 3 of these 48 pairs scored over 0.35.
        rng = np.random.default_rng(1)
        still = desk()
        old, new = [], []
        for shift in [(4, 4), (6, 6), (8, 8), (6, -4)]:
            for _ in range(12):
                reference, moved = frame_signature(camera(still, rng)), frame_signature(camera(still, rng, shift=shift))
                old.append(frame_change(reference, moved))
                new.append(aligned_frame_change(reference, moved))
        assert max(old) > 0.35 and max(new) < 0.05

    def test_one_more_finger_scores_what_it_did(self):
        rng = np.random.default_rng(2)
        hands = [frame_signature(camera(desk(fingers=count), rng)) for count in range(1, 6)]
        for before, after in zip(hands, hands[1:]):
            new = aligned_frame_change(before, after)
            assert 0.33 < new < 0.55
            assert new == pytest.approx(frame_change(before, after), abs=0.02)

    def test_a_scene_change_still_scores_high(self):
        rng = np.random.default_rng(3)
        empty = frame_signature(camera(desk(), rng))
        assert aligned_frame_change(empty, frame_signature(camera(desk(phone=True), rng))) > 1.5
        assert aligned_frame_change(empty, frame_signature(camera(desk(person_x=200), rng))) > 1.2


# --- the noise floor ------------------------------------------------------------


class TestNoiseFloor:
    def test_the_ceiling_holds_until_the_first_still_frame(self):
        noise = NoiseFloor(ceiling=0.55)
        assert (noise.threshold, noise.median) == (0.55, None)

    def test_a_quiet_source_reaches_the_floor_and_a_noisy_one_keeps_the_ceiling(self):
        quiet, noisy, between = (NoiseFloor(ceiling=0.55) for _ in range(3))
        quiet.add(0.018)
        noisy.add(0.2)
        between.add(0.08)
        assert (quiet.threshold, noisy.threshold, between.threshold) == (0.30, 0.55, 0.40)

    def test_the_median_ignores_a_stray_score_and_the_window_forgets(self):
        noise = NoiseFloor(ceiling=0.55, window=5)
        for score in (0.02, 0.02, 0.5, 0.02):
            noise.add(score)
        assert noise.threshold == 0.30
        for _ in range(5):
            noise.add(0.2)
        assert noise.median == 0.2 and noise.threshold == 0.55

    def test_a_floor_above_the_ceiling_keeps_the_ceiling(self):
        noise = NoiseFloor(ceiling=0.4, floor=0.6)
        noise.add(0.01)
        assert noise.threshold == 0.4

    def test_window_must_hold_a_score(self):
        with pytest.raises(ValueError):
            NoiseFloor(ceiling=0.55, window=0)


# --- selection over the demo's scenes ---------------------------------------------


class TestCalibratedSelection:
    @pytest.mark.parametrize("seed", range(3))
    @pytest.mark.parametrize("fps, per_count", [(1.0, 2), (2.0, 4)])
    def test_every_finger_count_is_kept_and_none_was_before(self, seed, fps, per_count):
        frames = signatures(finger_frames(np.random.default_rng(seed), per_count=per_count))
        counts_start = [count * per_count for count in range(5)]
        decisions = select(frames, fps=fps)
        assert kept(decisions) == counts_start
        assert all(d.reason == "change" and d.threshold == 0.30 for d in decisions if d.kept and d.index)
        assert kept(select(frames, fps=fps, calibrate=False)) == [0]

    @pytest.mark.parametrize("seed", range(6))
    def test_noise_exposure_steps_and_shake_keep_nothing(self, seed):
        frames = signatures(nuisance_frames(np.random.default_rng(seed)))
        decisions = select(frames)
        assert kept(decisions) == [0]
        assert decisions[-1].threshold == 0.30
        assert kept(select(frames, calibrate=False)) == [0]

    def test_a_phone_put_down_and_a_person_sitting_down_are_kept(self):
        rng = np.random.default_rng(4)
        frames = signatures([camera(desk(), rng) for _ in range(3)] + [camera(desk(phone=True), rng) for _ in range(3)]
                            + [camera(desk(phone=True, person_x=150), rng) for _ in range(3)])
        assert kept(select(frames)) == kept(select(frames, calibrate=False)) == [0, 3, 6]

    def test_a_dim_noisy_camera_keeps_the_configured_threshold(self):
        # Low light: 15% of the exposure and eight times the sensor noise. The still
        # frames score about 0.28, so the threshold stays at 0.55, and the 0.16.1
        # score keeps noise there that the calibrated policy does not.
        rng = np.random.default_rng(0)
        frames = signatures([camera(desk(), rng, exposure=0.15, noise=32.0) for _ in range(14)])
        decisions = select(frames)
        assert kept(decisions) == [0]
        assert decisions[-1].threshold == 0.55
        assert len(kept(select(frames, calibrate=False))) > 1

    def test_the_heartbeat_still_keeps_a_still_scene(self):
        rng = np.random.default_rng(5)
        frames = signatures([camera(desk(), rng) for _ in range(7)])
        decisions = select_keyframes(frames, fps=1.0, threshold=0.55, max_gap=3.0, load=lambda s: s)
        assert [(d.index, d.reason) for d in decisions if d.kept] == [(0, "first"), (3, "heartbeat"),
                                                                        (6, "heartbeat")]

    def test_a_policy_decides_one_frame_at_a_time_like_a_clip(self):
        # A caller that receives frames one at a time (a camera session) keeps one policy.
        frames = signatures(finger_frames(np.random.default_rng(6), per_count=2))
        policy = KeyframePolicy(threshold=0.55, max_gap=30.0)
        one_by_one = [policy.decide(signature, index=index, time_s=float(index))
                      for index, signature in enumerate(frames)]
        assert one_by_one == select(frames)
        assert policy.noise is not None and policy.threshold == 0.30


# --- the old setting restores 0.16.1 exactly ------------------------------------------


class TestOldSettingRestoresSelection:
    @pytest.mark.parametrize("scene", ["fingers", "nuisance", "desk_events", "dim"])
    def test_calibrate_off_is_the_0161_policy_decision_for_decision(self, scene):
        rng = np.random.default_rng(11)
        frames = {
            "fingers": lambda: finger_frames(rng, per_count=3),
            "nuisance": lambda: nuisance_frames(rng),
            "desk_events": lambda: [camera(desk(phone=index > 2, person_x=150 if index > 5 else None), rng)
                                    for index in range(9)],
            "dim": lambda: [camera(desk(), rng, exposure=0.25, noise=16.0) for _ in range(10)],
        }[scene]()
        sequence = signatures(frames)
        for max_gap in (30.0, 2.0):
            decisions = select_keyframes(sequence, fps=1.0, threshold=0.55, max_gap=max_gap,
                                         load=lambda s: s, calibrate=False)
            reference = select_keyframes_0161(sequence, fps=1.0, threshold=0.55, max_gap=max_gap, load=lambda s: s)
            assert as_0161(decisions) == reference
            assert {decision.threshold for decision in decisions} == {0.55}


# --- through observe() ------------------------------------------------------------


def fingers_in(image: bytes) -> int:
    """Raised fingers in a frame of the desk scene: skin-colored runs along a row through the fingers."""
    picture = Image.open(io.BytesIO(image)).convert("RGB").resize((WIDTH, HEIGHT))
    row = np.asarray(picture, dtype=np.float32)[190:200, 320:440].mean(axis=0)
    skin = (row[:, 0] > 175) & (row[:, 0] - row[:, 2] > 40)
    return int(np.count_nonzero(skin[1:] & ~skin[:-1]) + skin[0])


class CountingVision:
    """Reads the finger count off the frame and quotes it, so each count is a new description."""

    model_name = "fake/counting-1"

    def __init__(self) -> None:
        self.counts: list[int] = []

    async def describe(self, image: bytes, *, prompt: str) -> str:
        count = fingers_in(image)
        self.counts.append(count)
        return f'This is a video frame of a desk. A hand is held up with "{count} fingers" raised.'


@pytest.fixture
def counting_vision(monkeypatch) -> CountingVision:
    fake = CountingVision()
    monkeypatch.setattr("prme.vision.provider.create_vision_provider", lambda config: fake)
    return fake


def write_video(frames: list[Image.Image], directory: Path, name: str) -> Path:
    folder = directory / f"{name}_frames"
    folder.mkdir()
    for index, frame in enumerate(frames):
        frame.save(folder / f"{index:05d}.png")
    path = directory / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", str(folder / "%05d.png"), "-r", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture
def finger_video(tmp_path) -> Path:
    """Ten seconds at one frame a second: 1 to 5 fingers, two seconds each."""
    return write_video(finger_frames(np.random.default_rng(0), per_count=2), tmp_path, "fingers")


@pytest.fixture
def nuisance_video(tmp_path) -> Path:
    return write_video(nuisance_frames(np.random.default_rng(1)), tmp_path, "nuisance")


@NEEDS_FFMPEG
async def test_observe_keeps_every_finger_count_by_default(tmp_path, finger_video, counting_vision):
    config = directory_config(tmp_path / "pack", max_gap_seconds=30.0)
    assert config.vision.calibrate_keyframes is True
    async with MemoryEngine.open(config) as engine:
        report = await observe_source(engine, finger_video, user_id=USER, captured_at=START)
        assert report.frames == 10
        assert [(entry.index, entry.reason) for entry in report.keyframe_log if entry.kept] == [
            (0, "first"), (2, "change"), (4, "change"), (6, "change"), (8, "change")]
        assert all(entry.threshold == 0.30 for entry in report.keyframe_log[2:])
        assert counting_vision.counts == [1, 2, 3, 4, 5]
        assert report.admitted == 5
        assert [record.time_s for record in report.observations if record.status == ADMITTED] == [0.0, 2.0, 4.0,
                                                                                                  6.0, 8.0]
        assert json.loads(json.dumps(report.to_dict(), default=str))["keyframe_log"][2]["threshold"] == 0.30


@NEEDS_FFMPEG
async def test_observe_with_calibration_off_keeps_the_0161_frames(tmp_path, finger_video, counting_vision):
    config = directory_config(tmp_path / "pack", max_gap_seconds=30.0, calibrate_keyframes=False)
    async with MemoryEngine.open(config) as engine:
        report = await observe_source(engine, finger_video, user_id=USER, captured_at=START)
    assert [entry.index for entry in report.keyframe_log if entry.kept] == [0]
    assert counting_vision.counts == [1] and report.admitted == 1
    frames = decode_video_frames(finger_video, tmp_path / "decoded", fps=1.0, width=1280)
    reference = select_keyframes_0161(frames, fps=1.0, threshold=0.55, max_gap=30.0, load=frame_signature)
    assert as_0161(report.keyframe_log) == reference


@NEEDS_FFMPEG
async def test_observe_keeps_nothing_for_noise_exposure_and_shake(tmp_path, nuisance_video, counting_vision):
    for calibrate in (True, False):
        config = directory_config(tmp_path / f"pack-{calibrate}", max_gap_seconds=30.0,
                                  calibrate_keyframes=calibrate)
        async with MemoryEngine.open(config) as engine:
            report = await observe_source(engine, nuisance_video, user_id=USER, captured_at=START)
        assert report.frames == 13
        assert [entry.index for entry in report.keyframe_log if entry.kept] == [0], calibrate


# --- configuration ------------------------------------------------------------------


def test_calibration_is_on_by_default_and_the_environment_turns_it_off(monkeypatch):
    vision = VisionConfig()
    assert (vision.calibrate_keyframes, vision.min_change_threshold, vision.noise_multiplier) == (True, 0.30, 5.0)
    assert (keyframes_module.MIN_CHANGE_THRESHOLD, keyframes_module.NOISE_MULTIPLIER) == (0.30, 5.0)
    monkeypatch.setenv("PRME_VISION__CALIBRATE_KEYFRAMES", "false")
    monkeypatch.setenv("PRME_VISION__MIN_CHANGE_THRESHOLD", "0.25")
    loaded = PRMEConfig().vision
    assert (loaded.calibrate_keyframes, loaded.min_change_threshold) == (False, 0.25)
    monkeypatch.delenv("PRME_VISION__CALIBRATE_KEYFRAMES")
    monkeypatch.setenv("PRME_VISION_CALIBRATE_KEYFRAMES", "false")
    assert VisionConfig().calibrate_keyframes is False
    with pytest.raises(ValueError):
        VisionConfig(noise_multiplier=0)
    with pytest.raises(ValueError):
        VisionConfig(min_change_threshold=-0.1)


# --- the still-scene recording script --------------------------------------------------


@NEEDS_FFMPEG
def test_still_scene_script_reports_false_keeps_under_both_settings(tmp_path, capsys):
    rng = np.random.default_rng(7)
    still = write_video([camera(desk(), rng) for _ in range(40)], tmp_path, "still")
    jumpy = write_video([camera(desk(phone=index % 10 >= 5), rng) for index in range(20)], tmp_path, "jumpy")
    out = tmp_path / "report.json"
    report = keyframe_calibration.main(["still", str(still), str(jumpy), "--fixed", "0.30", "--json", str(out)])
    assert json.loads(out.read_text()) == json.loads(json.dumps(report))
    printed = capsys.readouterr().out
    assert "False-keep rate" in printed and str(still) in printed
    names = ["0.16.1 (calibrate_keyframes=false)", "calibrated (default)", "0.16.1 score, fixed 0.30"]
    quiet, moving = (clip["settings"] for clip in report["clips"])
    assert list(quiet) == names
    for name in names:
        row = quiet[name]
        assert (row["frames"], row["minutes"], row["false_keeps"], row["false_keep_rate"]) == (40, 0.67, 0, 0.0)
        assert row["keeps"] == 2 and row["heartbeats"] == 1  # the first frame and one heartbeat at 30 s
        assert row["keeps_per_minute"] == 3.0
    assert quiet["calibrated (default)"]["threshold_max"] == 0.30
    assert quiet["0.16.1 (calibrate_keyframes=false)"]["threshold_min"] == 0.55
    # A phone put down and picked up every five seconds is three changes, which a
    # still-scene clip would count as false keeps.
    assert moving["calibrated (default)"]["false_keeps"] == 3
    assert moving["calibrated (default)"]["false_keep_rate"] == round(3 / 19, 4)
