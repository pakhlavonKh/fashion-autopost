"""Image downloader module for downloading product images locally.

Per SRS FR-5 and SDD §3.6.
Downloads remote product images to local storage (data/images/) so publishers (Telegram, etc.)
can upload them directly as multipart binary files instead of relying on external CDNs.
"""

import hashlib
import logging
from pathlib import Path
import re
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

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

        # Build local target file path prefix
        url_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:12]
        clean_id = sanitize_filename(external_id) if external_id else "item"

        # Check if cached with any common extension
        for ext in (".webp", ".jpg", ".png"):
            cached_candidate = self.dest_dir / f"{clean_id}_{url_hash}{ext}"
            if cached_candidate.is_file() and cached_candidate.stat().st_size > 500:
                logger.debug("Using cached downloaded image: %s", cached_candidate)
                return cached_candidate

        # Download remote image with browser headers
        headers = dict(BROWSER_HEADERS)
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                resp = client.get(stripped, headers=headers)
                resp.raise_for_status()

                content = resp.content
                if len(content) < 200:
                    logger.warning("Downloaded image content suspiciously small (%d bytes) for %s", len(content), stripped)
                    return None

                # Determine extension from magic bytes
                extension = ".jpg"
                if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
                    extension = ".webp"
                elif len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
                    extension = ".png"

                target_path = self.dest_dir / f"{clean_id}_{url_hash}{extension}"
                target_path.write_bytes(content)
                logger.info("Successfully downloaded product image to %s (%d bytes)", target_path, len(content))
                return target_path
        except Exception as exc:
            logger.warning("Failed to download image from %s: %s", stripped, exc)
            return None

    def download_all(self, photo_urls: list[str], external_id: str = "") -> list[Path]:
        """Download multiple photos for a product card, returning successfully downloaded local paths."""
        downloaded: list[Path] = []
        for i, url in enumerate(photo_urls):
            sub_id = f"{external_id}_{i}" if external_id else f"item_{i}"
            path = self.download(url, external_id=sub_id)
            if path and path.is_file():
                downloaded.append(path)
        return downloaded
