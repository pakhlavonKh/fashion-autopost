"""The store links the admin sent on 2026-10-07, read the way the bot reads them.

Lefties and Pull&Bear posted too few or empty photos, Charles & Keith showed
no other colours. Each test pins one of those links as it was sent: its own
colour, the store's other colours with links that open them, every photo of
that colour, and no "already published" for another colour of the product.
"""

import json

import pytest

from adapters.product_page import parse_product_html
from core.color_variants import extract_color_variants
from core.dedup import extract_duplicate_signatures, variant_parts

LEFTIES = (
    "https://www.lefties.com/es/en/woman/footwear/sneakers/"
    "retro-sneakers-c1030272270p754376580.html?colorId=001&parentId=754380551"
)
PULL_AND_BEAR = "https://www.pullandbear.com/de/retrosneaker-l11308840?cS=002&pelement=753677463"
CHARLES_KEITH = (
    "https://www.charleskeith.co.uk/gb/CK1-61720277_DK.BRW.html"
    "?ampv=plp-personalised-ahp-control-search-revamp-treatment-social-proof-on-pdp-treatment"
)
SHOTS = ("a1o", "a2o", "a3o", "a4o", "a5o", "a6o", "a7o", "a8o", "a9o", "b1o", "b2o", "b3o")


def _signatures(source: str, url: str) -> set[str]:
    product, colour = variant_parts(url)
    return extract_duplicate_signatures(source, f"{source}-{product}-{colour}", url, "Retro sneakers")


@pytest.mark.parametrize(
    ("url", "product", "colour"),
    [(LEFTIES, "754376580", "001"), (PULL_AND_BEAR, "11308840", "002"), (CHARLES_KEITH, "61720277", "dk.brw")],
)
def test_each_link_names_its_product_and_colour(url: str, product: str, colour: str) -> None:
    assert variant_parts(url) == (product, colour)


@pytest.mark.parametrize(
    ("source", "first", "second"),
    [
        ("lefties", LEFTIES, LEFTIES.replace("colorId=001", "colorId=800")),
        ("pullandbear", PULL_AND_BEAR, PULL_AND_BEAR.replace("cS=002", "cS=800")),
        ("charleskeith", CHARLES_KEITH, "https://www.charleskeith.co.uk/gb/CK1-61720277_BLACK.html"),
    ],
)
def test_another_colour_is_not_a_repeat_but_the_same_link_is(source: str, first: str, second: str) -> None:
    assert _signatures(source, first) & _signatures(source, second) == set()
    assert _signatures(source, first) & _signatures(source, first)


def _inditex_page(cdn: str, reference: str, colours: dict[str, str], own: str) -> str:
    """An Inditex product page: JSON-LD, the main gallery of the open colour, other colours' photos, colour data."""
    base = f"https://{cdn}/assets/public/aa/bb/cc/dd"
    photos = {
        code: [f"{base}/{reference}{code}-{shot}/{reference}{code}-{shot}.jpg?ts=1700000000000&amp;w=850" for shot in SHOTS]
        for code in colours
    }
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Retro sneakers",
        "image": [photos[own][0].replace("&amp;", "&")],
        "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "EUR"},
    })
    gallery = "".join(f'<img data-qa-anchor="pdpMainImage" src="{url}" alt="Retro sneakers">' for url in photos[own])
    others = "".join(f'<img src="{url}">' for code, urls in photos.items() if code != own for url in urls)
    data = json.dumps({"product": {"id": 754376580, "detail": {"colors": [
        {"id": code, "name": name, "sizes": [{"name": "36"}, {"name": "37"}, {"name": "38"}]}
        for code, name in colours.items()
    ]}}})
    return (
        f'<html><head><title>Retro sneakers</title><script type="application/ld+json">{ld}</script></head>'
        f"<body><h1>Retro sneakers</h1>{gallery}{others}<script>window.__PRODUCT__ = {data};</script></body></html>"
    )


@pytest.mark.parametrize(
    ("url", "cdn", "reference", "own", "key"),
    [
        (LEFTIES, "static.lefties.com", "37254002", "001", "colorId"),
        (PULL_AND_BEAR, "static.pullandbear.net", "11308840", "002", "cS"),
    ],
)
def test_inditex_link_gets_its_colours_and_all_twelve_photos(url: str, cdn: str, reference: str, own: str, key: str) -> None:
    html = _inditex_page(cdn, reference, {own: "WHITE / BEIGE", "800": "BLACK"}, own)
    product = parse_product_html(html, url)
    assert product is not None
    assert product.color == "WHITE / BEIGE"
    assert product.sizes == ("36", "37", "38")
    assert [photo.split("?")[0].rsplit("/", 1)[-1] for photo in product.photo_urls] == [f"{reference}{own}-{shot}.jpg" for shot in SHOTS]
    assert all(photo.startswith(f"https://{cdn}/") for photo in product.photo_urls)
    assert [(item.code, item.name, item.selected) for item in product.color_variants] == [
        (own, "WHITE / BEIGE", True),
        ("800", "BLACK", False),
    ]
    # The other colour opens with the store's own colour key, the rest of the link intact.
    other = product.color_variants[1].url
    assert f"{key}=800" in other and f"{key}={own}" not in other
    assert other.split("?")[0] == url.split("?")[0]


CK_VARIATION = "https://www.charleskeith.co.uk/on/demandware.store/Sites-ck-uk-Site/en_GB/Product-Variation"
CK_EXPECTED = [
    ("DK.BRW", "Dark Brown", "https://www.charleskeith.co.uk/gb/CK1-61720277_DK.BRW.html", True),
    ("BLACK", "Black", "https://www.charleskeith.co.uk/gb/CK1-61720277_BLACK.html", False),
]


def _rows(html: str) -> list[tuple[str, str, str, bool]]:
    return [(item.code, item.name, item.url, item.selected) for item in extract_color_variants(html, CHARLES_KEITH)]


def test_charles_keith_swatch_buttons_open_the_colour_pages() -> None:
    html = "".join(
        f'<button class="color-attribute" aria-label="Select Color {name}" '
        f'data-url="{CK_VARIATION}?dwvar_CK1-61720277_color={code}&amp;pid=CK1-61720277&amp;quantity=1"></button>'
        for code, name, _url, _selected in CK_EXPECTED
    )
    # The store's own case stays: the colour page is _DK.BRW.html, not _dk.brw.html.
    assert _rows(html) == CK_EXPECTED


def test_charles_keith_variation_attributes() -> None:
    attributes = [
        {"attributeId": "color", "displayName": "Colour", "values": [
            {"id": code, "value": code, "displayValue": name, "selected": selected, "selectable": True,
             "url": f"{CK_VARIATION}?dwvar_CK1-61720277_color={code}&pid=CK1-61720277"}
            for code, name, _url, selected in CK_EXPECTED
        ]},
        {"attributeId": "size", "displayName": "Size", "values": [{"id": "S", "value": "S", "displayValue": "S"}]},
    ]
    html = f'<script>window.pdp = {{"product":{{"id":"CK1-61720277","variationAttributes":{json.dumps(attributes)}}}}};</script>'
    assert _rows(html) == CK_EXPECTED


def test_charles_keith_plain_colour_links_and_no_other_products() -> None:
    html = (
        '<a class="swatch" href="/gb/CK1-61720277_DK.BRW.html" title="Dark Brown"></a>'
        '<a class="swatch" href="/gb/CK1-61720277_BLACK.html" title="Black"></a>'
        '<a class="tile" href="/gb/CK2-80151234_BLACK.html" title="Black">Another bag</a>'
    )
    assert _rows(html) == CK_EXPECTED


def test_charles_keith_colours_reach_the_post() -> None:
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Belted Tote Bag",
        "sku": "CK1-61720277_DK.BRW",
        "image": [
            "https://www.charleskeith.co.uk/dw/image/v2/BCWJ_PRD/on/demandware.static/-/Sites-ck-products/default/a/CK1-61720277_DK.BRW_1.jpg",
            "https://www.charleskeith.co.uk/dw/image/v2/BCWJ_PRD/on/demandware.static/-/Sites-ck-products/default/a/CK1-61720277_DK.BRW_2.jpg",
        ],
        "offers": {"@type": "Offer", "price": "79.00", "priceCurrency": "GBP"},
    })
    html = f'<script type="application/ld+json">{ld}</script>' + "".join(
        f'<a class="swatch" href="/gb/CK1-61720277_{code}.html" title="{name}"></a>' for code, name, _url, _sel in CK_EXPECTED
    )
    product = parse_product_html(html, CHARLES_KEITH)
    assert product is not None
    assert [(item.code, item.name, item.selected) for item in product.color_variants] == [
        ("DK.BRW", "Dark Brown", True),
        ("BLACK", "Black", False),
    ]
    assert product.color == "Dark Brown"
    other = parse_product_html(html, "https://www.charleskeith.co.uk/gb/CK1-61720277_BLACK.html")
    assert other is not None
    assert other.color == "Black"
    assert other.external_id != product.external_id


def test_shopify_colour_option_links_by_variant() -> None:
    product = {
        "id": 1,
        "title": "Tote",
        "options": [{"name": "Colour"}, {"name": "Size"}],
        "variants": [
            {"id": 120, "option1": "Mocha", "option2": "S"},
            {"id": 121, "option1": "Black", "option2": "S"},
            {"id": 122, "option1": "Mocha", "option2": "M"},
        ],
    }
    html = f'<script type="application/json" data-product-json>{json.dumps(product)}</script>'
    variants = extract_color_variants(html, "https://store.example/products/tote?variant=121&utm_source=ig")
    assert [(item.code, item.name, item.url, item.selected) for item in variants] == [
        ("120", "Mocha", "https://store.example/products/tote?variant=120", False),
        ("121", "Black", "https://store.example/products/tote?variant=121", True),
    ]


CK_IMAGE = (
    "https://www.charleskeith.co.uk/dw/image/v2/BCWJ_PRD/on/demandware.static/-/Sites-ck-products/default/"
    "dw1a2b3c4d/images/hi-res/2026-L2-CK1-61720277-{colour}-{shot}.jpg?sw=1152&sh=1536"
)


def test_a_store_without_its_own_reader_posts_the_whole_gallery() -> None:
    # Structured data names two photos; the page shows seven. Another colour's
    # swatch and another bag's photo sit on the same page.
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Belted Tote Bag",
        "image": [CK_IMAGE.format(colour="DK.BRW", shot=shot) for shot in (1, 2)],
        "offers": {"@type": "Offer", "price": "79.00", "priceCurrency": "GBP"},
    })
    gallery = "".join(
        f'<div class="primary-images"><img src="{CK_IMAGE.format(colour="DK.BRW", shot=shot)}" alt="Belted Tote Bag"></div>'
        for shot in range(1, 8)
    )
    swatch = f'<a href="/gb/CK1-61720277_BLACK.html" title="Black"><img src="{CK_IMAGE.format(colour="BLACK", shot=1)}"></a>'
    other_bag = f'<div class="product-tile"><img src="{CK_IMAGE.format(colour="BLACK", shot=2).replace("CK1-61720277", "CK2-80151234")}"></div>'
    html = f'<script type="application/ld+json">{ld}</script>{gallery}{swatch}{other_bag}'
    product = parse_product_html(html, CHARLES_KEITH)
    assert product is not None
    assert [photo.split("?")[0].rsplit("/", 1)[-1] for photo in product.photo_urls] == [
        f"2026-L2-CK1-61720277-DK.BRW-{shot}.jpg" for shot in range(1, 8)
    ]
