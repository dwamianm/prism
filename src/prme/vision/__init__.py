"""Visual memory: images and video as observation events (RFC-0018).

Phase 1 covers saved photos and video files through
:meth:`prme.storage.engine.MemoryEngine.observe`,
:meth:`prme.client.MemoryClient.observe` and ``prme observe``. The pieces:

- :mod:`prme.vision.frames` resolves a source, decodes video frames with
  ffmpeg and works out the capture clock.
- :mod:`prme.vision.keyframes` keeps the frames that changed (pixel change
  plus a heartbeat) and makes thumbnails.
- :mod:`prme.vision.describe` holds the versioned description prompt and
  reads the values a description quotes.
- :mod:`prme.vision.provider` describes a frame with a vision model.
- :mod:`prme.vision.repeat` drops a description that repeats the previous
  kept frame's.
- :mod:`prme.vision.observe` runs them in order and admits each description
  as an event.

Everything is off until ``vision.enabled`` is set (RFC-0018, "Public surface").
"""

from prme.vision.errors import (
    FrameDecodeError,
    ObservationError,
    UnsupportedSourceError,
    VisionDisabledError,
    VisionError,
)
from prme.vision.observe import ObservationRecord, ObservationReport, observe_source
from prme.vision.provider import OllamaVisionProvider, VisionProvider, create_vision_provider

__all__ = [
    "FrameDecodeError",
    "ObservationError",
    "ObservationRecord",
    "ObservationReport",
    "OllamaVisionProvider",
    "UnsupportedSourceError",
    "VisionDisabledError",
    "VisionError",
    "VisionProvider",
    "create_vision_provider",
    "observe_source",
]
