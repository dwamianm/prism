"""Keyframe selection and thumbnails (RFC-0018, "Keyframe policy").

Continuous capture is mostly repetition. Two of the three filters live here,
each cheaper than the stage it protects:

1. **Pixel change.** Each frame is reduced to a small grayscale signature,
   blurred a little, aligned to the last kept frame by phase correlation so
   camera shake does not read as change, contrast-normalized, and compared
   block by block. A frame is kept when the largest block difference reaches
   ``threshold``.
2. **Heartbeat.** A frame is kept every ``max_gap`` seconds regardless, so a
   long static scene is still sampled and a text-only change near the noise
   floor is caught within one gap.

The third filter, dropping a frame whose description repeats the previous
kept frame's, needs the vision model's output and lives in
:mod:`prme.vision.repeat`. Every decision here is returned with its score so
the caller can log it. The thresholds were measured on the spike corpus
(``benchmarks/results/research/2026-10-15/VISUAL-MEMORY-SPIKE.md``) and are
configuration, not constants.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

SIGNATURE_SIZE = (320, 180)
SIGNATURE_MARGIN = 12
SIGNATURE_BLOCKS = (13, 37)


@dataclass(frozen=True)
class KeyframeDecision:
    """Why one sampled frame was kept or dropped by the pixel-change filter."""

    index: int
    time_s: float
    change: float | None
    kept: bool
    reason: str | None  # "first", "change" or "heartbeat" when kept

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


def _aligned(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """Shift *candidate* by the whole-pixel translation that best matches *reference* (phase correlation)."""
    cross = np.fft.fft2(reference - reference.mean()) * np.conj(np.fft.fft2(candidate - candidate.mean()))
    cross /= np.abs(cross) + 1e-6
    peak = np.fft.ifft2(cross).real
    dy, dx = (int(value) for value in np.unravel_index(np.argmax(peak), peak.shape))
    if dy > reference.shape[0] // 2:
        dy -= reference.shape[0]
    if dx > reference.shape[1] // 2:
        dx -= reference.shape[1]
    return np.roll(candidate, (dy, dx), axis=(0, 1))


def frame_change(reference: np.ndarray, candidate: np.ndarray) -> float:
    """How much *candidate* differs from *reference* after alignment and contrast normalization.

    The value is the largest block-mean absolute difference between the two
    z-scored signatures over a 13x37 grid, so a change confined to one line
    of text still registers while exposure drift and a few pixels of shake do
    not. Measured on the spike corpus: same-layout slides whose text changed
    scored about 0.7, any other scene change above 2.3, shake and exposure
    noise at most 0.37, and consecutive seconds of a slow aerial pan about
    0.5.
    """
    margin = SIGNATURE_MARGIN
    a = reference[margin:-margin, margin:-margin]
    b = _aligned(reference, candidate)[margin:-margin, margin:-margin]
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    difference = np.abs(a - b)
    rows, cols = SIGNATURE_BLOCKS
    block_h, block_w = difference.shape[0] // rows, difference.shape[1] // cols
    blocks = difference[: block_h * rows, : block_w * cols].reshape(rows, block_h, cols, block_w).mean(axis=(1, 3))
    return float(blocks.max())


FrameSource = bytes | str | Path | Image.Image


def select_keyframes(
    frames: Sequence[FrameSource] | Iterable[FrameSource],
    *,
    fps: float,
    threshold: float,
    max_gap: float,
    load: Callable[[Any], np.ndarray] = frame_signature,
) -> list[KeyframeDecision]:
    """Decide for each sampled frame whether it starts a new keyframe.

    *frames* are consecutive samples ``fps`` apart, given as image bytes,
    paths or PIL images. The first frame is always kept; a later frame is
    kept when it changed from the last kept frame by *threshold* or more, or
    when *max_gap* seconds passed since the last kept frame. Only the last
    kept frame's signature is held in memory, so a long recording costs one
    signature at a time.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    decisions: list[KeyframeDecision] = []
    last_signature: np.ndarray | None = None
    last_time = 0.0
    for index, frame in enumerate(frames):
        time_s = index / fps
        signature = load(frame)
        if last_signature is None:
            change, reason = None, "first"
        else:
            change = round(frame_change(last_signature, signature), 3)
            if change >= threshold:
                reason = "change"
            elif time_s - last_time >= max_gap:
                reason = "heartbeat"
            else:
                reason = None
        decisions.append(KeyframeDecision(index=index, time_s=time_s, change=change, kept=reason is not None,
                                          reason=reason))
        if reason is not None:
            last_signature, last_time = signature, time_s
    return decisions


def thumbnail_bytes(image: FrameSource, *, width: int = 320, quality: int = 80) -> bytes:
    """Return a JPEG thumbnail of *image* no wider or taller than *width* pixels."""
    picture = load_image(image).copy()
    picture.thumbnail((width, width))
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
