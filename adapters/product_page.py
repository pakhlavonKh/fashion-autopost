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
from core.color_variants import extract_color_variants
from core.gallery import MAX_PRODUCT_PHOTOS, _inditex_urls, ordered_photos, page_gallery_is_authoritative
from core.store_platforms import inditex_brand, is_inditex
from core.product_facts import extract_heel_height, extract_site_facts, is_heeled_footwear

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
# One product page is read twice in a row: once for sizes and colour, once for
# the gallery. The last page stays available for that second read.
PAGE_CACHE_SECONDS = 180.0
_page_cache: tuple[str, str, str, float] | None = None

# Chrome loses the race to nodriver's short connect window often enough that
# one try is not enough, and a page read that gives up costs the post its
# sizes and its gallery.
BROWSER_ATTEMPTS = 3
BROWSER_RETRY_PAUSE_SECONDS = 2.0


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
    images = ordered_photos(html_text, brand, url, images, max_photos=MAX_PRODUCT_PHOTOS)
    external_id = generate_deterministic_id(brand, raw_id=chosen.raw_id or None, url=url)
    title = " ".join(chosen.title.split("|")[0].split())
    color, sizes = extract_site_facts(html_text, url)
    variants = tuple(extract_color_variants(html_text, url, current_color=color))
    selected = next((item for item in variants if item.selected), None)
    if selected is not None and (color or "").strip().casefold() != selected.name.strip().casefold():
        # The page's first colour is not the one in the link: take that colour's own name and sizes.
        _first, own_sizes = extract_site_facts(html_text, url, prefer=(selected.code, selected.name))
        if not selected.name.startswith("Цвет "):
            color = selected.name
        sizes = own_sizes or sizes
    heel = extract_heel_height(html_text, title) if is_heeled_footwear(title, url) else None
    return RawProduct(
        external_id=external_id,
        source=brand,
        title=title,
        price=chosen.price,
        currency=chosen.currency,
        photo_url=images[0],
        product_url=url,
        in_stock=chosen.in_stock,
        photo_urls=images[:MAX_PRODUCT_PHOTOS],
        heel_height=heel,
        color=color,
        sizes=sizes,
        photos_verified=page_gallery_is_authoritative(html_text, brand, url),
        color_variants=variants,
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

    # 3. Fall back to the sign printed next to an amount. A bare word is not
    # enough: a German page is full of «Bekleidung», and the «lei» inside it
    # is not the Romanian leu.
    for currency, sign in _PRICED_IN:
        if sign.search(html_text):
            return currency

    return default_currency


def _priced_in(sign: str) -> re.Pattern[str]:
    """Match a currency sign only where it sits against a price."""
    return re.compile(rf"(?:\d[\d\s.,]*\s*{sign})|(?:{sign}\s*\d)", re.IGNORECASE)


_PRICED_IN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TRY", _priced_in(r"(?:₺|\bTL\b)")),
    ("PLN", _priced_in(r"(?:zł|\bPLN\b)")),
    ("CZK", _priced_in(r"(?:Kč|\bCZK\b)")),
    ("GBP", _priced_in(r"(?:£|\bGBP\b)")),
    ("RON", _priced_in(r"(?:\blei\b|\bRON\b)")),
    ("KZT", _priced_in(r"(?:₸|\bKZT\b)")),
    ("AED", _priced_in(r"(?:\bAED\b|د\.إ)")),
)


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


_HTML_CACHE: dict[str, tuple[str, str]] = {}


def _fetch_html_fast(url: str, timeout_seconds: float) -> tuple[str, str]:
    cached = _HTML_CACHE.get(url)
    if cached is not None:
        return cached
    final_url, html_text = _fetch_html_uncached(url, timeout_seconds)
    if html_text:
        _HTML_CACHE[url] = (final_url, html_text)
        _HTML_CACHE[final_url] = (final_url, html_text)
        if len(_HTML_CACHE) > 40:
            _HTML_CACHE.pop(next(iter(_HTML_CACHE)))
    return final_url, html_text


def _fetch_html_uncached(url: str, timeout_seconds: float) -> tuple[str, str]:
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


def fetch_product_html(url: str, timeout_seconds: float = 20.0) -> tuple[str, str]:
    """Return (final_url, HTML) for a product page.

    A plain request is tried first. Stores that answer it with a bot wall or a
    bare script shell are opened in real Chrome, the same way a pasted link is.
    The result is held briefly: one post reads the same page for its size grid
    and for its gallery, and opening Chrome twice for that is pure waste.
    """
    cached = _cached_page(url)
    if cached is not None:
        return cached

    final_url, html_text = _fetch_html_fast(url, timeout_seconds)
    if _carries_product_markup(html_text):
        return _remember_page(url, final_url or url, html_text)

    page_url = final_url or url
    logger.info("Product page %s came back empty or blocked, opening it in Chrome", page_url)
    for attempt in range(1, BROWSER_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(BROWSER_RETRY_PAUSE_SECONDS)
        try:
            rendered, rendered_url = _fetch_html_browser(page_url, timeout_seconds)
        except Exception as exc:
            # Chrome needs a few seconds to open its debug port and nodriver
            # waits under three. On a loaded box that race is lost often
            # enough that a single try costs the caption its size grid.
            logger.warning(
                "Chrome attempt %s of %s could not open the product page %s: %s",
                attempt,
                BROWSER_ATTEMPTS,
                page_url,
                exc,
            )
            continue
        if rendered and not _is_blocked_page(rendered):
            return _remember_page(url, rendered_url or page_url, rendered)
        logger.warning(
            "Chrome attempt %s of %s still saw a bot wall on %s", attempt, BROWSER_ATTEMPTS, page_url
        )
    return page_url, html_text


def _carries_product_markup(html_text: str) -> bool:
    """A store that served the real card sends a long document, not a loader."""
    if not html_text or _is_blocked_page(html_text):
        return False
    return len(html_text) > 20000


def _cached_page(url: str) -> tuple[str, str] | None:
    if _page_cache is None or _page_cache[0] != url:
        return None
    if time.monotonic() - _page_cache[3] > PAGE_CACHE_SECONDS:
        return None
    return _page_cache[1], _page_cache[2]


def _remember_page(url: str, final_url: str, html_text: str) -> tuple[str, str]:
    global _page_cache

    _page_cache = (url, final_url, html_text, time.monotonic())
    return final_url, html_text


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
    running_before = set(uc.util.get_registered_instances())
    try:
        browser = await uc.start(**kwargs)
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
        _close_chrome(uc, running_before)


def _close_chrome(uc, running_before: set) -> None:
    """Shut down every Chrome this call started, a failed start included.

    nodriver registers a browser the moment Chrome is spawned, before it has
    connected to the debug port. When that connection times out the start
    call raises and nothing but this registry still points at the live
    process, so skipping the sweep leaks a Chrome — and the half-gigabyte it
    holds is exactly what the next attempt then fails to find.
    """
    registry = uc.util.get_registered_instances()
    for browser in list(registry):
        if browser in running_before:
            continue
        try:
            browser.stop()
        except Exception as exc:
            logger.warning("Could not stop the Chrome instance we started: %s", exc)
        registry.discard(browser)


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
    matched_types = [str(item) for item in type_names if item]
    if "Product" not in matched_types and "ProductGroup" not in matched_types:
        return None
    title = str(node.get("name") or "").strip()
    offers = node.get("offers") or {}
    if not offers and isinstance(node.get("hasVariant"), list) and node["hasVariant"]:
        first_variant = node["hasVariant"][0]
        if isinstance(first_variant, dict):
            offers = first_variant.get("offers") or {}
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
    if not images and isinstance(node.get("hasVariant"), list) and node["hasVariant"]:
        first_variant = node["hasVariant"][0]
        if isinstance(first_variant, dict):
            images = _image_urls(first_variant.get("image"), url)
    availability = str(offers.get("availability") or "")
    in_stock = "OutOfStock" not in availability and "SoldOut" not in availability
    raw_id = str(node.get("sku") or node.get("productID") or node.get("mpn") or "")
    if not raw_id and isinstance(node.get("hasVariant"), list) and node["hasVariant"]:
        first_variant = node["hasVariant"][0]
        if isinstance(first_variant, dict):
            raw_id = str(first_variant.get("sku") or first_variant.get("productID") or "")
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
    images = [_absolute_url(item, url) for item in _meta_all(html_text, "og:image")]
    images = [item for item in images if item]
    if not title or not images:
        return None
    priced = _meta_price(html_text, default_currency) or _shelf_price(html_text, default_currency)
    if priced is None:
        return None
    price, currency = priced
    return _ParsedProduct(title=title, price=price, currency=currency, images=images, match_url=url)


def _meta_price(html_text: str, default_currency: str) -> tuple[Decimal, str] | None:
    """The amount a store states in its meta tags."""
    amount = (
        _meta(html_text, "product:price:amount")
        or _meta(html_text, "og:price:amount")
        or _itemprop(html_text, "price")
    )
    if not amount:
        return None
    meta_currency = (
        _meta(html_text, "product:price:currency")
        or _meta(html_text, "og:price:currency")
        or default_currency
    )
    try:
        return parse_price(f"{amount} {meta_currency}", default_currency=default_currency)
    except ValueError:
        return None


# Some storefronts ship no JSON-LD and leave the price out of their meta tags.
# The amount is then only where the shopper reads it, in the price block of the
# product card, tagged as the current price so a sale keeps the two apart.
_CURRENT_PRICE_ANCHORS = ('data-qa-id="price-container-current"', "product-detail-info__price")
_PRICE_DATA_NODE = re.compile(
    r'<data[^>]*\bdata-currency="(?P<currency>[A-Za-z]{3})"[^>]*\bvalue="(?P<amount>[\d.,]+)"',
    re.IGNORECASE,
)
_PRICE_BLOCK_WINDOW = 2000


def _shelf_price(html_text: str, default_currency: str) -> tuple[Decimal, str] | None:
    """The amount printed on the product card itself."""
    for anchor in _CURRENT_PRICE_ANCHORS:
        start = html_text.find(anchor)
        if start < 0:
            continue
        match = _PRICE_DATA_NODE.search(html_text, start, start + _PRICE_BLOCK_WINDOW)
        if match is None:
            continue
        try:
            return parse_price(
                f"{match.group('amount')} {match.group('currency')}",
                default_currency=default_currency,
            )
        except ValueError:
            continue
    return None


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
    """Every product-only shot in carousel order: front, back, close-ups, then the other stills."""
    tail: list[str] = []
    for url in [*stills[:2], *details, *stills[2:]]:
        if url and url not in tail:
            tail.append(url)
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

    if is_inditex(brand_lower) or (inditex_brand(url) and inditex_brand(url) != "zara"):
        found.extend(_inditex_urls(html_text))

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
