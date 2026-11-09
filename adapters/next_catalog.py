"""Next product pages answer the bot with a wall. The catalog search still has the card.

Used only for next.* links. Other stores never come through here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import html
import logging
import re
import time
from urllib.parse import urlsplit

import httpx

from adapters.base import RawProduct
from adapters.scrapers.base import generate_deterministic_id
from core.color_variants import ColorVariant

logger = logging.getLogger(__name__)

_STYLE = re.compile(r"/style/([a-z]{1,3}\d{3,})/([a-z0-9-]+)(?:[/?#]|$)", re.IGNORECASE)
# The homepage lists these in either order, and also a similarly named
# bloomreachDomainKey that is not the search catalog.
_ACCOUNT = re.compile(r'"accountId"\s*:\s*"(\d+)"', re.IGNORECASE)
_DOMAIN = re.compile(r'"domainKey"\s*:\s*"([^"]+)"', re.IGNORECASE)
_CURRENCY = re.compile(r'"currencyCode"\s*:\s*"([A-Z]{3})"')
_SEARCH = "https://core.dxpapi.com/api/v1/core/"
_FIELDS = "pid,title,price,sale_price,url,brand,thumb_image,sizes,style_id,available"
# The catalog record has one picture, the cover. The rest of that colour's
# gallery is {item}s.jpg, {item}s2.jpg, … on the image host. The cover file
# is the same bytes as the first of those.
_SHOTS = "https://xcdn.next.co.uk/common/items/default/default/itemimages/3_4Ratio/product/lge"
_MAX_SHOTS = 16
_MIN_SHOT_BYTES = 8000
_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "image/jpeg,image/webp,image/*;q=0.8",
}

_CATALOG_TTL_SECONDS = 60 * 60
_catalogs: dict[str, tuple[str, str, str, float]] = {}


def is_next_host(url: str) -> bool:
    host = urlsplit(url or "").netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host == "next.com" or host.startswith("next.")


def is_next_product_url(url: str) -> bool:
    return is_next_host(url) and _STYLE.search(urlsplit(url or "").path or "") is not None


def fetch_next_product(url: str) -> RawProduct | None:
    """The colour named in a Next style link, from the public catalog search."""
    match = _STYLE.search(urlsplit(url or "").path or "")
    if match is None or not is_next_host(url):
        return None
    style, item = match.group(1).lower(), match.group(2).upper()
    try:
        account, domain, currency = _catalog(url)
        docs = _search(account, domain, style)
    except Exception as exc:
        logger.info("Next catalog could not be read for %s: %s", url, exc)
        return None
    chosen = next((doc for doc in docs if str(doc.get("pid") or "").upper() == item), None)
    if chosen is None:
        logger.info("Next catalog has no item %s in style %s", item, style)
        return None
    title = _plain(str(chosen.get("title") or ""))
    price = _price(chosen)
    cover = str(chosen.get("thumb_image") or "").strip()
    if not title or price is None or price <= 0 or not cover.startswith("http"):
        return None
    photos = _gallery(item, cover)
    colours = _colours(docs)
    names = {str(doc.get("pid") or "").upper(): name for doc, name in colours}
    color = names.get(item) or (title.split()[0] if title else "")
    variants = tuple(
        ColorVariant(
            str(doc.get("pid") or "").upper(),
            name,
            _same_host(url, str(doc.get("url") or "")),
            selected=str(doc.get("pid") or "").upper() == item,
        )
        for doc, name in colours
        if len(colours) >= 2 and str(doc.get("url") or "").startswith("http")
    )
    logger.info("Next catalog read %s as %s %s %s with %s photo(s)", url, title, price, currency, len(photos))
    return RawProduct(
        external_id=generate_deterministic_id("next", raw_id=item, url=url),
        source="next",
        title=title,
        price=price,
        currency=currency,
        photo_url=photos[0],
        product_url=url,
        in_stock=True,
        photo_urls=photos,
        color=color,
        sizes=_sizes(chosen.get("sizes"), chosen.get("variants")),
        color_variants=variants,
        photos_verified=True,
    )


def _gallery(item: str, cover: str) -> list[str]:
    """Every shot of this item code. The cover stays when the gallery cannot be listed."""
    names = [f"{item}s"] + [f"{item}s{index}" for index in range(2, _MAX_SHOTS + 1)]
    urls = [f"{_SHOTS}/{name}.jpg" for name in names]
    try:
        shots = _existing_shots(urls)
    except Exception as exc:
        logger.info("Next gallery could not be listed for %s: %s", item, exc)
        shots = []
    if shots:
        logger.info("Next gallery for %s has %s shots", item, len(shots))
        return shots
    return [cover]


def _existing_shots(urls: list[str]) -> list[str]:
    """The numbered shots that exist, in that order. Missing numbers are skipped."""
    if not urls:
        return []
    with httpx.Client(timeout=8, follow_redirects=True, headers=_IMAGE_HEADERS) as client:
        with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
            found = list(pool.map(lambda url: _shot_exists(client, url), urls))
    return [url for url, ok in zip(urls, found) if ok]


def _shot_exists(client: httpx.Client, url: str) -> bool:
    response = _request(client, url, "head")
    if response is None:
        return False
    if _is_real_shot(response.status_code, response.headers.get("content-type"), response.headers.get("content-length")):
        return True
    if response.status_code == 404:
        return False
    # A small image with status 200 is not a second-chance photo.
    if response.status_code == 200 and "image" in (response.headers.get("content-type") or "").lower():
        return False
    ranged = _request(client, url, "get")
    if ranged is None:
        return False
    return _is_real_shot(200 if ranged.status_code in (200, 206) else ranged.status_code, ranged.headers.get("content-type"), _full_length(ranged))


def _request(client: httpx.Client, url: str, method: str) -> httpx.Response | None:
    """Ask once, and once more when the first ask does not come back."""
    for _attempt in range(2):
        try:
            if method == "head":
                return client.head(url)
            return client.get(url, headers={"Range": "bytes=0-32"})
        except Exception:
            continue
    return None


def _full_length(response: httpx.Response) -> str | None:
    """The file size, including when the server sent only the first bytes."""
    span = response.headers.get("content-range") or ""
    total = re.search(r"/(\d+)\s*$", span)
    if total:
        return total.group(1)
    if response.status_code == 200:
        return response.headers.get("content-length")
    return None


def _is_real_shot(status: int, content_type: str | None, length: str | None) -> bool:
    if status != 200 or "image" not in (content_type or "").lower():
        return False
    if length and length.isdigit() and int(length) < _MIN_SHOT_BYTES:
        return False
    return True


def _catalog_ids(text: str) -> tuple[str, str] | None:
    account = _ACCOUNT.search(text or "")
    domain = _DOMAIN.search(text or "")
    if account is None or domain is None:
        return None
    return account.group(1), domain.group(1)


def _fresh_catalog(cached: tuple[str, str, str, float] | None, now: float) -> tuple[str, str, str] | None:
    """The homepage's catalog ids, while they are still younger than an hour."""
    if cached is None or now - cached[3] >= _CATALOG_TTL_SECONDS:
        return None
    return cached[0], cached[1], cached[2]


def _catalog(url: str) -> tuple[str, str, str]:
    host = urlsplit(url).netloc.lower()
    fresh = _fresh_catalog(_catalogs.get(host), time.monotonic())
    if fresh is not None:
        return fresh
    home = f"{urlsplit(url).scheme or 'https'}://{host}/"
    from curl_cffi import requests as curl_requests

    response = curl_requests.get(home, impersonate="chrome", timeout=20, allow_redirects=True)
    text = response.text or ""
    found = _catalog_ids(text)
    if found is None or response.status_code != 200:
        raise RuntimeError(f"Next homepage did not name its catalog ({response.status_code})")
    currency_match = _CURRENCY.search(text)
    if currency_match:
        currency = currency_match.group(1)
    elif host.endswith(".co.uk"):
        currency = "GBP"
    else:
        currency = "EUR"
    account, domain = found
    catalog = (account, domain, currency)
    _catalogs[host] = (*catalog, time.monotonic())
    return catalog


def _search(account: str, domain: str, style: str) -> list[dict]:
    response = httpx.get(
        _SEARCH,
        params={
            "account_id": account,
            "domain_key": domain,
            "request_type": "search",
            "search_type": "keyword",
            "q": "*",
            "fq": f'style_id:"{style}"',
            "rows": "40",
            "url": "https://www.next.co.uk/",
            "fl": _FIELDS,
        },
        timeout=20,
    )
    response.raise_for_status()
    docs = response.json().get("response", {}).get("docs") or []
    return [doc for doc in docs if isinstance(doc, dict)]


def _price(doc: dict) -> Decimal | None:
    amount = doc.get("sale_price")
    if amount is None:
        amount = doc.get("price")
    try:
        return Decimal(str(amount))
    except Exception:
        return None


def _sizes(raw: object, variants: object = None) -> tuple[str, ...]:
    labels: list[str] = []
    seen: set[str] = set()
    values = raw if isinstance(raw, list) else []
    flags = variants if isinstance(variants, list) else []
    for index, item in enumerate(values):
        if index < len(flags) and _size_unavailable(flags[index]):
            continue
        text = re.sub(r"^eu", "", str(item).strip(), flags=re.IGNORECASE)
        text = text.replace("-", " ").strip()
        # Next writes a half size as three digits ending in 5: eu355 is 35.5.
        if re.fullmatch(r"\d{2}5", text):
            text = f"{text[:2]}.5"
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        labels.append(text.upper() if text.isalpha() else text)
    return tuple(labels)


def _size_unavailable(variant: object) -> bool:
    """True only when the catalog explicitly says this size cannot be bought.

    A missing or empty flag is not a sold-out size.
    """
    if not isinstance(variant, dict):
        return False
    available = variant.get("available")
    if isinstance(available, list):
        if not available:
            return False
        return all(str(item).strip().lower() == "false" for item in available)
    if available is None:
        return False
    return str(available).strip().lower() == "false"


def _colours(docs: list[dict]) -> list[tuple[dict, str]]:
    titles = [_plain(str(doc.get("title") or "")) for doc in docs]
    shared = _shared_tail(titles) if len(titles) >= 2 else ""
    found: list[tuple[dict, str]] = []
    for doc, title in zip(docs, titles):
        if shared and title.endswith(shared):
            name = title[: len(title) - len(shared)].strip(" -")
        else:
            name = title.split()[0] if title else ""
        if name:
            found.append((doc, name[:1].upper() + name[1:]))
    return found


def _shared_tail(titles: list[str]) -> str:
    tail = titles[0]
    for title in titles[1:]:
        while tail and not title.endswith(tail):
            tail = tail[1:]
    tail = tail.lstrip()
    return f" {tail}" if tail and not tail.startswith(" ") else tail


def _plain(text: str) -> str:
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def _same_host(original: str, catalog_url: str) -> str:
    """Keep the country site the admin pasted. The catalog sometimes names another one."""
    if not catalog_url.startswith("http"):
        return original
    source, target = urlsplit(original), urlsplit(catalog_url)
    return target._replace(scheme=source.scheme or target.scheme, netloc=source.netloc).geturl()
