"""Gallery extractor module for retrieving all product photos from the product page.

Performs fast lightweight extraction of multi-angle photos from e-commerce product cards (Mango, Zara, etc.)
so Telegram and Instagram publishers can create rich multi-photo album carousels.
"""

import logging
import re
from typing import Optional
from urllib.parse import urljoin
import httpx

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_gallery_photos(
    product_url: str,
    brand: str = "",
    max_photos: int = 6,
    timeout_seconds: float = 10.0,
) -> list[str]:
    """Retrieve all high-resolution product photos from the product detail page (card)."""
    if not product_url or not product_url.startswith("http"):
        return []

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            resp = client.get(product_url)
            if resp.status_code != 200:
                logger.debug("Failed to fetch product page (%d): %s", resp.status_code, product_url)
                return []
            html_text = resp.text
    except Exception as exc:
        logger.debug("Error fetching gallery from %s: %s", product_url, exc)
        return []

    photos: list[str] = []
    brand_lower = brand.lower()

    # 1. Mango Gallery
    if "mango" in brand_lower or "mango.com" in product_url:
        matches = re.findall(r"https://media\.mango\.com/is/image/punto/[0-9]+-[0-9A-Z]+-[0-9A-Z]+", html_text)
        if matches:
            # Group by color variant (e.g. 37085988-99-xxx vs 37085988-30-xxx)
            variants: dict[str, list[str]] = {}
            for m in matches:
                parts = m.split("/")[-1].split("-")
                if len(parts) >= 3:
                    var_id = parts[1]
                    variants.setdefault(var_id, []).append(m)
            # Pick primary variant with most images
            if variants:
                best_var = max(variants.values(), key=len)
                photos = list(dict.fromkeys(best_var))
            else:
                photos = list(dict.fromkeys(matches))

    # 2. Zara Gallery
    elif "zara" in brand_lower or "zara.com" in product_url:
        zara_matches = re.findall(r"https://static\.zara\.net/photos/[^\s\"\'<>]+", html_text)
        if zara_matches:
            clean_zara = [m.split("?")[0] for m in zara_matches if "/w/" in m or "/2/" in m or "_0." in m or "_1." in m]
            photos = list(dict.fromkeys(clean_zara))

    # 3. Generic JSON-LD / og:image fallback
    if not photos:
        og_images = re.findall(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html_text)
        photos = [urljoin(product_url, u) for u in og_images if u]

    # Clean query strings and limit
    clean_photos: list[str] = []
    seen: set[str] = set()
    for p in photos:
        base = p.split("?")[0]
        if base not in seen:
            seen.add(base)
            clean_photos.append(base)
        if len(clean_photos) >= max_photos:
            break

    if clean_photos:
        logger.info("Extracted %d gallery photos from %s", len(clean_photos), product_url)
    return clean_photos
