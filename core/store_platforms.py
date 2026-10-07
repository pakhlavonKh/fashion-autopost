"""Store platforms the bot knows, shared by every module that reads a store.

Brands come and go; the platforms under them do not. Zara, Pull&Bear, Bershka,
Lefties and the other Inditex chains run one storefront and one image CDN, so
anything learnt on one of them holds for all, including a chain added later.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Every Inditex chain. A new one only needs its name here.
INDITEX_BRANDS = (
    "zara",
    "zarahome",
    "pullandbear",
    "bershka",
    "stradivarius",
    "massimodutti",
    "oysho",
    "lefties",
)
# Inditex sister chains (not Zara) select a colour with ?colorId=, except
# Pull&Bear, which uses ?cS=.
INDITEX_COLOR_PARAM = {"pullandbear": "cS"}

# static.zara.net, static.pullandbear.net, static.e-stradivarius.net, static.lefties.com …
_INDITEX_CDN = re.compile(
    r"(?:^|\.)static\.(?:e-)?(" + "|".join(INDITEX_BRANDS) + r")\.(?:net|com)$",
    re.IGNORECASE,
)


def host_of(url: str) -> str:
    return (urlsplit(url or "").netloc or "").lower().split(":")[0]


def inditex_brand(url: str) -> str:
    """The Inditex chain behind a store page or CDN link, or ""."""
    host = host_of(url)
    if not host:
        return ""
    cdn = _INDITEX_CDN.search(host)
    if cdn:
        return cdn.group(1).lower()
    label = re.sub(r"^www\d?\.", "", host).split(".")[0]
    return label if label in INDITEX_BRANDS else ""


def is_inditex(url_or_brand: str) -> bool:
    value = (url_or_brand or "").lower()
    if value in INDITEX_BRANDS:
        return True
    return bool(inditex_brand(value)) if "://" in value or "." in value else False


def is_inditex_cdn(url: str) -> bool:
    return bool(_INDITEX_CDN.search(host_of(url)))
