"""Prepare product photos for an Instagram feed post.

Instagram accepts JPEG only, between 4:5 and 1.91:1. Every slide is the
original store photo, cover-cropped to the same 1080x1350 frame so the
carousel has one format and no gray bars or extra background.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FEED_WIDTH = 1080
FEED_HEIGHT = 1350
MAX_JPEG_BYTES = 8 * 1024 * 1024
JPEG_QUALITY = 100
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
    save_publish_jpeg(canvas, dest)
    return dest


def prepare_feed_jpeg(source: Path, dest: Path) -> Path:
    """Write a 4:5 sRGB JPEG filled by the original photo, with no padding."""
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        filled = _cover_rgb(_open_rgb(image), FEED_WIDTH, FEED_HEIGHT)
        save_publish_jpeg(filled, dest)
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
    from PIL import Image

    with Image.open(path) as image:
        image = _open_rgb(image)
        return _cover_rgb(image, width, height)


def _cover_rgb(image, width: int, height: int):
    from PIL import Image

    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(width, int(image.width * scale)), max(height, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    if scale < 0.99:
        resized = _sharpen(resized)
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _open_rgb(image):
    """Apply camera rotation and convert embedded color profiles to sRGB."""
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image)
    profile = image.info.get("icc_profile")
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    if not profile:
        return image.convert("RGB")
    try:
        from PIL import ImageCms

        source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        target = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(image.convert("RGB"), source, target, outputMode="RGB")
    except Exception:
        logger.debug("Keeping image colors without an ICC conversion", exc_info=True)
        return image.convert("RGB")


def _sharpen(image):
    """Restore the edge contrast a downscale removes, without crunching flat areas."""
    from PIL import ImageFilter

    return image.filter(ImageFilter.UnsharpMask(radius=0.8, percent=40, threshold=2))


def save_publish_jpeg(image, dest: Path) -> None:
    """Write a 4:4:4 JPEG and only drop quality if the file exceeds 8MB."""
    quality = JPEG_QUALITY
    while quality >= 60:
        image.save(
            dest,
            "JPEG",
            quality=quality,
            subsampling=0,
            optimize=True,
        )
        if dest.stat().st_size <= MAX_JPEG_BYTES:
            return
        quality -= 5
    logger.warning("Instagram JPEG is still over 8MB after compression: %s", dest)
