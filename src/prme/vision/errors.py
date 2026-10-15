"""Errors of the visual memory pipeline (RFC-0018)."""

from __future__ import annotations


class VisionError(Exception):
    """Base of every error raised while observing an image or a video."""


class VisionDisabledError(VisionError):
    """observe() was called while ``vision.enabled`` is False."""


class UnsupportedSourceError(VisionError):
    """The source is neither an image nor a video that this release can read."""


class FrameDecodeError(VisionError):
    """Frames could not be decoded from a video, usually because ffmpeg is missing or failed."""


class ObservationError(VisionError):
    """An observe() run did not admit every keyframe; ``report`` says what it did admit.

    Raised by ``MemoryEngine.observe()`` when any frame failed, and by
    ``observe_source()`` when the vision model or the pack failed part way,
    so the events admitted before the failure are not lost: ``report.event_ids``
    lists them.
    """

    def __init__(self, message: str, *, report) -> None:
        self.report = report
        super().__init__(message)
