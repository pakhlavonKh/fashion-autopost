"""Linzi colours and photos, and a Next page that the store no longer opens."""

from decimal import Decimal

import pytest

from adapters.base import RawProduct
from adapters.next_catalog import fetch_next_product
from adapters.product_page import fetch_product_page
from core.color_variants import extract_color_variants
from core.gallery import ordered_photos


LINZI = """
<div data-product-media-list>
  <div data-image-src="//www.linzi.com/cdn/shop/files/CAPSULECAMEL.jpg?v=1"></div>
  <div data-image-src="//www.linzi.com/cdn/shop/files/CAPSULECAMELPU_2.jpg?v=1"></div>
</div>
<img src="//www.linzi.com/cdn/shop/files/IMG_7538_1.png" />
<img src="//www.linzi.com/cdn/shop/files/image_2241.png" />
<div class="product__siblings">
  <a href="/en-de/products/capsule-brown" class="sibling__link">
    <span class="visually-hidden">brown</span>
  </a>
  <a href="/en-de/products/capsule-camel" class="sibling__link sibling__link--current">
    <span class="visually-hidden">camel</span>
  </a>
</div>
"""


def test_linzi_keeps_the_gallery_and_offers_each_colour() -> None:
    page = "https://www.linzi.com/en-de/products/capsule-camel"
    photos = ordered_photos(LINZI, "linzi", page)
    assert photos == [
        "https://www.linzi.com/cdn/shop/files/CAPSULECAMEL.jpg",
        "https://www.linzi.com/cdn/shop/files/CAPSULECAMELPU_2.jpg",
    ]
    colours = extract_color_variants(LINZI, page, current_color="Camel")
    assert [(item.name, item.selected) for item in colours] == [("Brown", False), ("Camel", True)]
    assert colours[1].url == "https://www.linzi.com/en-de/products/capsule-camel"


def test_next_blocked_page_is_read_from_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "adapters.next_catalog._catalog",
        lambda url: ("7059", "nextint_en_es", "EUR"),
    )
    monkeypatch.setattr(
        "adapters.next_catalog._search",
        lambda account, domain, style: [
            {
                "pid": "Y72635",
                "title": "black forever comfort® borg lined suede mules",
                "price": 50,
                "sale_price": 50,
                "style_id": "sv124032",
                "sizes": ["eu36", "eu37", "eu38", "eu39", "eu40", "eu41", "eu42", "eu43"],
                "thumb_image": "https://xcdn.next.co.uk/common/items/default/default/itemimages/Y72635.jpg",
                "url": "https://www.next.es/style/SV124032/Y72635#Y72635",
            }
        ],
    )
    product = fetch_next_product("https://www.next.es/en/style/sv124032/y72635")
    assert product is not None
    assert product.title.startswith("black forever comfort")
    assert product.price == Decimal("50")
    assert product.currency == "EUR"
    assert product.color == "Black"
    assert product.sizes == ("36", "37", "38", "39", "40", "41", "42", "43")
    assert product.photo_url.endswith("Y72635.jpg")
    assert product.color_variants == ()


def test_a_blocked_next_link_does_not_open_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    wall = "<html><body><div id='sec-if-cpt-container'></div></body></html>"
    monkeypatch.setattr("adapters.product_page._fetch_html_fast", lambda url, timeout: (url, wall))

    def fail_browser(url: str, timeout: float) -> tuple[str, str]:
        raise AssertionError("Chrome should not open a Next wall")

    monkeypatch.setattr("adapters.product_page._fetch_html_browser", fail_browser)
    ready = RawProduct(
        external_id="next-Y72635",
        source="next",
        title="black mules",
        price=Decimal("50"),
        currency="EUR",
        photo_url="https://xcdn.next.co.uk/Y72635.jpg",
        product_url="https://www.next.es/en/style/sv124032/y72635",
        in_stock=True,
    )
    monkeypatch.setattr("adapters.next_catalog.fetch_next_product", lambda url: ready)
    product = fetch_product_page("https://www.next.es/en/style/sv124032/y72635")
    assert product.external_id == "next-Y72635"
