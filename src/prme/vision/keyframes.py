"""Keyframe selection and thumbnails (RFC-0018, "Keyframe policy").

Continuous capture is mostly repetition. Two of the three filters live here,
each cheaper than the stage it protects:

1. **Pixel change.** Each frame is reduced to a small grayscale signature,
   blurred a little, aligned to the last kept frame so camera shake does not
   read as change, contrast-normalized, and compared block by block. A frame
   is kept when the largest block difference reaches the threshold.
2. **Heartbeat.** A frame is kept every ``max_gap`` seconds regardless, so a
   long static scene is still sampled and a text-only change near the noise
   floor is caught within one gap.

By default (``vision.calibrate_keyframes``, dwamianm/prism#389) the threshold
is calibrated for each video from its own noise floor, and the alignment is
checked by the difference it leaves (:func:`aligned_frame_change`). A fixed
camera, whose still frames score about 0.02, then keeps a change as small as
one more raised finger (0.34 or more), while a hand-held recording or a pan,
whose still frames score far more, keeps the configured threshold. With
calibration off, selection is the 0.16.1 policy exactly: :func:`frame_change`
against a fixed ``threshold``. :class:`KeyframePolicy` holds the state of one
video, or of one camera session: a live observe session
(:mod:`prme.vision.stream`) keeps one per session and scores each frame
without keeping it until the picture has held still.

The third filter, dropping a frame whose description repeats the previous
kept frame's, needs the vision model's output and lives in
:mod:`prme.vision.repeat`. Every decision here is returned with its score and
threshold so the caller can log it. The thresholds were measured on the spike
corpus (``benchmarks/results/research/2026-10-15/VISUAL-MEMORY-SPIKE.md``) and
on synthetic fixed-camera scenes
(``benchmarks/results/research/2026-11-02/KEYFRAME-CALIBRATION.md``), and are
configuration, not constants.
"""

from __future__ import annotations

import io
import statistics
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

SIGNATURE_SIZE = (320, 180)
SIGNATURE_MARGIN = 12
SIGNATURE_BLOCKS = (13, 37)
# Calibrated selection (dwamianm/prism#389). The defaults match VisionConfig.
MIN_CHANGE_THRESHOLD = 0.30
NOISE_MULTIPLIER = 5.0
NOISE_WINDOW = 20  # still-frame scores whose median is the noise floor
ALIGN_RADIUS = 3  # signature pixels searched around no shift: 12 pixels of a 1280 wide frame


@dataclass(frozen=True)
class KeyframeDecision:
    """Why one sampled frame was kept or dropped by the pixel-change filter."""

    index: int
    time_s: float
    change: float | None
    kept: bool
    reason: str | None  # "first", "change" or "heartbeat" when kept
    threshold: float | None = None  # the threshold *change* was compared with

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_image(source: bytes | str | Path | Image.Image) -> Image.Image:
    """Open *source* as an RGB image with its EXIF orientation applied."""
    if isinstance(source, Image.Image):
        image = source
    elif isinstance(source, (bytes, bytearray)):
        image = Image.open(io.BytesIO(source))
    else:
        image = Image.open(source)
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def frame_signature(image: Image.Image | bytes | str | Path) -> np.ndarray:
    """A small grayscale copy, blurred a little so sub-pixel camera shake does not read as change."""
    small = load_image(image).convert("L").resize(SIGNATURE_SIZE, Image.Resampling.BOX)
    return np.asarray(small.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)


def _phase_shift(reference: np.ndarray, candidate: np.ndarray) -> tuple[int, int]:
    """The whole-pixel translation of *candidate* that best matches *reference* by phase correlation."""
    cross = np.fft.fft2(reference - reference.mean()) * np.conj(np.fft.fft2(candidate - candidate.mean()))
    cross /= np.abs(cross) + 1e-6
    peak = np.fft.ifft2(cross).real
    dy, dx = (int(value) for value in np.unravel_index(np.argmax(peak), peak.shape))
    if dy > reference.shape[0] // 2:
        dy -= reference.shape[0]
    if dx > reference.shape[1] // 2:
        dx -= reference.shape[1]
    return dy, dx


def _aligned(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """Shift *candidate* by the whole-pixel translation that best matches *reference* (phase correlation)."""
    return np.roll(candidate, _phase_shift(reference, candidate), axis=(0, 1))


def _largest_block_difference(reference: np.ndarray, aligned: np.ndarray) -> float:
    margin = SIGNATURE_MARGIN
    a = reference[margin:-margin, margin:-margin]
    b = aligned[margin:-margin, margin:-margin]
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    difference = np.abs(a - b)
    rows, cols = SIGNATURE_BLOCKS
    block_h, block_w = difference.shape[0] // rows, difference.shape[1] // cols
    blocks = difference[: block_h * rows, : block_w * cols].reshape(rows, block_h, cols, block_w).mean(axis=(1, 3))
    return float(blocks.max())


def frame_change(reference: np.ndarray, candidate: np.ndarray) -> float:
    """How much *candidate* differs from *reference* after alignment and contrast normalization.

    The value is the largest block-mean absolute difference between the two
    z-scored signatures over a 13x37 grid, so a change confined to one line
    of text still registers while exposure drift and a few pixels of shake do
    not. Measured on the spike corpus: same-layout slides whose text changed
    scored about 0.7, any other scene change above 2.3, shake and exposure
    noise at most 0.37, and consecutive seconds of a slow aerial pan about
    0.5. This is the 0.16.1 score, kept unchanged; calibrated selection uses
    :func:`aligned_frame_change`.
    """
    return _largest_block_difference(reference, _aligned(reference, candidate))


def _shifted(image: np.ndarray, dy2: int, dx2: int) -> np.ndarray:
    """*image* moved by ``(dy2 / 2, dx2 / 2)`` pixels; a half pixel is the mean of the two whole-pixel moves."""
    rows = [dy2 // 2] if dy2 % 2 == 0 else [(dy2 - 1) // 2, (dy2 + 1) // 2]
    cols = [dx2 // 2] if dx2 % 2 == 0 else [(dx2 - 1) // 2, (dx2 + 1) // 2]
    moved = [np.roll(image, (dy, dx), axis=(0, 1)) for dy in rows for dx in cols]
    return moved[0] if len(moved) == 1 else sum(moved[1:], moved[0]) / len(moved)


def _matched(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """Move *candidate* by the shift, to half a pixel, that leaves the smallest difference from *reference*.

    The candidates are the phase-correlation shift and every whole-pixel
    shift within ``ALIGN_RADIUS`` of none; the best of them is refined by
    half a pixel. Phase correlation alone sometimes returns no shift for a
    noisy, low-texture frame, and a whole-pixel shift leaves half a pixel of
    residual: on a synthetic desk those scored up to 0.49 for a few pixels of
    shake. Checking each shift by the difference it leaves brings that under
    0.07.
    """
    margin = SIGNATURE_MARGIN
    inner = (slice(margin, -margin), slice(margin, -margin))
    reference_inner = reference[inner]
    reference_inner = (reference_inner - reference_inner.mean()) / (reference_inner.std() + 1e-6)

    def residual(shift: tuple[int, int]) -> float:
        moved = _shifted(candidate, *shift)[inner]
        moved = (moved - moved.mean()) / (moved.std() + 1e-6)
        return float(np.abs(reference_inner - moved).mean())

    dy, dx = _phase_shift(reference, candidate)
    reach = 2 * ALIGN_RADIUS
    shifts = {(y, x) for y in range(-reach, reach + 1, 2) for x in range(-reach, reach + 1, 2)} | {(2 * dy, 2 * dx)}
    # Sorted, so a tie goes to the same shift on every machine.
    best = min(sorted(shifts), key=residual)
    best = min(sorted((best[0] + y, best[1] + x) for y in (-1, 0, 1) for x in (-1, 0, 1)), key=residual)
    return _shifted(candidate, *best)


def aligned_frame_change(reference: np.ndarray, candidate: np.ndarray) -> float:
    """:func:`frame_change` with an alignment checked by the difference it leaves (dwamianm/prism#389).

    The score is the same largest block difference, so real changes score
    what they did (a different scene above 2.3, one more raised finger on a
    synthetic hand 0.34 to 0.48), but shake no longer reads as change when
    phase correlation picks the wrong shift: 1 to 6 pixels of shake on a
    fixed synthetic camera scored at most 0.07, where the 0.16.1 score
    reached 0.49, and the still frames of the hand-held spike recording at
    most 0.17 against their scene's first frame, where it reached 0.61. It
    costs about five times the 0.16.1 score, a few milliseconds a frame.
    """
    return _largest_block_difference(reference, _matched(reference, candidate))


class NoiseFloor:
    """The change threshold of one video or camera session, calibrated from its still frames.

    Every frame that scored under the threshold is a still frame, and its
    score is noise. The threshold is ``multiplier`` times the median of the
    last ``window`` still-frame scores, never under ``floor`` and never over
    ``ceiling`` (``vision.change_threshold``): a source is never less
    sensitive than with calibration off. Before the first still frame it is
    the ceiling. One still frame is enough to lower it, so a pose held for
    two samples is kept; the median keeps one stray score from moving it.
    """

    def __init__(self, *, ceiling: float, floor: float = MIN_CHANGE_THRESHOLD,
                 multiplier: float = NOISE_MULTIPLIER, window: int = NOISE_WINDOW) -> None:
        if window < 1:
            raise ValueError("window must be at least 1")
        self.ceiling = ceiling
        self.floor = floor
        self.multiplier = multiplier
        self._scores: deque[float] = deque(maxlen=window)

    @property
    def median(self) -> float | None:
        """The median still-frame score, or None before the first still frame."""
        return float(statistics.median(self._scores)) if self._scores else None

    @property
    def threshold(self) -> float:
        median = self.median
        if median is None:
            return self.ceiling
        return round(min(self.ceiling, max(self.floor, self.multiplier * median)), 3)

    def add(self, score: float) -> None:
        """Record the score of a frame that stayed under the threshold."""
        self._scores.append(score)


class KeyframePolicy:
    """The keyframe decision for one video or one camera session, one frame at a time.

    It holds the last kept frame's signature and time and, when calibrated,
    the session's :class:`NoiseFloor`. :func:`select_keyframes` uses one per
    video through :meth:`decide`; a live observe session
    (:class:`prme.vision.stream.StreamPolicy`) keeps one per session and uses
    :meth:`change`, :meth:`still` and :meth:`keep`, so it can wait for the
    picture to hold still before it keeps a frame. With ``calibrate`` off
    the decision is the 0.16.1 policy exactly: :func:`frame_change` against
    the fixed *threshold*.
    """

    def __init__(self, *, threshold: float, max_gap: float, calibrate: bool = True,
                 min_threshold: float = MIN_CHANGE_THRESHOLD, noise_multiplier: float = NOISE_MULTIPLIER) -> None:
        self.max_gap = max_gap
        self.noise = NoiseFloor(ceiling=threshold, floor=min_threshold, multiplier=noise_multiplier) \
            if calibrate else None
        self._threshold = threshold
        self._last_signature: np.ndarray | None = None
        self._last_time = 0.0

    @property
    def threshold(self) -> float:
        """The threshold the next frame is compared with."""
        return self._threshold if self.noise is None else self.noise.threshold

    @property
    def last_kept_time(self) -> float | None:
        """When the last kept frame was sampled, or None before the first."""
        return None if self._last_signature is None else self._last_time

    def compare(self, reference: np.ndarray, candidate: np.ndarray) -> float:
        """This policy's change score: :func:`aligned_frame_change` when calibrated, else :func:`frame_change`."""
        score = frame_change if self.noise is None else aligned_frame_change
        return round(score(reference, candidate), 3)

    def change(self, signature: np.ndarray) -> float | None:
        """How much *signature* changed from the last kept frame, or None before the first; nothing is kept."""
        return None if self._last_signature is None else self.compare(self._last_signature, signature)

    def still(self, change: float) -> None:
        """Record the score of a frame that stayed under the threshold (the noise floor, when calibrated)."""
        if self.noise is not None:
            self.noise.add(change)

    def keep(self, signature: np.ndarray, *, time_s: float) -> None:
        """Make *signature*, sampled at *time_s* seconds, the last kept frame."""
        self._last_signature, self._last_time = signature, time_s

    def decide(self, signature: np.ndarray, *, index: int, time_s: float) -> KeyframeDecision:
        """Keep or drop the frame with *signature*, sampled at *time_s* seconds."""
        threshold = self.threshold
        change = self.change(signature)
        if change is None:
            reason: str | None = "first"
        elif change >= threshold:
            reason = "change"
        elif time_s - self._last_time >= self.max_gap:
            reason = "heartbeat"
        else:
            reason = None
        if change is not None and change < threshold:
            self.still(change)
        if reason is not None:
            self.keep(signature, time_s=time_s)
        return KeyframeDecision(index=index, time_s=time_s, change=change, kept=reason is not None, reason=reason,
                                threshold=threshold)


FrameSource = bytes | str | Path | Image.Image


def select_keyframes(
    frames: Sequence[FrameSource] | Iterable[FrameSource],
    *,
    fps: float,
    threshold: float,
    max_gap: float,
    load: Callable[[Any], np.ndarray] = frame_signature,
    calibrate: bool = True,
    min_threshold: float = MIN_CHANGE_THRESHOLD,
    noise_multiplier: float = NOISE_MULTIPLIER,
) -> list[KeyframeDecision]:
    """Decide for each sampled frame whether it starts a new keyframe.

    *frames* are consecutive samples ``fps`` apart, given as image bytes,
    paths or PIL images. The first frame is always kept; a later frame is
    kept when it changed from the last kept frame by the threshold or more,
    or when *max_gap* seconds passed since the last kept frame. With
    *calibrate* (the default) the threshold is calibrated for this video
    between *min_threshold* and *threshold* (see :class:`NoiseFloor`); with
    it off, the threshold is *threshold* and the score :func:`frame_change`,
    as in 0.16.1. Only the last kept frame's signature is held in memory, so
    a long recording costs one signature at a time.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    policy = KeyframePolicy(threshold=threshold, max_gap=max_gap, calibrate=calibrate, min_threshold=min_threshold,
                            noise_multiplier=noise_multiplier)
    return [policy.decide(load(frame), index=index, time_s=index / fps) for index, frame in enumerate(frames)]


def thumbnail_bytes(image: FrameSource, *, width: int = 320, quality: int = 80) -> bytes:
    """Return a JPEG thumbnail of *image* no wider or taller than *width* pixels."""
    picture = load_image(image).copy()
    picture.thumbnail((width, width))
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
