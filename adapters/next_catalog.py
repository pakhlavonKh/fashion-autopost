"""Next product pages answer the bot with a wall. The catalog search still has the card.

Used only for next.* links. Other stores never come through here.
"""

from __future__ import annotations

from decimal import Decimal
import html
import logging
import re
from urllib.parse import urlsplit

import httpx

from adapters.base import RawProduct
from adapters.scrapers.base import generate_deterministic_id
from core.color_variants import ColorVariant

logger = logging.getLogger(__name__)

_STYLE = re.compile(r"/style/([a-z]{1,3}\d{3,})/([a-z0-9-]+)(?:[/?#]|$)", re.IGNORECASE)
_CATALOG = re.compile(
    r'"domainKey"\s*:\s*"([^"]+)"[\s\S]{0,160}?"accountId"\s*:\s*"(\d+)"',
    re.IGNORECASE,
)
_CURRENCY = re.compile(r'"currencyCode"\s*:\s*"([A-Z]{3})"')
_SEARCH = "https://core.dxpapi.com/api/v1/core/"
_FIELDS = "pid,title,price,sale_price,url,brand,thumb_image,sizes,style_id"

_catalogs: dict[str, tuple[str, str, str]] = {}


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
    photo = str(chosen.get("thumb_image") or "").strip()
    if not title or price is None or price <= 0 or not photo.startswith("http"):
        return None
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
    logger.info("Next catalog read %s as %s %s %s", url, title, price, currency)
    return RawProduct(
        external_id=generate_deterministic_id("next", raw_id=item, url=url),
        source="next",
        title=title,
        price=price,
        currency=currency,
        photo_url=photo,
        product_url=url,
        in_stock=True,
        photo_urls=[photo],
        color=color,
        sizes=_sizes(chosen.get("sizes")),
        color_variants=variants,
        photos_verified=True,
    )


def _catalog(url: str) -> tuple[str, str, str]:
    host = urlsplit(url).netloc.lower()
    cached = _catalogs.get(host)
    if cached is not None:
        return cached
    home = f"{urlsplit(url).scheme or 'https'}://{host}/"
    from curl_cffi import requests as curl_requests

    response = curl_requests.get(home, impersonate="chrome", timeout=20, allow_redirects=True)
    text = response.text or ""
    found = _CATALOG.search(text)
    if found is None or response.status_code != 200:
        raise RuntimeError(f"Next homepage did not name its catalog ({response.status_code})")
    currency_match = _CURRENCY.search(text)
    if currency_match:
        currency = currency_match.group(1)
    elif host.endswith(".co.uk"):
        currency = "GBP"
    else:
        currency = "EUR"
    catalog = (found.group(2), found.group(1), currency)
    _catalogs[host] = catalog
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


def _sizes(raw: object) -> tuple[str, ...]:
    labels: list[str] = []
    seen: set[str] = set()
    values = raw if isinstance(raw, list) else []
    for item in values:
        text = re.sub(r"^eu", "", str(item).strip(), flags=re.IGNORECASE)
        text = text.replace("-", " ").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        labels.append(text.upper() if text.isalpha() else text)
    return tuple(labels)


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
