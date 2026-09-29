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
