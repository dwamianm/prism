"""Sources, capture clocks and video decoding (RFC-0018, phase 1; ``prme.vision.frames``)."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from prme.vision import frames
from prme.vision.errors import FrameDecodeError, UnsupportedSourceError

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "
GIF = b"GIF89a" + b"\x00" * 10
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
AVIF = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00"
MKV = b"\x1aE\xdf\xa3" + b"\x00" * 12
AVI = b"RIFF\x00\x00\x00\x00AVI LIST"


class TestKinds:
    @pytest.mark.parametrize("head, kind", [
        (JPEG, "photo"), (PNG, "photo"), (WEBP, "photo"), (GIF, "photo"), (HEIC, "photo"), (AVIF, "photo"),
        (MP4, "video_frame"), (MOV, "video_frame"), (MKV, "video_frame"), (AVI, "video_frame"),
    ])
    def test_sniff_kind(self, head, kind):
        assert frames.sniff_kind(head) == kind

    def test_unknown_bytes_sniff_to_nothing(self):
        assert frames.sniff_kind(b"plain text file") is None

    def test_suffix_wins_then_bytes(self):
        assert frames.kind_of(Path("clip.MOV"), b"") == "video_frame"
        assert frames.kind_of(Path("IMG.HEIC"), b"") == "photo"
        assert frames.kind_of(Path("unknown.bin"), MP4) == "video_frame"
        assert frames.kind_of(None, PNG) == "photo"
        with pytest.raises(UnsupportedSourceError, match="not an image or video"):
            frames.kind_of(Path("notes.txt"), b"hello")


def write_photo(path: Path, exif: dict | None = None) -> Path:
    image = Image.new("RGB", (64, 48), (200, 30, 30))
    if exif:
        data = image.getexif()
        for tag, value in exif.items():
            data[tag] = value
        image.save(path, quality=90, exif=data.tobytes())
    else:
        image.save(path, quality=90)
    return path


class TestResolveSource:
    def test_path_source(self, tmp_path):
        photo = write_photo(tmp_path / "a.jpg")
        resolved = frames.resolve_source(photo, name=None, workdir=tmp_path / "work")
        assert resolved.kind == "photo" and resolved.name == "a.jpg"
        assert resolved.given_path == str(photo.resolve())
        assert resolved.sha256 == frames.sha256_file(photo)

    def test_bytes_source_is_copied_and_named(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        data = write_photo(tmp_path / "a.jpg").read_bytes()
        resolved = frames.resolve_source(data, name=None, workdir=work)
        assert resolved.kind == "photo" and resolved.given_path is None
        assert resolved.name.startswith("bytes:") and resolved.path.parent == work
        named = frames.resolve_source(data, name="scan.png", workdir=work)
        assert named.name == "scan.png" and named.path.suffix == ".png"

    def test_unreadable_photo_is_refused_before_description(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        with pytest.raises(UnsupportedSourceError, match="Pillow can read"):
            frames.resolve_source(JPEG, name=None, workdir=work)
        with pytest.raises(UnsupportedSourceError, match="empty"):
            frames.resolve_source(b"", name=None, workdir=work)
        with pytest.raises(FileNotFoundError):
            frames.resolve_source(tmp_path / "missing.jpg", name=None, workdir=work)

    def test_heif_needs_pillow_heif(self, tmp_path, monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.setattr(frames, "_register_heif", lambda: False)
        with pytest.raises(UnsupportedSourceError, match="pillow-heif"):
            frames.resolve_source(HEIC + b"\x00" * 32, name="IMG_0001.HEIC", workdir=work)


class TestCaptureClock:
    def photo(self, tmp_path, exif=None) -> frames.Source:
        path = write_photo(tmp_path / "a.jpg", exif)
        return frames.resolve_source(path, name=None, workdir=tmp_path)

    def test_given_clock_wins_and_must_be_aware(self, tmp_path):
        source = self.photo(tmp_path, {36867: "2026:10:12 08:41:00"})
        given = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert frames.capture_clock(source, given) == (given, "given")
        with pytest.raises(ValueError, match="timezone-aware"):
            frames.capture_clock(source, datetime(2026, 1, 1))

    def test_exif_with_offset(self, tmp_path):
        source = self.photo(tmp_path, {36867: "2026:10:12 08:41:00", 36881: "-07:00"})
        clock, how = frames.capture_clock(source, None)
        assert how == "exif" and clock == datetime(2026, 10, 12, 15, 41, tzinfo=timezone.utc)

    def test_naive_exif_uses_the_local_rules_for_that_date(self, tmp_path):
        source = self.photo(tmp_path, {36867: "2026:07:04 12:00:00"})
        clock, how = frames.capture_clock(source, None)
        assert how == "exif"
        assert clock == datetime(2026, 7, 4, 12, 0).astimezone()
        assert clock.utcoffset() == datetime(2026, 7, 4, 12, 0).astimezone().utcoffset()

    def test_file_time_without_exif(self, tmp_path):
        source = self.photo(tmp_path)
        clock, how = frames.capture_clock(source, None)
        assert how == "file"
        modified = datetime.fromtimestamp(source.path.stat().st_mtime, tz=timezone.utc)
        assert abs(clock - modified) < timedelta(seconds=1)

    def test_bytes_without_exif_get_now(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        source = frames.resolve_source(write_photo(tmp_path / "a.jpg").read_bytes(), name=None, workdir=work)
        clock, how = frames.capture_clock(source, None)
        assert how == "now" and abs(clock - datetime.now(timezone.utc)) < timedelta(minutes=1)

    def test_video_without_ffprobe_falls_back_to_the_file_time(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(MP4 + b"\x00" * 64)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert frames.probe_video(video) == frames.VideoInfo(duration_s=None, created_at=None)
        source = frames.resolve_source(video, name=None, workdir=tmp_path)
        clock, how = frames.capture_clock(source, None)
        assert how == "file" and clock.tzinfo is not None


class TestDecode:
    def test_missing_ffmpeg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(FrameDecodeError, match="ffmpeg is not installed"):
            frames.decode_video_frames(tmp_path / "clip.mp4", tmp_path / "out", fps=1.0, width=640)

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
    def test_ffmpeg_failure_is_reported(self, tmp_path):
        bogus = tmp_path / "clip.mp4"
        bogus.write_bytes(MP4 + b"\x00" * 64)
        with pytest.raises(FrameDecodeError, match="could not decode"):
            frames.decode_video_frames(bogus, tmp_path / "out", fps=1.0, width=640)

    def test_timeout_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=kwargs.get("timeout", 0))

        monkeypatch.setattr(subprocess, "run", slow)
        with pytest.raises(FrameDecodeError, match="longer than 1 seconds"):
            frames.decode_video_frames(tmp_path / "clip.mp4", tmp_path / "out", fps=1.0, width=640, timeout=1)
