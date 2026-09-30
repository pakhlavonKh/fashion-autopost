"""Read a single product page that an admin pasted into the Telegram bot.

A plain browser-like request is tried first. Stores that answer with a bot
wall are opened in real Chrome, then title, price, and photos are taken from
the page the same way for every brand.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urljoin, urlparse

import httpx

from adapters.base import RawProduct
from adapters.scrapers.base import generate_deterministic_id, parse_price
from core.gallery import ordered_photos

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}

_display_started = False


class ProductPageError(Exception):
    """The page did not contain a usable product title, price, and photo."""


@dataclass
class _ParsedProduct:
    title: str
    price: Decimal
    currency: str
    images: list[str] = field(default_factory=list)
    in_stock: bool = True
    raw_id: str = ""
    match_url: str = ""


def fetch_product_page(url: str, headless: bool = True, timeout_seconds: float = 45.0) -> RawProduct:
    """Load a product URL and return a normalized product."""
    del headless  # Store walls reject headless Chrome; the browser fallback is headed.
    final_url, html_text = _fetch_html_fast(url, timeout_seconds)
    product = parse_product_html(html_text, final_url) if html_text else None
    if product is not None:
        return product

    logger.info("Fast fetch missed product data for %s, opening the page in Chrome", final_url)
    rendered = ""
    rendered_url = final_url
    for attempt in range(2):
        try:
            rendered, rendered_url = _fetch_html_browser(final_url, timeout_seconds)
        except Exception as exc:
            logger.warning("Chrome product fetch failed for %s: %s", final_url, exc)
            rendered, rendered_url = "", final_url
        product = parse_product_html(rendered, rendered_url or final_url) if rendered else None
        if product is not None:
            return product
        # A short or blocked document is usually a display/startup miss. Retry once.
        if rendered and not _is_blocked_page(rendered) and len(rendered) > 20000:
            break
        logger.info(
            "Chrome attempt %s did not yield a product for %s (%s bytes)",
            attempt + 1,
            final_url,
            len(rendered),
        )
    raise ProductPageError(_unreadable_message(url))


def parse_product_html(html_text: str, url: str) -> RawProduct | None:
    """Extract one product from HTML. Returns None when required fields are missing."""
    if not html_text or not url.startswith("http"):
        return None
    if _is_blocked_page(html_text):
        return None

    brand = brand_from_url(url)
    default_currency = currency_hint(url)
    default_currency = _detect_page_currency(html_text, default_currency)

    from_ld = _best_json_ld(html_text, url, default_currency)
    from_embedded = _from_embedded_article(html_text, url, default_currency)
    chosen = _merge_parsed(from_ld, from_embedded)

    if chosen is None:
        chosen = _from_open_graph(html_text, url, default_currency)
    if chosen is None or chosen.price <= 0 or not chosen.images:
        return None

    page_curr = _detect_page_currency(html_text, chosen.currency)
    if page_curr and page_curr != chosen.currency:
        chosen.currency = page_curr

    if len(chosen.images) >= 2:
        images = chosen.images
    else:
        images = _merge_images(chosen.images, _brand_image_urls(html_text, brand, url), base_url=url)
    if not images:
        images = chosen.images
    if not images:
        return None
    images = ordered_photos(html_text, brand, url, images, max_photos=10)
    external_id = generate_deterministic_id(brand, raw_id=chosen.raw_id or None, url=url)
    title = chosen.title.split("|")[0].strip()
    return RawProduct(
        external_id=external_id,
        source=brand,
        title=" ".join(title.split()),
        price=chosen.price,
        currency=chosen.currency,
        photo_url=images[0],
        product_url=url,
        in_stock=chosen.in_stock,
        photo_urls=images[:10],
    )


def brand_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^www\d?\.", "", host)
    if "zara" in host:
        return "zara"
    if "mango" in host:
        return "mango"
    if host == "hm.com" or host.endswith(".hm.com"):
        return "hm"
    if "stradivarius" in host:
        return "stradivarius"
    if "massimodutti" in host:
        return "massimodutti"
    if "bershka" in host:
        return "bershka"
    if "pullandbear" in host:
        return "pullandbear"
    if "oysho" in host:
        return "oysho"
    label = host.split(".")[0] if host else "store"
    return label or "store"


def currency_hint(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if "/uk/" in path or "/gb/" in path or host.endswith(".co.uk"):
        return "GBP"
    if "/us/" in path or "/en-us" in path or "/en_us" in path:
        return "USD"
    if "/tr/" in path or "/tr_" in path or "tr." in host:
        return "TRY"
    if "/pl/" in path or "/pl_" in path or host.endswith(".pl"):
        return "PLN"
    if "/cz/" in path or "/cz_" in path or host.endswith(".cz"):
        return "CZK"
    if "/ro/" in path or "/ro_" in path or host.endswith(".ro"):
        return "RON"
    if "/ch/" in path or host.endswith(".ch"):
        return "CHF"
    if "/ae/" in path or "/ae_" in path or host.endswith(".ae"):
        return "AED"
    if "/kz/" in path or host.endswith(".kz"):
        return "KZT"
    if "/se/" in path or host.endswith(".se"):
        return "SEK"
    if "/no/" in path or host.endswith(".no"):
        return "NOK"
    if "/dk/" in path or host.endswith(".dk"):
        return "DKK"
    if "/ru/" in path or host.endswith(".ru"):
        return "RUB"
    if "/uz/" in path or host.endswith(".uz"):
        return "UZS"
    if "/es" in path or host.endswith(".hm.com") or "/de" in path or "/fr" in path or "/it" in path:
        return "EUR"
    return "EUR"


def _detect_page_currency(html_text: str, default_currency: str) -> str:
    from adapters.scrapers.base import CURRENCY_SYMBOL_MAP

    # 1. Check meta tags
    meta_curr = _meta(html_text, "product:price:currency") or _meta(html_text, "og:price:currency")
    if meta_curr and meta_curr.strip().upper() in CURRENCY_SYMBOL_MAP.values():
        return meta_curr.strip().upper()

    # 2. Check JSON-LD priceCurrency
    m = re.search(r'"priceCurrency"\s*:\s*"([A-Za-z]{3})"', html_text)
    if m:
        curr = m.group(1).upper()
        if curr in CURRENCY_SYMBOL_MAP.values():
            return curr

    # 3. Check distinctive symbols in text (e.g. TL/₺ before $, €)
    if "₺" in html_text or " TL" in html_text or "TL " in html_text:
        return "TRY"
    if "zł" in html_text or " PLN" in html_text:
        return "PLN"
    if "Kč" in html_text or " CZK" in html_text:
        return "CZK"
    if "£" in html_text or " GBP" in html_text:
        return "GBP"
    if "lei" in html_text or " RON" in html_text:
        return "RON"
    if "₸" in html_text or " KZT" in html_text:
        return "KZT"
    if "AED" in html_text or "د.إ" in html_text:
        return "AED"

    return default_currency


def _unreadable_message(url: str) -> str:
    return (
        "Не удалось прочитать товар по этой ссылке: магазин не отдал название, цену или фото. "
        f"Ссылка: {url}"
    )


def _is_blocked_page(html_text: str) -> bool:
    lowered = html_text[:4000].lower()
    if "access denied" in lowered and "ld+json" not in html_text.lower():
        return True
    if "sec-if-cpt-container" in lowered or "behavioral-content" in lowered:
        return True
    return False


def _fetch_html_fast(url: str, timeout_seconds: float) -> tuple[str, str]:
    try:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(
            url,
            impersonate="chrome",
            timeout=timeout_seconds,
            allow_redirects=True,
            headers={"Accept-Language": BROWSER_HEADERS["Accept-Language"]},
        )
        final_url = str(response.url or url)
        if response.status_code == 200 and response.text:
            return final_url, response.text
        logger.info("Browser-like fetch returned HTTP %s for %s", response.status_code, url)
    except Exception as exc:
        logger.info("Browser-like fetch failed for %s: %s", url, exc)

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            response = client.get(url)
            final_url = str(response.url)
            if response.status_code == 200:
                return final_url, response.text
            logger.info("Product page HTTP %s for %s", response.status_code, url)
    except Exception as exc:
        logger.info("Product page HTTP fetch failed for %s: %s", url, exc)
    return url, ""


def _fetch_html_browser(url: str, timeout_seconds: float) -> tuple[str, str]:
    import asyncio

    _ensure_virtual_display()
    return asyncio.run(_browser_html(url, timeout_seconds))


async def _browser_html(url: str, timeout_seconds: float) -> tuple[str, str]:
    import nodriver as uc

    chrome = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None
    kwargs = {
        "headless": False,
        "browser_args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    }
    if chrome:
        kwargs["browser_executable_path"] = chrome
    browser = await uc.start(**kwargs)
    try:
        page = await browser.get(url)
        deadline = time.monotonic() + max(12.0, min(timeout_seconds, 40.0))
        html = ""
        page_url = url
        while time.monotonic() < deadline:
            await page.sleep(2)
            html = await page.get_content() or ""
            page_url = getattr(page, "url", None) or url
            if not str(page_url).startswith("http"):
                page_url = url
            if parse_product_html(html, str(page_url)) is not None:
                break
        return html, str(page_url)
    finally:
        browser.stop()


def _ensure_virtual_display() -> None:
    global _display_started
    if os.environ.get("DISPLAY") or not sys.platform.startswith("linux"):
        return
    if _display_started:
        os.environ["DISPLAY"] = ":99"
        return
    socket_path = "/tmp/.X11-unix/X99"
    if os.path.exists(socket_path):
        os.environ["DISPLAY"] = ":99"
        _display_started = True
        return
    if not shutil.which("Xvfb"):
        return
    subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1366x900x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.environ["DISPLAY"] = ":99"
    _display_started = True
    for _ in range(25):
        if os.path.exists(socket_path):
            time.sleep(0.3)
            return
        time.sleep(0.2)


def _best_json_ld(html_text: str, url: str, default_currency: str) -> _ParsedProduct | None:
    found: list[_ParsedProduct] = []
    for node in _iter_json_ld(html_text):
        variants = node.get("hasVariant")
        nodes = [node]
        if isinstance(variants, list):
            nodes.extend(item for item in variants if isinstance(item, dict))
        for item in nodes:
            parsed = _product_from_json_ld(item, url, default_currency)
            if parsed is not None:
                found.append(parsed)
    return _choose_match(found, url)


def _product_from_json_ld(node: dict, url: str, default_currency: str) -> _ParsedProduct | None:
    types = node.get("@type")
    type_names = types if isinstance(types, list) else [types]
    if "Product" not in [str(item) for item in type_names if item]:
        return None
    title = str(node.get("name") or "").strip()
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}
    amount = offers.get("price")
    if amount is None:
        amount = offers.get("lowPrice")
    currency = str(offers.get("priceCurrency") or default_currency)
    if amount is None or not title:
        return None
    try:
        price, currency = parse_price(f"{amount} {currency}", default_currency=default_currency)
    except ValueError:
        return None
    images = _image_urls(node.get("image"), url)
    availability = str(offers.get("availability") or "")
    in_stock = "OutOfStock" not in availability and "SoldOut" not in availability
    raw_id = str(node.get("sku") or node.get("productID") or node.get("mpn") or "")
    match_url = str(offers.get("url") or node.get("url") or "")
    return _ParsedProduct(title, price, currency, images, in_stock, raw_id, match_url)


def _from_embedded_article(html_text: str, url: str, default_currency: str) -> _ParsedProduct | None:
    data = _extract_json_object_after(html_text, '"productArticleDetails":')
    if not isinstance(data, dict):
        return None
    variations = data.get("variations")
    if not isinstance(variations, dict):
        return None
    article = _page_article(url) or str(data.get("articleCode") or "")
    current = variations.get(article)
    if not isinstance(current, dict):
        for key, value in variations.items():
            if article and article in str(key) and isinstance(value, dict) and value.get("images"):
                current = value
                break
    if not isinstance(current, dict):
        return None
    amount = current.get("whitePriceValue") or current.get("redPriceValue") or current.get("price")
    currency = str(current.get("priceCurrency") or default_currency)
    if amount is None:
        return None
    try:
        price, currency = parse_price(f"{amount} {currency}", default_currency=default_currency)
    except ValueError:
        return None
    color = str(current.get("name") or "").strip()
    product_name = str(data.get("productName") or data.get("baseProductName") or "").strip()
    if product_name and color and color.lower() not in product_name.lower():
        title = f"{product_name} - {color}"
    else:
        title = product_name or color
    looks: list[str] = []
    stills: list[str] = []
    details: list[str] = []
    for item in current.get("images") or []:
        if not isinstance(item, dict):
            continue
        asset = str(item.get("assetType") or "").lower()
        if "swatch" in asset:
            continue
        raw = str(item.get("baseUrl") or item.get("image") or "")
        absolute = _absolute_url(raw, url)
        if not absolute:
            continue
        if "detail" in asset:
            details.append(absolute)
        elif "still" in asset:
            stills.append(absolute)
        else:
            looks.append(absolute)
    images = looks + _product_angles(stills, details)
    if not title or not images:
        return None
    return _ParsedProduct(
        title=title,
        price=price,
        currency=currency,
        images=images,
        raw_id=article,
        match_url=str(current.get("url") or url),
    )


def _from_open_graph(html_text: str, url: str, default_currency: str) -> _ParsedProduct | None:
    title = _meta(html_text, "og:title") or _meta(html_text, "twitter:title")
    title = title.split("|")[0].strip()
    amount = (
        _meta(html_text, "product:price:amount")
        or _meta(html_text, "og:price:amount")
        or _itemprop(html_text, "price")
    )
    meta_currency = (
        _meta(html_text, "product:price:currency")
        or _meta(html_text, "og:price:currency")
        or default_currency
    )
    images = [_absolute_url(item, url) for item in _meta_all(html_text, "og:image")]
    images = [item for item in images if item]
    if not title or not amount or not images:
        return None
    try:
        price, currency = parse_price(f"{amount} {meta_currency}", default_currency=default_currency)
    except ValueError:
        return None
    return _ParsedProduct(title=title, price=price, currency=currency, images=images, match_url=url)


def _merge_parsed(primary: _ParsedProduct | None, extra: _ParsedProduct | None) -> _ParsedProduct | None:
    if primary is None:
        return extra
    if extra is None:
        return primary
    images = extra.images or primary.images
    if primary.images and extra.images:
        images = _merge_images(extra.images, primary.images)
    title = primary.title if len(primary.title) >= len(extra.title) else extra.title
    return _ParsedProduct(
        title=title,
        price=primary.price or extra.price,
        currency=primary.currency or extra.currency,
        images=images,
        in_stock=primary.in_stock,
        raw_id=extra.raw_id or primary.raw_id,
        match_url=primary.match_url or extra.match_url,
    )


def _choose_match(found: list[_ParsedProduct], url: str) -> _ParsedProduct | None:
    if not found:
        return None
    article = _page_article(url)
    if article:
        matched = [
            item for item in found
            if article in item.raw_id or article in item.match_url
        ]
        if matched:
            images: list[str] = []
            for item in matched:
                images = _merge_images(images, item.images)
            best = matched[0]
            best_title = max(matched, key=lambda item: len(item.title)).title
            return _ParsedProduct(
                title=best_title,
                price=best.price,
                currency=best.currency,
                images=images or best.images,
                in_stock=any(item.in_stock for item in matched),
                raw_id=article,
                match_url=best.match_url,
            )
    return found[0]


def _page_article(url: str) -> str:
    match = re.search(r"productpage\.(\d{6,})", url, re.I)
    if match:
        return match.group(1)
    match = re.search(r"/(\d{7,12})(?:[./?#]|$)", url)
    return match.group(1) if match else ""


def _extract_json_object_after(html_text: str, marker: str) -> dict | None:
    start = html_text.find(marker)
    if start < 0:
        return None
    body = html_text[start + len(marker):].lstrip()
    if not body.startswith("{"):
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(body[:index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _iter_json_ld(html_text: str):
    for raw in re.findall(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        payload = raw.strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        yield from _walk_nodes(data)


def _walk_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _walk_nodes(item)
        return
    if not isinstance(data, dict):
        return
    yield data
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _walk_nodes(item)


def _product_angles(stills: list[str], details: list[str]) -> list[str]:
    """Product-only shots in carousel order: front, back, close-up."""
    tail: list[str] = []
    if stills:
        tail.append(stills[0])
    if len(stills) > 1:
        tail.append(stills[1])
    close = details[0] if details else (stills[2] if len(stills) > 2 else "")
    if close and close not in tail:
        tail.append(close)
    return tail


def _image_urls(image_field, page_url: str) -> list[str]:
    values: list[str] = []
    if isinstance(image_field, str):
        values = [image_field]
    elif isinstance(image_field, list):
        for item in image_field:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict) and item.get("url"):
                values.append(str(item["url"]))
    elif isinstance(image_field, dict) and image_field.get("url"):
        values = [str(image_field["url"])]
    cleaned: list[str] = []
    for value in values:
        absolute = _absolute_url(value, page_url)
        if absolute and absolute not in cleaned:
            cleaned.append(absolute)
    return cleaned


def _absolute_url(value: str, page_url: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    absolute = urljoin(page_url, raw)
    if not absolute.startswith("http"):
        return ""
    if "image.hm.com" in absolute or "static.zara.net" in absolute:
        absolute = absolute.split("?", 1)[0]
    return absolute


def _brand_image_urls(html_text: str, brand: str, url: str) -> list[str]:
    found: list[str] = []
    brand_lower = (brand or "").lower()
    url_lower = (url or "").lower()

    if "mango" in brand_lower or "mango.com" in url_lower:
        found.extend(re.findall(r"https://media\.mango\.com/is/image/punto/[0-9]+-[0-9A-Z]+-[0-9A-Z]+", html_text))

    if "zara" in brand_lower or "zara.com" in url_lower or "static.zara.net" in html_text:
        found.extend(re.findall(r"https://static\.zara\.net/(?:photos|assets|stdphotos)/[^\s\"'<>]+", html_text))
        for match in re.finditer(r'"path"\s*:\s*"(/assets/public/[^"]+|/photos/[^"]+)"\s*,\s*"name"\s*:\s*"([^"]+)"', html_text):
            p, n = match.group(1), match.group(2)
            base = f"https://static.zara.net{p.rstrip('/')}/{n}"
            if not base.lower().endswith((".jpg", ".jpeg", ".webp", ".png")):
                base += ".jpg"
            found.append(base)

    if any(k in brand_lower or k in url_lower for k in ("stradivarius", "massimodutti", "bershka", "pullandbear", "oysho")):
        pattern = r"https://static\.(?:stradivarius|massimodutti|bershka|pullandbear|oysho)\.net/(?:photos|assets|public)/[^\s\"'<>]+"
        found.extend(re.findall(pattern, html_text))

    if "hm" in brand_lower or "hm.com" in url_lower:
        found.extend(re.findall(r"https://image\.hm\.com/assets/hm/[^\s\"'<>]+", html_text))

    # General DOM / picture / srcset / img extraction
    for match in re.finditer(r'<picture[^>]*>(.*?)</picture>', html_text, flags=re.DOTALL | re.IGNORECASE):
        pic = match.group(1)
        srcsets = re.findall(r'srcset=[\'"]([^\'"]+)[\'"]', pic, flags=re.IGNORECASE)
        for s in srcsets:
            candidates = [part.strip().split()[0] for part in s.split(",") if part.strip()]
            if candidates:
                cand = candidates[-1].split("?")[0]
                if _is_likely_product_photo(cand):
                    found.append(urljoin(url, cand))

    for match in re.finditer(r'<img[^>]+(?:data-zoom-src|data-large-img-url|data-high-res-src|data-src|src)=[\'"]([^\'"]+)[\'"]', html_text, flags=re.IGNORECASE):
        cand = match.group(1).split("?")[0]
        if _is_likely_product_photo(cand):
            found.append(urljoin(url, cand))

    cleaned_found: list[str] = []
    for item in found:
        cleaned = item.replace("{width}", "2048").split("?")[0].rstrip(".,;\"'")
        if cleaned.startswith("http") and cleaned not in cleaned_found:
            cleaned_found.append(cleaned)

    return cleaned_found


def _is_likely_product_photo(url_str: str) -> bool:
    lower = url_str.lower()
    if not lower.startswith(("http://", "https://", "//", "/")):
        return False
    if not any(lower.endswith(ext) or ext in lower for ext in (".jpg", ".jpeg", ".webp", ".png")):
        return False
    skip = ("logo", "icon", "badge", "avatar", "banner", "spinner", "pixel", "tracking", "swatch", "favicon", "arrow", "social")
    if any(k in lower for k in skip):
        return False
    return True


def _merge_images(primary: list[str], extra: list[str], base_url: str = "") -> list[str]:
    merged: list[str] = []
    base = base_url if (base_url and base_url.startswith("http")) else "https://example.com/"
    for image in list(primary) + list(extra):
        cleaned = _absolute_url(image, base)
        if cleaned.startswith("http") and cleaned not in merged:
            merged.append(cleaned)
    return merged


def _meta(html_text: str, key: str) -> str:
    values = _meta_all(html_text, key)
    return values[0] if values else ""


def _meta_all(html_text: str, key: str) -> list[str]:
    patterns = [
        rf"<meta[^>]+(?:property|name|itemprop)=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)[\"']",
        rf"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name|itemprop)=[\"']{re.escape(key)}[\"']",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
            value = match.group(1).strip()
            if value and value not in found:
                found.append(value)
    return found


def _itemprop(html_text: str, key: str) -> str:
    match = re.search(
        rf"<[^>]+itemprop=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)[\"']",
        html_text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""
