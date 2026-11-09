"""Linzi colours and photos, and a Next page that the store no longer opens."""

from decimal import Decimal

import pytest

from adapters.base import RawProduct
from adapters.next_catalog import (
    _CATALOG_TTL_SECONDS,
    _catalog_ids,
    _fresh_catalog,
    _is_real_shot,
    _shot_exists,
    _sizes,
    fetch_next_product,
)
from core.product_facts import site_description
from adapters.product_page import fetch_product_page
from core.color_variants import extract_color_variants
from core.gallery import ordered_photos
from core.pipeline import post_photo_urls


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
    monkeypatch.setattr("adapters.next_catalog._existing_shots", lambda urls: [])
    product = fetch_next_product("https://www.next.es/en/style/sv124032/y72635")
    assert product is not None
    assert product.title.startswith("black forever comfort")
    assert product.price == Decimal("50")
    assert product.currency == "EUR"
    assert product.color == "Black"
    assert product.sizes == ("36", "37", "38", "39", "40", "41", "42", "43")
    assert product.photo_url.endswith("Y72635.jpg")
    assert product.photo_urls == [product.photo_url]
    assert product.color_variants == ()


def test_next_keeps_every_shot_of_the_linked_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "adapters.next_catalog._catalog",
        lambda url: ("7059", "nextint_en_es", "EUR"),
    )
    monkeypatch.setattr(
        "adapters.next_catalog._search",
        lambda account, domain, style: [
            {
                "pid": "V95287",
                "title": "black forever comfort® jewelled point toe ballet shoes",
                "price": 47,
                "sale_price": 47,
                "style_id": "sv123942",
                "sizes": ["eu35", "eu36"],
                "thumb_image": "https://xcdn.next.co.uk/common/items/default/default/itemimages/3_4Ratio/Product_SIP/Lge/V95287.jpg",
                "url": "https://www.next.es/style/SV123942/V95287#V95287",
            }
        ],
    )

    def listed(urls: list[str]) -> list[str]:
        assert urls[0].endswith("/V95287s.jpg")
        assert urls[1].endswith("/V95287s2.jpg")
        assert "Product_SIP" not in urls[0]
        return urls[:3]

    monkeypatch.setattr("adapters.next_catalog._existing_shots", listed)
    product = fetch_next_product("https://www.next.es/en/style/sv123942/v95287")
    assert product is not None
    names = [url.rsplit("/", 1)[-1] for url in product.photo_urls]
    assert names == ["V95287s.jpg", "V95287s2.jpg", "V95287s3.jpg"]
    assert product.photo_url == product.photo_urls[0]
    assert product.photos_verified is True
    assert post_photo_urls(product) == product.photo_urls


class _Head:
    def __init__(self, status: int, kind: str, length: str, total: str = "") -> None:
        self.status_code = status
        self.headers = {"content-type": kind, "content-length": length}
        if total:
            self.headers["content-range"] = f"bytes 0-32/{total}"


class _ShotClient:
    def __init__(self, heads: list[object], gets: list[object] | None = None) -> None:
        self.heads = list(heads)
        self.gets = list(gets or [])
        self.get_calls = 0

    def head(self, url: str) -> object:
        item = self.heads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url: str, headers: dict | None = None) -> object:
        self.get_calls += 1
        item = self.gets.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_a_tiny_next_image_is_not_fetched_again() -> None:
    client = _ShotClient([_Head(200, "image/jpeg", "120")])
    assert _shot_exists(client, "https://xcdn.next.co.uk/tiny.jpg") is False
    assert client.get_calls == 0


def test_a_next_shot_is_asked_again_when_the_first_ask_fails() -> None:
    client = _ShotClient([TimeoutError("slow"), _Head(200, "image/jpeg", "326097")])
    assert _shot_exists(client, "https://xcdn.next.co.uk/ok.jpg") is True


def test_a_ranged_next_reply_still_has_to_be_a_full_photo() -> None:
    small = _ShotClient([_Head(403, "text/html", "0")], [_Head(206, "image/jpeg", "33", total="100")])
    assert _shot_exists(small, "https://xcdn.next.co.uk/small.jpg") is False
    large = _ShotClient([_Head(403, "text/html", "0")], [_Head(206, "image/jpeg", "33", total="326097")])
    assert _shot_exists(large, "https://xcdn.next.co.uk/large.jpg") is True


def test_next_drops_only_a_size_the_catalog_marks_unavailable() -> None:
    assert _sizes(
        ["eu36", "eu37", "eu38"],
        [{"available": ["true"]}, {"available": ["false"]}, {"available": ["true"]}],
    ) == ("36", "38")
    assert _sizes(["eu36", "eu37"], None) == ("36", "37")
    assert _sizes(["eu355"], []) == ("35.5",)
    assert _sizes(["eu36"], [{}]) == ("36",)
    assert _sizes(["eu36"], [{"available": []}]) == ("36",)


def test_next_half_sizes_stay_in_the_caption() -> None:
    sizes = _sizes(["eu355", "eu36", "eu375", "eu38", "eu43"])
    assert sizes == ("35.5", "36", "37.5", "38", "43")
    assert site_description("Black", sizes) == "Размеры от 35.5 до 43.\nЦвет: черный."


def test_next_catalog_ids_expire_after_an_hour() -> None:
    remembered = ("7059", "nextint_en_es", "EUR", 1_000.0)
    assert _fresh_catalog(remembered, 1_000.0 + _CATALOG_TTL_SECONDS - 1) == ("7059", "nextint_en_es", "EUR")
    assert _fresh_catalog(remembered, 1_000.0 + _CATALOG_TTL_SECONDS) is None
    assert _fresh_catalog(None, 1_000.0) is None


def test_next_catalog_ids_survive_either_field_order() -> None:
    newer = (
        '{"bloomreachDomainKey":"next_global",'
        '"bloomReachConfig":{"accountId":"7059","domainKey":"nextint_en_es"}}'
    )
    older = '{"domainKey":"next","accountId":"6042","bloomreachDomainKey":"next_global"}'
    assert _catalog_ids(newer) == ("7059", "nextint_en_es")
    assert _catalog_ids(older) == ("6042", "next")
    assert _catalog_ids('{"bloomreachDomainKey":"next_global"}') is None


def test_a_next_shot_is_a_real_image_and_not_a_tiny_file() -> None:
    assert _is_real_shot(200, "image/jpeg", "326097") is True
    assert _is_real_shot(200, "image/webp", None) is True
    assert _is_real_shot(404, "image/jpeg", "326097") is False
    assert _is_real_shot(200, "text/html", "326097") is False
    assert _is_real_shot(200, "image/jpeg", "120") is False


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
