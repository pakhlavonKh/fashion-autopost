"""Prepare product photos for an Instagram feed post.

Instagram accepts JPEG only, between 4:5 and 1.91:1. Fashion portraits from
Zara and Mango are often taller than 4:5, so each slide is fitted onto a
1080x1350 canvas without cropping the garment.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FEED_WIDTH = 1080
FEED_HEIGHT = 1350
MAX_JPEG_BYTES = 8 * 1024 * 1024
# Thin white gutter, same weight as the boutique grid covers.
COLLAGE_GAP = 16
# Top-left tile is a little taller than the detail under it.
COLLAGE_TOP_RATIO = 0.62


def render_feed_collage(sources: list[Path], dest: Path) -> Path:
    """Cover slide for a carousel: two stacked photos on the left, hero on the right.

    The first gallery photo is the on-model shot and fills the tall right tile.
    The next two fill the left column. A 4:5 frame keeps the later slides uncropped.
    """
    from PIL import Image

    photos = [path for path in sources if path.is_file()][:3]
    if len(photos) < 2:
        raise RuntimeError("Feed collage needs at least two photos")

    canvas = Image.new("RGB", (FEED_WIDTH, FEED_HEIGHT), (255, 255, 255))
    ordered = photos[1:] + photos[:1]
    for (x, y, width, height), path in zip(_collage_frames(len(photos)), ordered):
        canvas.paste(_cover_tile(path, width, height), (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    _save_under_limit(canvas, dest)
    return dest


def prepare_feed_jpeg(source: Path, dest: Path) -> Path:
    """Write a 4:5 sRGB JPEG that Instagram Graph API will accept."""
    from PIL import Image, ImageOps

    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        background = _edge_color(image)
        image.thumbnail((FEED_WIDTH, FEED_HEIGHT), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (FEED_WIDTH, FEED_HEIGHT), background)
        offset = ((FEED_WIDTH - image.width) // 2, (FEED_HEIGHT - image.height) // 2)
        canvas.paste(image, offset)
        _save_under_limit(canvas, dest)
    return dest


def _collage_frames(count: int) -> list[tuple[int, int, int, int]]:
    left_w = (FEED_WIDTH - COLLAGE_GAP) // 2
    right_x = left_w + COLLAGE_GAP
    right_w = FEED_WIDTH - right_x
    if count == 2:
        return [
            (0, 0, left_w, FEED_HEIGHT),
            (right_x, 0, right_w, FEED_HEIGHT),
        ]
    top_h = int(FEED_HEIGHT * COLLAGE_TOP_RATIO)
    bottom_y = top_h + COLLAGE_GAP
    return [
        (0, 0, left_w, top_h),
        (0, bottom_y, left_w, FEED_HEIGHT - bottom_y),
        (right_x, 0, right_w, FEED_HEIGHT),
    ]


def _cover_tile(path: Path, width: int, height: int):
    from PIL import Image, ImageOps

    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        scale = max(width / image.width, height / image.height)
        resized = image.resize(
            (max(width, int(image.width * scale)), max(height, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        left = max(0, (resized.width - width) // 2)
        top = max(0, (resized.height - height) // 2)
        return resized.crop((left, top, left + width, top + height))


def _edge_color(image) -> tuple[int, int, int]:
    """Average the corner pixels so the padding matches the studio background."""
    width, height = image.size
    corners = (
        image.getpixel((0, 0)),
        image.getpixel((width - 1, 0)),
        image.getpixel((0, height - 1)),
        image.getpixel((max(width - 1, 0), max(height - 1, 0))),
    )
    channels = []
    for index in range(3):
        channels.append(sum(pixel[index] for pixel in corners) // len(corners))
    return channels[0], channels[1], channels[2]


def _save_under_limit(image, dest: Path) -> None:
    quality = 88
    while quality >= 60:
        image.save(dest, "JPEG", quality=quality, optimize=True)
        if dest.stat().st_size <= MAX_JPEG_BYTES:
            return
        quality -= 8
    logger.warning("Instagram JPEG is still over 8MB after compression: %s", dest)
