"""Read a single product page that an admin pasted into the Telegram bot.

Tries a plain HTTP fetch first, then Playwright, and normalizes JSON-LD /
Open Graph data into the same RawProduct the catalog pipeline already uses.
"""

from decimal import Decimal
import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from adapters.base import RawProduct
from adapters.scrapers.base import generate_deterministic_id, parse_price

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class ProductPageError(Exception):
    """The page did not contain a usable product title, price, and photo."""


def fetch_product_page(url: str, headless: bool = True, timeout_seconds: float = 25.0) -> RawProduct:
    """Load a product URL and return a normalized product."""
    html_text = _fetch_html_http(url, timeout_seconds)
    product = parse_product_html(html_text, url) if html_text else None
    if product is not None:
        return product

    logger.info("HTTP parse missed product data for %s, retrying with Playwright", url)
    try:
        rendered = _fetch_html_playwright(url, timeout_seconds, headless=headless)
    except Exception as exc:
        logger.warning("Playwright product fetch failed for %s: %s", url, exc)
        rendered = ""
    product = parse_product_html(rendered, url) if rendered else None
    if product is None:
        raise ProductPageError(
            "Не удалось прочитать товар по этой ссылке. Пришлите прямую ссылку на карточку товара."
        )
    return product


def parse_product_html(html_text: str, url: str) -> RawProduct | None:
    """Extract one product from HTML. Returns None when required fields are missing."""
    if not html_text or not url.startswith("http"):
        return None

    brand = brand_from_url(url)
    default_currency = currency_hint(url)
    title = ""
    price: Decimal | None = None
    currency = default_currency
    images: list[str] = []
    in_stock = True
    raw_id = ""

    for node in _iter_json_ld(html_text):
        types = node.get("@type")
        type_names = types if isinstance(types, list) else [types]
        if "Product" not in [str(item) for item in type_names if item]:
            continue
        parsed = _product_from_json_ld(node, url, default_currency)
        if parsed is None:
            continue
        title, price, currency, images, in_stock, raw_id = parsed
        break

    if not title:
        title = _meta(html_text, "og:title") or _meta(html_text, "twitter:title") or ""
        title = title.split("|")[0].strip()
    if price is None:
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
        if amount:
            try:
                price, currency = parse_price(f"{amount} {meta_currency}", default_currency=default_currency)
            except ValueError:
                price = None
    if not images:
        og_image = _meta(html_text, "og:image")
        if og_image:
            images = [urljoin(url, og_image)]

    images = _merge_images(images, _brand_image_urls(html_text, brand, url))
    if not title or price is None or price <= 0 or not images:
        return None

    external_id = generate_deterministic_id(brand, raw_id=raw_id or None, url=url)
    return RawProduct(
        external_id=external_id,
        source=brand,
        title=" ".join(title.split()),
        price=price,
        currency=currency,
        photo_url=images[0],
        product_url=url,
        in_stock=in_stock,
        photo_urls=images[:8],
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
    label = host.split(".")[0] if host else "store"
    return label or "store"


def currency_hint(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if "/uk/" in path or "/gb/" in path or host.endswith(".co.uk"):
        return "GBP"
    if "/us/" in path or "/en-us" in path:
        return "USD"
    if "/tr/" in path:
        return "TRY"
    return "EUR"


def _fetch_html_http(url: str, timeout_seconds: float) -> str:
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            response = client.get(url)
            if response.status_code != 200:
                logger.info("Product page HTTP %s for %s", response.status_code, url)
                return ""
            return response.text
    except Exception as exc:
        logger.info("Product page HTTP fetch failed for %s: %s", url, exc)
        return ""


def _fetch_html_playwright(url: str, timeout_seconds: float, headless: bool) -> str:
    from playwright.sync_api import sync_playwright

    from adapters.scrapers.base import dismiss_cookie_banner

    timeout_ms = int(timeout_seconds * 1000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                user_agent=BROWSER_HEADERS["User-Agent"],
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            dismiss_cookie_banner(page)
            page.wait_for_timeout(1200)
            return page.content()
        finally:
            browser.close()


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


def _product_from_json_ld(
    node: dict,
    url: str,
    default_currency: str,
) -> tuple[str, Decimal, str, list[str], bool, str] | None:
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
    return title, price, currency, images, in_stock, raw_id


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
        absolute = urljoin(page_url, value.strip())
        if absolute.startswith("http") and absolute not in cleaned:
            cleaned.append(absolute)
    return cleaned


def _brand_image_urls(html_text: str, brand: str, url: str) -> list[str]:
    found: list[str] = []
    if "mango" in brand or "mango.com" in url:
        found.extend(re.findall(r"https://media\.mango\.com/is/image/punto/[0-9]+-[0-9A-Z]+-[0-9A-Z]+", html_text))
    if "zara" in brand or "zara.com" in url:
        found.extend(re.findall(r"https://static\.zara\.net/photos/[^\"'\s]+", html_text))
    return found


def _merge_images(primary: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    for image in list(primary) + list(extra):
        if image.startswith("http") and image not in merged:
            merged.append(image)
    return merged


def _meta(html_text: str, key: str) -> str:
    patterns = [
        rf"<meta[^>]+(?:property|name|itemprop)=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)[\"']",
        rf"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name|itemprop)=[\"']{re.escape(key)}[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _itemprop(html_text: str, key: str) -> str:
    match = re.search(
        rf"<[^>]+itemprop=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)[\"']",
        html_text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""
