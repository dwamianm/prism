"""Build the visual memory spike corpus.

Everything is generated on this machine; no network is used and nothing is
billed. The corpus has three sources:

- ``meeting.mp4``: a synthetic meeting recording of slides and a whiteboard.
  The whiteboard's launch date and budget change part way through, so the
  questions test recall, the latest value, the earlier value and chart reading.
  Each frame is jittered by a few pixels and a little brightness so no two
  frames are byte-identical, like a real camera.
- ``photos/``: synthetic photos with known text (a receipt, a boarding pass, a
  parking sign and a fridge note) plus one real landscape photo from the macOS
  desktop pictures.
- the real 4K aerial wallpaper video that ships with macOS, referenced by path.

Rendered frames are 1280x720. Fonts come from the system font folders.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

FONT_DIR = Path("/System/Library/Fonts")
SUPPLEMENTAL = FONT_DIR / "Supplemental"
SONOMA_HEIC = Path("/System/Library/Desktop Pictures/Sonoma.heic")
TAHOE_MOV = Path("/System/Library/Desktop Pictures/.wallpapers/Tahoe Day/Tahoe Day.mov")

SIZE = (1280, 720)
# Source clocks for the events. The meeting is a Tuesday morning; the photos
# were "taken" on the days their content suggests; the aerial video is a
# weekend afternoon. They are the event_time of each observation.
MEETING_START = "2026-10-14T10:00:00-07:00"
TAHOE_START = "2026-10-10T14:00:00-07:00"
PHOTO_TIMES = {
    "receipt.jpg": "2026-10-12T08:41:00-07:00",
    "boarding_pass.jpg": "2026-10-15T07:30:00-07:00",
    "parking_sign.jpg": "2026-10-11T12:15:00-07:00",
    "fridge_note.jpg": "2026-10-13T19:20:00-07:00",
    "sonoma.jpg": "2026-10-05T16:00:00-07:00",
}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for folder in (FONT_DIR, SUPPLEMENTAL):
        path = folder / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise FileNotFoundError(name)


def _lines(draw: ImageDraw.ImageDraw, lines: list[tuple[str, int, tuple[int, int, int]]],
           x: int, y: int, font_name: str, gap: int = 18) -> int:
    for text, size, color in lines:
        font = _font(font_name, size)
        draw.text((x, y), text, fill=color, font=font)
        y += size + gap
    return y


BLACK = (20, 20, 20)
BLUE = (30, 60, 160)
RED = (190, 30, 30)
GREEN = (20, 120, 60)
WHITE = (255, 255, 255)


def slide(title: str, bullets: list[str], *, footer: str | None = None) -> Image.Image:
    img = Image.new("RGB", SIZE, WHITE)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, SIZE[0], 110), fill=(24, 48, 96))
    draw.text((60, 28), title, fill=WHITE, font=_font("Helvetica.ttc", 52))
    y = 170
    for bullet in bullets:
        draw.ellipse((70, y + 16, 86, y + 32), fill=BLUE)
        draw.text((110, y), bullet, fill=BLACK, font=_font("Helvetica.ttc", 40))
        y += 72
    if footer:
        draw.text((60, 650), footer, fill=(90, 90, 90), font=_font("Helvetica.ttc", 26))
    return img


def whiteboard(items: list[tuple[str, tuple[int, int, int]]], *, struck: str | None = None) -> Image.Image:
    img = Image.new("RGB", SIZE, (238, 238, 232))
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, SIZE[0] - 20, SIZE[1] - 20), outline=(150, 150, 150), width=10)
    draw.rectangle((0, SIZE[1] - 24, SIZE[0], SIZE[1]), fill=(120, 120, 120))  # the marker tray
    y = 70
    font = _font("MarkerFelt.ttc", 54)
    for text, color in items:
        draw.text((80, y), text, fill=color, font=font)
        if struck and struck in text:
            start = draw.textlength(text[: text.index(struck)], font=font)
            width = draw.textlength(struck, font=font)
            draw.line((80 + start, y + 30, 80 + start + width, y + 30), fill=RED, width=6)
        y += 92
    return img


def photo_slide(photo: Path, caption: str) -> Image.Image:
    img = Image.new("RGB", SIZE, (10, 10, 10))
    picture = Image.open(photo).convert("RGB")
    picture.thumbnail((SIZE[0], SIZE[1] - 80))
    img.paste(picture, ((SIZE[0] - picture.width) // 2, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, SIZE[1] - 80, SIZE[0], SIZE[1]), fill=(0, 0, 0))
    draw.text((40, SIZE[1] - 62), caption, fill=WHITE, font=_font("Helvetica.ttc", 36))
    return img


def bar_chart(title: str, bars: list[tuple[str, int]]) -> Image.Image:
    img = Image.new("RGB", SIZE, WHITE)
    draw = ImageDraw.Draw(img)
    draw.text((60, 30), title, fill=BLACK, font=_font("Helvetica.ttc", 44))
    base_y, top_y = 620, 160
    draw.line((120, base_y, 1200, base_y), fill=BLACK, width=3)
    draw.line((120, base_y, 120, top_y), fill=BLACK, width=3)
    peak = max(value for _, value in bars)
    slot = (1200 - 160) // len(bars)
    font = _font("Helvetica.ttc", 36)
    for index, (label, value) in enumerate(bars):
        x0 = 160 + index * slot + slot // 4
        x1 = x0 + slot // 2
        y0 = base_y - int((base_y - top_y) * value / peak)
        draw.rectangle((x0, y0, x1, base_y), fill=BLUE)
        draw.text(((x0 + x1) // 2 - 20, y0 - 48), str(value), fill=BLACK, font=font)
        draw.text(((x0 + x1) // 2 - 24, base_y + 12), label, fill=BLACK, font=font)
    return img


def meeting_scenes(sonoma: Path) -> list[tuple[int, Image.Image]]:
    """(seconds on screen, image) in order. About 3 minutes 40 seconds in total."""
    return [
        (15, slide("Falcon weekly sync", ["Tuesday 14 October 2026", "Facilitator: Priya Raman",
                                          "Agenda: launch, budget, actions, risks"])),
        (40, whiteboard([("Falcon launch", BLUE), ("Launch date: 3 Nov", BLUE), ("Budget: $12,500", BLUE),
                         ("Vendor: Northwind Sensors", GREEN), ("Owner: Priya", GREEN)])),
        (20, photo_slide(sonoma, "Site photo A, taken last week")),
        (40, whiteboard([("Falcon launch", BLUE), ("Launch date: 3 Nov  10 Nov", RED), ("Budget: $14,000", RED),
                         ("Vendor: Northwind Sensors", GREEN), ("Owner: Priya", GREEN)], struck="3 Nov")),
        (30, slide("Action items", ["Dmitri orders 40 sensors by Friday", "Mei books the Oakland venue",
                                    "Sam writes the test plan"])),
        (30, bar_chart("Sensor failures per quarter", [("Q1", 12), ("Q2", 7), ("Q3", 15)])),
        (25, slide("Risk register", ["Battery supplier delay: High", "Firmware bug 1182: Medium",
                                     "Venue permit: Low"])),
        (20, slide("Wrap up", ["Next sync: Tuesday 21 October, 10:00", "Demo for Acme on 28 October"],
                   footer="Thanks everyone")),
    ]


def jitter(image: Image.Image, rng: random.Random) -> Image.Image:
    """A few pixels of camera shake and a little exposure drift."""
    dx, dy = rng.randint(-4, 4), rng.randint(-4, 4)
    canvas = Image.new("RGB", SIZE, (0, 0, 0))
    canvas.paste(image, (dx, dy))
    return ImageEnhance.Brightness(canvas).enhance(rng.uniform(0.96, 1.04))


def render_meeting(out: Path, sonoma: Path, *, seed: int = 7) -> dict:
    frames = out / "meeting_frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    rng = random.Random(seed)
    index, scenes = 0, []
    for seconds, image in meeting_scenes(sonoma):
        scenes.append({"start_s": index, "end_s": index + seconds})
        for _ in range(seconds):
            jitter(image, rng).save(frames / f"{index:05d}.png")
            index += 1
    video = out / "meeting.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", str(frames / "%05d.png"),
                    "-r", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    shutil.rmtree(frames)
    return {"path": str(video), "seconds": index, "scenes": scenes, "start": MEETING_START}


def receipt() -> Image.Image:
    img = Image.new("RGB", (600, 900), (252, 252, 248))
    draw = ImageDraw.Draw(img)
    _lines(draw, [("BLUE BOTTLE COFFEE", 36, BLACK), ("Oakland, CA", 26, BLACK), ("12 Oct 2026  08:41", 26, BLACK),
                  ("", 20, BLACK), ("2 x Oat Latte        $11.50", 26, BLACK), ("", 20, BLACK),
                  ("TOTAL                $11.50", 30, BLACK), ("VISA ****4421", 26, BLACK), ("", 20, BLACK),
                  ("Thank you!", 26, BLACK)], 50, 60, "Menlo.ttc")
    return img


def boarding_pass() -> Image.Image:
    img = Image.new("RGB", (1000, 450), WHITE)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1000, 90), fill=(0, 40, 120))
    draw.text((40, 22), "UNITED   BOARDING PASS", fill=WHITE, font=_font("Helvetica.ttc", 40))
    _lines(draw, [("Passenger: ALEX CHEN", 34, BLACK), ("Flight UA 884   SFO to JFK", 34, BLACK),
                  ("Date: 18 Oct 2026   Boards 07:05", 34, BLACK), ("Gate B12   Seat 14C", 34, BLACK)],
           40, 120, "Helvetica.ttc")
    return img


def parking_sign(sonoma: Path) -> Image.Image:
    img = Image.open(sonoma).convert("RGB")
    img.thumbnail((1280, 1280))
    draw = ImageDraw.Draw(img)
    x0, y0 = img.width // 2 - 200, img.height // 2 - 230
    draw.rectangle((x0, y0, x0 + 400, y0 + 460), fill=WHITE, outline=BLACK, width=6)
    draw.rectangle((x0, y0, x0 + 400, y0 + 90), fill=GREEN)
    draw.text((x0 + 60, y0 + 18), "PARKING", fill=WHITE, font=_font("Helvetica.ttc", 54))
    _lines(draw, [("2 HOUR LIMIT", 48, BLACK), ("8 AM - 6 PM", 48, BLACK), ("MON - SAT", 48, BLACK),
                  ("Permit holders exempt", 26, RED)], x0 + 30, y0 + 120, "Helvetica.ttc", gap=24)
    return img


def fridge_note() -> Image.Image:
    img = Image.new("RGB", (800, 600), (255, 240, 140))
    draw = ImageDraw.Draw(img)
    _lines(draw, [("Dentist!", 70, BLUE), ("Thu 23 Oct, 3pm", 56, BLUE), ("Dr. Okafor", 56, BLUE),
                  ("bring insurance card", 44, RED)], 60, 60, "Bradley Hand Bold.ttf", gap=28)
    return img


def convert_sonoma(out: Path) -> Path:
    target = out / "sonoma.jpg"
    subprocess.run(["sips", "-s", "format", "jpeg", "--resampleWidth", "1600", str(SONOMA_HEIC), "--out", str(target)],
                   check=True, capture_output=True)
    return target


def build(out: Path) -> dict:
    """Write the corpus under ``out`` and return its manifest."""
    out.mkdir(parents=True, exist_ok=True)
    photos = out / "photos"
    photos.mkdir(exist_ok=True)
    sonoma = convert_sonoma(photos)
    receipt().save(photos / "receipt.jpg", quality=92)
    boarding_pass().save(photos / "boarding_pass.jpg", quality=92)
    parking_sign(sonoma).save(photos / "parking_sign.jpg", quality=92)
    fridge_note().save(photos / "fridge_note.jpg", quality=92)
    manifest = {
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meeting": render_meeting(out, sonoma),
        "photos": [{"path": str(photos / name), "taken": taken} for name, taken in PHOTO_TIMES.items()],
        "tahoe": {"path": str(TAHOE_MOV), "start": TAHOE_START, "exists": TAHOE_MOV.exists()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    import sys

    print(json.dumps(build(Path(sys.argv[1])), indent=2))
