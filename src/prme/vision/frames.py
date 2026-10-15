"""Sources, frames and capture clocks (RFC-0018).

A source is an image file or a video file, given by path or as bytes. This
module tells the two apart, decodes a video into frames with ``ffmpeg``,
and works out the capture clock that becomes each observation's
``event_time``: the time the caller gives, else the photo's EXIF capture
time or the video container's creation time, else the file's modification
time. Which one was used is recorded on the observation as
``metadata.capture_clock``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from prme.vision.errors import FrameDecodeError, UnsupportedSourceError

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
                            ".heic", ".heif", ".avif"})
VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg"})
HEIF_SUFFIXES = frozenset({".heic", ".heif", ".avif"})
"""Still images in an ISO base media container; Pillow reads them only with pillow-heif."""
HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"})
DECODE_TIMEOUT_SECONDS = 3600.0

PHOTO = "photo"
VIDEO_FRAME = "video_frame"

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_OFFSET_TIME_ORIGINAL = 36881
_EXIF_IFD = 34665


@dataclass(frozen=True)
class Source:
    """A resolved source: where its bytes are on disk for this run, and what it is."""

    kind: str  # PHOTO or VIDEO_FRAME
    path: Path  # the file itself, or a temporary copy of given bytes
    name: str  # the display name recorded as metadata.source_file
    sha256: str  # of the whole source file
    given_path: str | None  # the caller's path, None for bytes


def sniff_kind(head: bytes) -> str | None:
    """Return the source kind that the first bytes of a file identify, or None."""
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")):
        return PHOTO
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return PHOTO
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return PHOTO
    if head[4:8] == b"ftyp":
        # HEIC, HEIF and AVIF stills share the ISO base media container with
        # MP4 and MOV; the brand tells them apart.
        return PHOTO if head[8:12] in HEIF_BRANDS else VIDEO_FRAME
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free"):
        return VIDEO_FRAME
    if head[:4] == b"\x1aE\xdf\xa3":  # Matroska and WebM
        return VIDEO_FRAME
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return VIDEO_FRAME
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):  # MPEG program stream
        return VIDEO_FRAME
    return None


def kind_of(path: Path | None, head: bytes) -> str:
    """Decide the source kind from the file suffix, then from the bytes."""
    suffix = path.suffix.casefold() if path is not None else ""
    if suffix in IMAGE_SUFFIXES:
        return PHOTO
    if suffix in VIDEO_SUFFIXES:
        return VIDEO_FRAME
    sniffed = sniff_kind(head)
    if sniffed is None:
        where = str(path) if path is not None else "the given bytes"
        raise UnsupportedSourceError(
            f"{where} is not an image or video this release can read; images: "
            f"{', '.join(sorted(IMAGE_SUFFIXES))}; videos: {', '.join(sorted(VIDEO_SUFFIXES))}"
        )
    return sniffed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source(source: str | os.PathLike[str] | bytes, *, name: str | None, workdir: Path) -> Source:
    """Resolve a path or bytes into a :class:`Source`, copying bytes into *workdir*.

    A photo is checked to be readable before anything is described, so an
    image format Pillow cannot open fails here with a clear message.
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if not data:
            raise UnsupportedSourceError("The given bytes are empty")
        guess = Path(name) if name else None
        kind = kind_of(guess, data[:16])
        heif = sniff_kind(data[:16]) == PHOTO and data[4:8] == b"ftyp"
        suffix = guess.suffix if guess and guess.suffix else (".heic" if heif else ".jpg" if kind == PHOTO else ".mp4")
        path = workdir / f"source{suffix}"
        path.write_bytes(data)
        resolved = Source(kind=kind, path=path, name=name or f"bytes:{hashlib.sha256(data).hexdigest()[:12]}",
                          sha256=hashlib.sha256(data).hexdigest(), given_path=None)
    else:
        path = Path(os.fspath(source))
        if not path.is_file():
            raise FileNotFoundError(f"Source not found: {path}")
        with path.open("rb") as handle:
            head = handle.read(16)
        resolved = Source(kind=kind_of(path, head), path=path, name=name or path.name, sha256=sha256_file(path),
                          given_path=str(path.resolve()))
    if resolved.kind == PHOTO:
        check_photo_readable(resolved.path)
    return resolved


_heif_registered = False


def _register_heif() -> bool:
    """Teach Pillow HEIF through pillow-heif when it is installed; return whether it is."""
    global _heif_registered
    if not _heif_registered:
        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            return False
        register_heif_opener()
        _heif_registered = True
    return True


def check_photo_readable(path: Path) -> None:
    """Raise :class:`UnsupportedSourceError` unless Pillow can open the photo at *path*."""
    if path.suffix.casefold() in HEIF_SUFFIXES or _is_heif_file(path):
        if not _register_heif():
            raise UnsupportedSourceError(
                f"{path} is a HEIF image (HEIC, HEIF or AVIF); install pillow-heif to read it, "
                "or convert it to JPEG first"
            )
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001 - Pillow raises several types for an unreadable file
        raise UnsupportedSourceError(f"{path} is not an image Pillow can read: {exc}") from exc


def _is_heif_file(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(16)
    return head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS


def photo_frame_bytes(path: Path) -> bytes:
    """The bytes the vision model, the thumbnail and the frame hash see for a photo.

    JPEG, PNG and the other formats Pillow and the model read directly are
    used as they are, so the frame's SHA-256 is the file's. A HEIF image is
    re-encoded as JPEG (quality 92) because vision servers do not take HEIF.
    """
    if path.suffix.casefold() in HEIF_SUFFIXES or _is_heif_file(path):
        _register_heif()
        from PIL import ImageOps

        with Image.open(path) as image:
            picture = ImageOps.exif_transpose(image).convert("RGB")
            exif = image.info.get("exif")
        buffer = io.BytesIO()
        if exif:
            picture.save(buffer, format="JPEG", quality=92, exif=exif)
        else:
            picture.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()
    return path.read_bytes()


# --- capture clocks ---------------------------------------------------------


def file_modified_at(path: Path) -> datetime:
    """The file's modification time as an aware datetime in the local timezone."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()


def _parse_exif_datetime(value: str, offset: str | None) -> datetime | None:
    try:
        moment = datetime.strptime(value.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if offset:
        try:
            return moment.replace(tzinfo=datetime.strptime(offset.strip(), "%z").tzinfo)
        except ValueError:
            pass
    # A naive EXIF time is the camera's wall clock. astimezone() on a naive
    # datetime applies the local rules for that date, so a summer photo keeps
    # its summer offset when it is observed in winter.
    return moment.astimezone()


def photo_captured_at(path: Path) -> datetime | None:
    """The photo's EXIF capture time, or None when the file carries none."""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            value = exif.get(_EXIF_DATETIME_ORIGINAL)
            offset = exif.get(_EXIF_OFFSET_TIME_ORIGINAL)
            if value is None:
                ifd = exif.get_ifd(_EXIF_IFD)
                value = ifd.get(_EXIF_DATETIME_ORIGINAL)
                offset = ifd.get(_EXIF_OFFSET_TIME_ORIGINAL)
    except Exception:  # noqa: BLE001 - a clock is a best guess; the caller falls back to the file time
        return None
    if not isinstance(value, str):
        return None
    return _parse_exif_datetime(value, offset if isinstance(offset, str) else None)


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float | None
    created_at: datetime | None


def probe_video(path: Path) -> VideoInfo:
    """Read a video's duration and container creation time with ffprobe, when available."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return VideoInfo(duration_s=None, created_at=None)
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=120,
        )
        info = json.loads(completed.stdout or "{}").get("format", {})
    except (subprocess.SubprocessError, OSError, ValueError):
        return VideoInfo(duration_s=None, created_at=None)
    duration = info.get("duration")
    created = (info.get("tags") or {}).get("creation_time")
    created_at = None
    if isinstance(created, str):
        try:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
    try:
        duration_s = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_s = None
    return VideoInfo(duration_s=duration_s, created_at=created_at)


def capture_clock(source: Source, captured_at: datetime | None) -> tuple[datetime, str]:
    """Return the capture clock of *source* and where it came from.

    The caller's *captured_at* wins (``"given"``). A photo then uses its
    EXIF capture time (``"exif"``); a video the container's creation time
    (``"container"``), taken as the recording's start. Without either, the
    file's modification time stands in: for a photo as is, for a video less
    its duration when ffprobe reports one (``"file"``). Bytes without a file
    get the current time (``"now"``).
    """
    if captured_at is not None:
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("captured_at must be a timezone-aware datetime")
        return captured_at, "given"
    if source.kind == PHOTO:
        exif_time = photo_captured_at(source.path)
        if exif_time is not None:
            return exif_time, "exif"
        if source.given_path is None:
            return datetime.now().astimezone(), "now"
        return file_modified_at(source.path), "file"
    info = probe_video(source.path)
    if info.created_at is not None:
        return info.created_at.astimezone(), "container"
    if source.given_path is None:
        return datetime.now().astimezone(), "now"
    modified = file_modified_at(source.path)
    if info.duration_s:
        modified -= timedelta(seconds=info.duration_s)
    return modified, "file"


# --- video frames -----------------------------------------------------------


def decode_video_frames(video: Path, out_dir: Path, *, fps: float, width: int,
                        timeout: float = DECODE_TIMEOUT_SECONDS) -> list[Path]:
    """Sample *video* at *fps* frames per second into JPEG files under *out_dir*, in order.

    Frames are scaled to *width* pixels wide. ``ffmpeg`` must be installed;
    hardware decoding is used when the build offers it, so the decoded
    pixels, and with them each frame's SHA-256, are stable on one machine
    but may differ between decoders. The already-observed check keys on the
    source hash and the frame time for that reason. ffmpeg is killed after
    *timeout* seconds.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FrameDecodeError("ffmpeg is not installed; it is needed to read video (images need no ffmpeg)")
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-v", "error", "-nostdin", "-hwaccel", "auto", "-i", str(video),
               "-vf", f"fps={fps},scale={width}:-2", "-q:v", "3", str(out_dir / "%06d.jpg")]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        raise FrameDecodeError(f"ffmpeg could not decode {video}: {detail[-1] if detail else exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FrameDecodeError(f"ffmpeg took longer than {timeout:.0f} seconds on {video} and was stopped") from exc
    except OSError as exc:
        raise FrameDecodeError(f"ffmpeg could not run: {exc}") from exc
    frames = sorted(out_dir.glob("*.jpg"))
    if not frames:
        raise FrameDecodeError(f"ffmpeg decoded no frames from {video}")
    return frames


def new_workdir(prefix: str = "prme-observe-") -> tempfile.TemporaryDirectory:
    """A temporary directory for decoded frames and byte sources, removed when the run ends."""
    return tempfile.TemporaryDirectory(prefix=prefix)
