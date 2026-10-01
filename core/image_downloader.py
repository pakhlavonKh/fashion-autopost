"""Image downloader module for downloading product images locally.

Per SRS FR-5 and SDD §3.6.
Downloads remote product images to local storage (data/images/) so publishers (Telegram, etc.)
can upload them directly as multipart binary files instead of relying on external CDNs.
"""

import hashlib
import logging
from pathlib import Path
import re
from typing import NamedTuple, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode
import httpx

from core.gallery import is_product_angle

logger = logging.getLogger(__name__)

# Storefront CDNs default to a thumbnail. These widths are the sharp rendition
# each CDN still serves, and they stay under the publisher upload limits.
INDITEX_WIDTH = 2048
MANGO_WIDTH = 2048
HM_WIDTH = 2160
_INDITEX_HOST = re.compile(
    r"^static\.(zara|bershka|pullandbear|stradivarius|massimodutti|oysho|lefties)\.",
    re.IGNORECASE,
)
_INDITEX_PATH_WIDTH = re.compile(r"/w/(\d+)(?=/)")

DEFAULT_IMAGE_DIR = Path("data/images")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "image/webp,image/png,image/jpeg,image/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def sanitize_filename(name: str) -> str:
    """Strip unsafe filesystem characters from filename."""
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)


def high_resolution_image_url(url: str) -> str:
    """Point a known store CDN URL at its large rendition.

    Unknown hosts are returned unchanged so signed or one-off URLs keep working.
    """
    if not url.startswith(("http://", "https://")):
        return url

    parts = urlsplit(url)
    host = parts.netloc.lower().split(":")[0]
    if _INDITEX_HOST.match(host):
        return _upgrade_inditex(url)
    if host == "media.mango.com":
        return _raise_query_int(_raise_query_int(url, "imwidth", MANGO_WIDTH), "qlt", 100)
    if host == "image.hm.com":
        return _raise_query_int(url, "imwidth", HM_WIDTH)
    if re.search(r"(?:^|&)imwidth=\d+", parts.query, flags=re.IGNORECASE):
        return _raise_query_int(url, "imwidth", MANGO_WIDTH)
    return url


def _upgrade_inditex(url: str) -> str:
    parts = urlsplit(url)
    path = _INDITEX_PATH_WIDTH.sub(_raise_inditex_width, parts.path)
    upgraded = urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    if re.search(r"(?:^|&)w=\d+", parts.query):
        return _raise_query_int(upgraded, "w", INDITEX_WIDTH)
    return upgraded


def _raise_inditex_width(match: re.Match[str]) -> str:
    current = int(match.group(1))
    if current >= INDITEX_WIDTH:
        return match.group(0)
    return f"/w/{INDITEX_WIDTH}/"


def _raise_query_int(url: str, name: str, minimum: int, extra: dict[str, str] | None = None) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    found = False
    updated: list[tuple[str, str]] = []
    for key, value in pairs:
        if key.lower() == name.lower():
            found = True
            try:
                current = int(value)
            except ValueError:
                current = 0
            updated.append((key, str(max(current, minimum))))
        else:
            updated.append((key, value))
    if not found:
        updated.append((name, str(minimum)))
    if extra:
        present = {key.lower() for key, _value in updated}
        for key, value in extra.items():
            if key.lower() not in present:
                updated.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(updated), parts.fragment))


OUTLINE_SIZE = 32
# Two shots of one garment disagree on tens of grey levels per pixel; the same shot
# served twice disagrees by about two, which is exposure drift and JPEG noise.
MAX_OUTLINE_DIFFERENCE = 10.0
# The outline ignores brightness, so this keeps one pose in two colourways apart.
MAX_COLOR_GAP = 120


class _Fingerprint(NamedTuple):
    color: tuple[int, int, int]
    outline: bytes


def unique_images(paths: list[Path], max_difference: float = MAX_OUTLINE_DIFFERENCE) -> list[Path]:
    """Drop a later photo when it is the same picture as one already kept."""
    kept: list[Path] = []
    seen: list[_Fingerprint] = []
    for path in paths:
        digest = _image_fingerprint(path)
        if digest is None:
            kept.append(path)
            continue
        if any(_same_picture(digest, previous, max_difference) for previous in seen):
            logger.info("Dropping duplicate photo %s", path.name)
            continue
        seen.append(digest)
        kept.append(path)
    return kept


def _image_fingerprint(path: Path) -> _Fingerprint | None:
    """Average color plus a brightness-normalised outline, so a re-encode matches and a new angle does not."""
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as image:
            small = image.convert("RGB").resize((OUTLINE_SIZE, OUTLINE_SIZE), Image.Resampling.BOX)
            outline = ImageOps.autocontrast(small.convert("L")).tobytes()
            raw = small.tobytes()
    except Exception:
        return None
    if not raw or not outline:
        return None
    count = len(raw) // 3
    mean = [sum(raw[channel::3]) // count for channel in range(3)]
    return _Fingerprint((mean[0], mean[1], mean[2]), outline)


def _same_picture(left: _Fingerprint, right: _Fingerprint, max_difference: float) -> bool:
    color_gap = sum(abs(one - other) for one, other in zip(left.color, right.color))
    if color_gap > MAX_COLOR_GAP:
        return False
    drift = sum(abs(one - other) for one, other in zip(left.outline, right.outline))
    return drift / len(left.outline) <= max_difference


def is_material_or_color_swatch(image_path: Path) -> bool:
    """Detect if an image is a single-color or fabric texture swatch rather than a full product photo.

    Checks:
    1. Small dimension icons (< 350x350, such as 200x200 swatches).
    2. Uniform solid colors or low-contrast fabric patches (color stddev < 18.0).
    """
    try:
        from PIL import Image, ImageStat
        with Image.open(image_path) as im:
            w, h = im.size
            if w < 350 or h < 350:
                logger.info("Filtered swatch image %s: small dimensions (%dx%d)", image_path.name, w, h)
                return True
            stat = ImageStat.Stat(im.convert("RGB"))
            avg_std = sum(stat.stddev) / len(stat.stddev)
            if avg_std < 18.0:
                logger.info("Filtered swatch image %s: low color variance (stddev=%.2f, single-color texture)", image_path.name, avg_std)
                return True
    except Exception as exc:
        logger.debug("Could not run swatch detection on %s: %s", image_path, exc)
    return False


class ImageDownloader:
    """Downloads remote product photos to local disk storage."""

    def __init__(self, dest_dir: Path | str = DEFAULT_IMAGE_DIR, timeout_seconds: float = 20.0) -> None:
        self.dest_dir = Path(dest_dir)
        self.timeout_seconds = timeout_seconds
        self.dest_dir.mkdir(parents=True, exist_ok=True)

    def download(self, photo_url_or_path: str, external_id: str = "") -> Optional[Path]:
        """Download remote image or verify local image, returning the Path on success."""
        if not photo_url_or_path:
            return None

        stripped = photo_url_or_path.strip()

        # If it's already a local file that exists, return it
        local_candidate = Path(stripped)
        if local_candidate.is_file():
            return local_candidate

        # If it's not an HTTP(S) URL, we cannot download it
        if not (stripped.startswith("http://") or stripped.startswith("https://")):
            return None

        clean_id = sanitize_filename(external_id) if external_id else "item"
        upgraded = high_resolution_image_url(stripped)

        # A previously saved thumbnail must not win over a sharper rendition.
        if upgraded != stripped:
            cached = self._cached_image(clean_id, upgraded)
            if cached is not None:
                return cached

        headers = dict(BROWSER_HEADERS)
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                if upgraded != stripped:
                    fetched = self._fetch_image(client, upgraded, clean_id, headers)
                    if fetched is not None:
                        logger.info("Downloaded high-resolution image instead of %s", stripped)
                        return fetched
                    logger.info("High-resolution URL failed, using the original image: %s", stripped)

                cached = self._cached_image(clean_id, stripped)
                if cached is not None:
                    return cached
                return self._fetch_image(client, stripped, clean_id, headers)
        except Exception as exc:
            logger.warning("Failed to download image from %s: %s", stripped, exc)
            return None

    def _cached_image(self, clean_id: str, url: str) -> Optional[Path]:
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        for ext in (".webp", ".jpg", ".png"):
            cached_candidate = self.dest_dir / f"{clean_id}_{url_hash}{ext}"
            if cached_candidate.is_file() and cached_candidate.stat().st_size > 500:
                logger.debug("Using cached downloaded image: %s", cached_candidate)
                return cached_candidate
        return None

    def _fetch_image(self, client: httpx.Client, url: str, clean_id: str, headers: dict[str, str]) -> Optional[Path]:
        try:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to download image from %s: %s", url, exc)
            return None

        content = resp.content
        if len(content) < 200:
            logger.warning("Downloaded image content suspiciously small (%d bytes) for %s", len(content), url)
            return None

        extension = ".jpg"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            extension = ".webp"
        elif len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
            extension = ".png"

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        target_path = self.dest_dir / f"{clean_id}_{url_hash}{extension}"
        target_path.write_bytes(content)
        logger.info("Successfully downloaded product image to %s (%d bytes)", target_path, len(content))
        return target_path

    def download_all(self, photo_urls: list[str], external_id: str = "") -> list[Path]:
        """Download multiple photos for a product card, returning successfully downloaded local paths with swatches filtered out."""
        downloaded: list[Path] = []
        for i, url in enumerate(photo_urls):
            sub_id = f"{external_id}_{i}" if external_id else f"item_{i}"
            path = self.download(url, external_id=sub_id)
            if path and path.is_file():
                # A light garment on a pale backdrop reads as a flat swatch.
                if not is_product_angle(url) and is_material_or_color_swatch(path):
                    continue
                downloaded.append(path)
        return unique_images(downloaded)
