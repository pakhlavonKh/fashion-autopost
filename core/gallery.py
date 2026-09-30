"""Gallery extractor module for retrieving all product photos from the product page.

Performs fast lightweight extraction of multi-angle photos from e-commerce product cards (Mango, Zara, etc.)
so Telegram and Instagram publishers can create rich multi-photo album carousels.

Model photos stay in the site's order. Product-only shots are kept for the end
of the carousel: front, then back, then a close-up. The same photo is kept once.
"""

import logging
import re
from urllib.parse import urljoin, urlsplit
import httpx

logger = logging.getLogger(__name__)

_WIDTH_SEGMENT = re.compile(r"/w/\d+(?=/|$)")
_MANGO_NAME = re.compile(r"^(\d+)-([0-9a-z]+)-(\d+)$", re.IGNORECASE)
_ZARA_SHOT = re.compile(r"_(\d+)_(\d+)_\d+$")
_ZARA_KIND = re.compile(r'"kind"\s*:\s*"(full|plain|other|colorcut)"', re.IGNORECASE)
_TAIL_ROLES = ("front", "back", "close")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def canonical_photo_key(url: str) -> str:
    """Identity of a product photo, ignoring CDN size and query duplicates."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        return raw.replace("\\", "/").lower()
    path = _WIDTH_SEGMENT.sub("", urlsplit(raw).path)
    name = path.rstrip("/").split("/")[-1].lower()
    name = re.sub(r"\.(jpe?g|png|webp)$", "", name)
    mango = _MANGO_NAME.match(name)
    if mango:
        return f"{mango.group(1)}-{mango.group(2).lower()}-{int(mango.group(3))}"
    return name


def arrange_carousel(
    urls: list[str],
    *,
    roles: dict[str, str] | None = None,
    max_photos: int = 10,
) -> list[str]:
    """One copy of each photo: looks first, then front, back and close-up."""
    assigned_roles = roles or {}
    chosen: dict[str, str] = {}
    role_of: dict[str, str] = {}
    sequence: list[str] = []
    for url in urls:
        if not url or not str(url).strip():
            continue
        role = assigned_roles.get(canonical_photo_key(url)) or _shot_role(url) or "look"
        if role == "skip":
            continue
        key = canonical_photo_key(url)
        if not key:
            continue
        if key in chosen:
            if _width_hint(url) > _width_hint(chosen[key]):
                chosen[key] = url
            continue
        chosen[key] = url
        role_of[key] = role
        sequence.append(key)

    looks: list[str] = []
    tail_slots: dict[str, list[str]] = {name: [] for name in _TAIL_ROLES}
    plain: list[str] = []
    for key in sequence:
        url = chosen[key]
        role = role_of[key]
        if role in tail_slots:
            tail_slots[role].append(url)
        elif role in {"plain", "still"}:
            plain.append(url)
        else:
            looks.append(url)

    for url in plain:
        for name in _TAIL_ROLES:
            if not tail_slots[name]:
                tail_slots[name].append(url)
                break

    tail = [tail_slots[name][0] for name in _TAIL_ROLES if tail_slots[name]]
    room = max(0, max_photos - len(tail))
    return looks[:room] + tail


def ordered_photos(
    html_text: str,
    brand: str,
    page_url: str,
    fallback: list[str] | None = None,
    max_photos: int = 10,
) -> list[str]:
    """Build the carousel list from a product page, keeping product angles last."""
    brand_lower = (brand or "").lower()
    page = page_url or ""
    roles: dict[str, str] = {}
    urls = list(fallback or [])

    if "mango" in brand_lower or "mango.com" in page:
        found = re.findall(r"https://media\.mango\.com/is/image/punto/[0-9]+-[0-9A-Z]+-[0-9A-Z]+", html_text)
        variant = _mango_variant(found)
        if variant:
            urls = variant
    elif "zara" in brand_lower or "zara.com" in page or "static.zara.net" in html_text:
        labeled = _zara_entries(html_text)
        if labeled:
            urls = []
            plain_index = 0
            for url, kind in labeled:
                if kind == "colorcut":
                    continue
                urls.append(url)
                if kind == "plain" and plain_index < len(_TAIL_ROLES):
                    roles[canonical_photo_key(url)] = _TAIL_ROLES[plain_index]
                    plain_index += 1
        elif not urls:
            urls = _zara_urls(html_text)

    if not urls:
        og_images = re.findall(
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
            html_text,
        )
        urls = [urljoin(page_url, item) for item in og_images if item]

    return arrange_carousel(urls, roles=roles, max_photos=max_photos)


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

    photos = ordered_photos(html_text, brand, product_url, max_photos=max_photos)
    if photos:
        logger.info("Extracted %d gallery photos from %s", len(photos), product_url)
    return photos


def _mango_variant(urls: list[str]) -> list[str]:
    """Keep the colour that has the most frames and drop the other colourways."""
    variants: dict[str, list[str]] = {}
    for url in urls:
        parts = url.split("/")[-1].split("-")
        if len(parts) >= 3:
            variants.setdefault(parts[1], []).append(url)
    if not variants:
        return list(dict.fromkeys(urls))
    return max(variants.values(), key=len)


def _shot_role(url: str) -> str | None:
    """front, back, close, or skip. None means a model photo that stays in place."""
    lower = url.lower().split("?")[0]
    if "/swatches/" in lower or "_swatch" in lower or "swatch" in lower.rsplit("/", 1)[-1]:
        return "skip"
    name = lower.rstrip("/").split("/")[-1]
    name = re.sub(r"\.(jpe?g|png|webp)$", "", name)
    if _ZARA_SHOT.search(name) and "_6_1_1" in f"_{name}":
        return "skip"

    suffix = name.split("-")[-1]
    if suffix.startswith("020") or suffix in {"021", "022"}:
        return "skip"
    pack = _pack_index(suffix)
    if pack is not None and _MANGO_NAME.match(name):
        if pack <= 0:
            return "front"
        if pack == 1:
            return "back"
        return "close"

    zara = _ZARA_SHOT.search(name)
    if zara:
        group, index = int(zara.group(1)), int(zara.group(2))
        if group == 6:
            return "skip"
        if group == 3:
            if index <= 1:
                return "front"
            if index == 2:
                return "back"
            return "close"
    return None


def _pack_index(suffix: str) -> int | None:
    """Mango still-life codes: 90/900 front, 91/901 back, 92/902 and later close-up."""
    if not suffix.isdigit():
        return None
    if not suffix.startswith(("90", "91", "92", "93")):
        return None
    number = int(suffix)
    if 900 <= number <= 909:
        return number - 900
    if 910 <= number <= 919:
        return 1
    if number >= 920:
        return 2
    if 90 <= number <= 99:
        return number - 90
    return None


def _width_hint(url: str) -> int:
    match = re.search(r"/w/(\d+)", url)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:^|[?&])imwidth=(\d+)", url, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def _zara_urls(html_text: str) -> list[str]:
    found = re.findall(r"https://static\.zara\.net/(?:photos|assets)/[^\s\"'<>]+", html_text)
    urls: list[str] = []
    for url in found:
        if "{width}" in url or "/swatches/" in url or "_swatch" in url:
            continue
        urls.append(url.split("?")[0])
    return urls


def _zara_entries(html_text: str) -> list[tuple[str, str]]:
    """(url, kind) for Zara xmedia, in page order."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _ZARA_KIND.finditer(html_text):
        kind = match.group(1).lower()
        window = html_text[match.end(): match.end() + 900]
        url = _zara_url_from_window(window) or _zara_url_from_window(html_text[max(0, match.start() - 400): match.start()])
        if not url:
            continue
        key = canonical_photo_key(url)
        if key in seen:
            continue
        seen.add(key)
        entries.append((url, kind))
    return entries


def _zara_url_from_window(window: str) -> str:
    path_match = re.search(r'"path"\s*:\s*"([^"]+)"', window)
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', window)
    if not path_match or not name_match:
        direct = re.search(r"https://static\.zara\.net/[^\"\\\s]+", window)
        if not direct:
            return ""
        url = direct.group(0).replace("{width}", "2048").replace("\\u0026", "&")
        return url.split("?")[0]
    path = path_match.group(1)
    name = name_match.group(1)
    if not path.startswith("/"):
        path = "/" + path
    base = "https://static.zara.net" + path.rstrip("/")
    if not base.endswith("/" + name):
        base = f"{base}/{name}"
    if not base.lower().endswith((".jpg", ".jpeg", ".webp", ".png")):
        base += ".jpg"
    return base
