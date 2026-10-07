"""Stores that draw the gallery and sizes inside web components (Pull&Bear).

The Pull&Bear sneaker was posted with one photo of five and no sizes: its page
hands the photos to <gallery-element .xmedias=…>, which draws them inside its
shadow root, and loads colours and sizes from the store's own API. The page
HTML carries only the og:image. The head below is the one the store served on
2026-10-07 (from the server's --inspect-url run).
"""

import asyncio
import json

from adapters.product_page import (
    _append_to_body,
    _needs_store_data,
    _page_snapshot,
    _with_store_data,
    parse_product_html,
)

URL = "https://www.pullandbear.com/de/retrosneaker-l11308840?cS=002&pelement=753677463"
BASE = "https://static.pullandbear.net/assets/public/3aa0/2217/8a2e486e94e7/0bc1a4cb110a"
MAIN = f"{BASE}/11308840002-M/11308840002-M.jpg?ts=1780572103801"
LD = {
    "@context": "http://schema.org/",
    "@type": "Product",
    "name": "Retro-Sneaker",
    "image": MAIN,
    "sku": "C1130884000201-I2026",
    "mpn": "1308/840",
    "brand": {"@type": "Brand", "name": "pullandbear"},
    "offers": {
        "@type": "Offer",
        "priceCurrency": "EUR",
        "price": "29.99",
        "url": "https://www.pullandbear.com/de/retrosneaker-l11308840",
        "category": "Schuhe",
        "color": "Natur",
        "availability": "http://schema.org/InStock",
    },
}
# What the page HTML holds: the og:image, JSON-LD, and other products' photos.
PAGE = (
    '<html><head><meta property="og:title" content="Retro-Sneaker | Pull&amp;Bear Deutschland">'
    f'<meta property="og:image" content="{MAIN}">'
    f'<script type="application/ld+json">{json.dumps(LD)}</script></head><body>'
    '<nav><img src="https://static.pullandbear.net/2/photos//2024/V/0/1/p/8674/347/800/8674347800_2_6_8.jpg">'
    '<img src="https://static.pullandbear.net/2/photos//2024/V/1/1/p/1505/340/100/1505340100_2_1_8.jpg"></nav>'
    "<h1>Retro-Sneaker</h1><gallery-element></gallery-element></body></html>"
)
SHOTS = ("M", "A1M", "A2M", "A3M", "A4M")
SHADOW = '<div data-shadow-host="gallery-element">' + "".join(
    f'<div class="carousel__item"><img src="{BASE}/11308840002-{shot}/11308840002-{shot}.jpg?ts=1780572103801&amp;w=1083"></div>'
    for shot in SHOTS
) + "</div>"
STORE_API = json.dumps({
    "id": 753677463,
    "name": "Retro-Sneaker",
    "detail": {
        "reference": "C1130884000201-I2026",
        "colors": [
            {"id": "002", "name": "NATUR", "sizes": [{"name": str(size)} for size in range(36, 42)]},
            {"id": "800", "name": "SCHWARZ", "sizes": [{"name": str(size)} for size in range(36, 39)]},
        ],
    },
})


def _names(urls: list[str]) -> list[str]:
    return [url.split("?")[0].rsplit("/", 1)[-1] for url in urls]


def test_the_page_html_alone_gives_the_one_photo_that_was_posted() -> None:
    product = parse_product_html(PAGE, URL)
    assert product is not None
    assert _names(product.photo_urls) == ["11308840002-M.jpg"]
    assert product.sizes == ()


def test_the_gallery_drawn_inside_the_web_component_is_read_whole() -> None:
    product = parse_product_html(_append_to_body(PAGE, SHADOW), URL)
    assert product is not None
    # Five photos of this colour; the menu's photos of other products stay out.
    assert _names(product.photo_urls) == [f"11308840002-{shot}.jpg" for shot in SHOTS]


def test_the_store_data_gives_sizes_and_the_colour_choice() -> None:
    store = '<script type="application/json" data-bot-source="store-api">' + STORE_API + "</script>"
    product = parse_product_html(_append_to_body(PAGE, SHADOW + store), URL)
    assert product is not None
    assert product.sizes == ("36", "37", "38", "39", "40", "41")
    assert [(item.code, item.name, item.selected) for item in product.color_variants] == [
        ("002", "NATUR", True),
        ("800", "SCHWARZ", False),
    ]
    assert product.color_variants[1].url == (
        "https://www.pullandbear.com/de/retrosneaker-l11308840?pelement=753677463&cS=800"
    )
    assert len(product.photo_urls) == 5


class _Page:
    """Enough of a nodriver tab for the snapshot helpers."""

    def __init__(self, html: str, shadow: object = "", store: object = "[]") -> None:
        self.url = URL
        self._html = html
        self._results = {"shadow": shadow, "store": store}

    async def get_content(self) -> str:
        return self._html

    async def evaluate(self, script: str, await_promise: bool = False, return_by_value: bool = False):
        key = "store" if "itxrest" in script else "shadow"
        result = self._results[key]
        if isinstance(result, Exception):
            raise result
        return result


def test_a_snapshot_includes_what_the_web_components_draw() -> None:
    richness, html, page_url = asyncio.run(_page_snapshot(_Page(PAGE, shadow=SHADOW), URL))
    assert richness[0] == 5
    assert html.index("data-shadow-host") < html.index("</body>")
    assert page_url == URL


def test_a_page_without_web_components_reads_as_before() -> None:
    # nodriver hands back a RemoteObject, not a string, for an empty result.
    richness, html, _url = asyncio.run(_page_snapshot(_Page(PAGE, shadow=object()), URL))
    assert html == PAGE
    assert richness[0] == 1


def test_store_data_is_added_only_when_it_makes_the_read_fuller() -> None:
    snapshot = asyncio.run(_page_snapshot(_Page(PAGE, shadow=SHADOW), URL))
    fuller = asyncio.run(_with_store_data(_Page(PAGE, store=json.dumps([STORE_API])), URL, snapshot))
    assert fuller[0] == (5, 1, 1)
    assert 'data-bot-source="store-api"' in fuller[1]

    unrelated = json.dumps([json.dumps({"ok": True})])
    assert asyncio.run(_with_store_data(_Page(PAGE, store=unrelated), URL, snapshot)) == snapshot
    failing = _Page(PAGE, store=RuntimeError("fetch failed"))
    assert asyncio.run(_with_store_data(failing, URL, snapshot)) == snapshot


def test_store_data_is_asked_for_whatever_the_read_lacks() -> None:
    assert _needs_store_data((5, 0, 0))
    assert _needs_store_data((5, 1, 0))
    assert _needs_store_data((2, 1, 1))
    assert not _needs_store_data((5, 1, 1))
