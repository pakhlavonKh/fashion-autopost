"""How a product link is read: a plain request, Chrome, and which read is kept.

Lefties posted one photo of four and no sizes: its app draws the gallery and
the size list after the first paint, and the bot kept the first paint.
"""

import json

import pytest

import adapters.product_page as product_page
from adapters.product_page import ProductPageError, fetch_product_html, fetch_product_page
from core.product_facts import extract_site_facts

URL = "https://www.lefties.com/es/en/woman/footwear/sneakers/retro-sneakers-c1030272270p754376580.html?colorId=001"
BASE = "https://static.lefties.com/assets/public/aa/bb/cc/dd"
SHOTS = [f"{BASE}/37254002001-{shot}/37254002001-{shot}.jpg?ts=17&w=850" for shot in ("a1o", "a2o", "a3o", "a4o")]


def _page(photos: int, sizes: bool = True) -> str:
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Retro sneakers",
        "image": [SHOTS[0]],
        "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "EUR"},
    })
    gallery = "".join(f'<img data-qa-anchor="pdpMainImage" src="{url}">' for url in SHOTS[:photos])
    size_list = "".join(f'<button class="size-selector__size">{size}</button>' for size in range(35, 42)) if sizes else ""
    return (
        f'<html><head><script type="application/ld+json">{ld}</script></head><body>'
        f"<h1>Retro sneakers</h1>{gallery}<div>{size_list}</div>{' ' * 21000}</body></html>"
    )


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_page, "_page_cache", None)


def _serve(monkeypatch: pytest.MonkeyPatch, plain: str, chrome) -> list[str]:
    opened: list[str] = []

    def browser(url: str, timeout_seconds: float) -> tuple[str, str]:
        opened.append(url)
        return chrome(url)

    monkeypatch.setattr(product_page, "_fetch_html_fast", lambda url, timeout_seconds: (url, plain))
    monkeypatch.setattr(product_page, "_fetch_html_browser", browser)
    return opened


def test_a_plain_read_with_one_photo_is_completed_in_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _serve(monkeypatch, _page(1, sizes=False), lambda url: (_page(4), url))
    product = fetch_product_page(URL)
    assert opened == [URL]
    assert [photo.split("?")[0].rsplit("/", 1)[-1] for photo in product.photo_urls] == [
        "37254002001-a1o.jpg",
        "37254002001-a2o.jpg",
        "37254002001-a3o.jpg",
        "37254002001-a4o.jpg",
    ]
    assert product.sizes == ("35", "36", "37", "38", "39", "40", "41")
    # The gallery and size lookups that follow reuse that page, not the plain read.
    monkeypatch.setattr(product_page, "_fetch_html_fast", lambda url, timeout_seconds: pytest.fail("read again"))
    assert fetch_product_html(URL)[1] == _page(4)


def test_a_full_plain_read_does_not_open_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _serve(monkeypatch, _page(4), lambda url: pytest.fail("Chrome opened"))
    assert len(fetch_product_page(URL).photo_urls) == 4
    assert opened == []


def test_chrome_showing_another_colour_is_not_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    other_colour = URL.replace("colorId=001", "colorId=800")
    _serve(monkeypatch, _page(1), lambda url: (_page(4), other_colour))
    product = fetch_product_page(URL)
    assert len(product.photo_urls) == 1
    assert product.product_url == URL


def test_chrome_failing_keeps_the_plain_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(url: str) -> tuple[str, str]:
        raise RuntimeError("Chrome did not start")

    _serve(monkeypatch, _page(1), broken)
    assert len(fetch_product_page(URL).photo_urls) == 1


def test_no_read_at_all_is_still_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, "", lambda url: ("", url))
    with pytest.raises(ProductPageError):
        fetch_product_page(URL)


def test_a_long_app_shell_is_not_taken_for_the_product_page(monkeypatch: pytest.MonkeyPatch) -> None:
    shell = "<html><body><div id='app'></div>" + "<script>/* bundle */</script>" * 1000 + "</body></html>"
    opened = _serve(monkeypatch, shell, lambda url: (_page(4), url))
    assert fetch_product_html(URL) == (URL, _page(4))
    assert opened == [URL]


def test_a_page_script_template_is_not_a_size() -> None:
    html = (
        '<button class="size-selector__size">35</button><button class="size-selector__size">36</button>'
        '<script>sizes.map(s => `<button class="size-selector__size" data-size="${s}">${s}</button>`)</script>'
    )
    assert extract_site_facts(html, URL)[1] == ("35", "36")


SNEAKERS = "https://www.lefties.com/es/en/woman/footwear/sneakers/retro-sneakers-c1030272270p754376580.html?colorId=001"
SHOE_SIZES = ("35", "36", "37", "38", "39", "40", "41")
LETTER_BLOCK = (
    '<section class="recommendations"><div class="product-card">'
    + "".join(f'<span class="product-card__size">{size}</span>' for size in ("XS", "S", "M", "L", "XL", "One Size"))
    + "</div></section>"
)


def _shoe_data() -> str:
    colours = [{"id": "001", "name": "WHITE", "sizes": [{"name": size} for size in SHOE_SIZES]}]
    return f"<script>window.__PRODUCT__ = {json.dumps({'product': {'detail': {'colors': colours}}})};</script>"


def test_sneakers_keep_their_shoe_sizes_next_to_a_letter_size_list() -> None:
    # Before: the letter list won over the numbers, and the post read «от XS до One Size».
    assert extract_site_facts(_shoe_data() + LETTER_BLOCK, SNEAKERS)[1] == SHOE_SIZES
    buttons = "".join(f'<button class="size-selector__size">{size}</button>' for size in SHOE_SIZES)
    assert extract_site_facts(buttons + LETTER_BLOCK, SNEAKERS)[1] == SHOE_SIZES


def test_a_shoe_is_known_by_its_name_when_the_link_does_not_say_so() -> None:
    link = "https://www.pullandbear.com/de/retro-l11308840?cS=002"
    html = '<meta property="og:title" content="Retro-Sneaker">' + _shoe_data() + LETTER_BLOCK
    assert extract_site_facts(html, link)[1] == SHOE_SIZES


def test_a_shoe_page_with_letter_sizes_only_gets_no_size_line() -> None:
    assert extract_site_facts(LETTER_BLOCK, SNEAKERS)[1] == ()


def test_clothes_keep_letter_sizes() -> None:
    dress = "https://www.lefties.com/es/en/woman/dresses/midi-dress-c1030267503p754000001.html?colorId=800"
    html = "".join(f'<button class="size-selector__size">{size}</button>' for size in ("XS", "S", "M", "L", "XL"))
    assert extract_site_facts(html, dress)[1] == ("XS", "S", "M", "L", "XL")


def test_sneakers_inside_a_link_word_are_footwear_without_a_heel() -> None:
    from core.product_facts import is_footwear, is_heeled_footwear

    link = "https://www.pullandbear.com/de/retrosneaker-l11308840?cS=002&pelement=753677463"
    assert is_footwear(link)
    assert not is_heeled_footwear(link)


def test_a_longer_size_list_later_on_the_page_does_not_win() -> None:
    from decimal import Decimal

    from adapters.base import RawProduct
    from adapters.product_page import _richness

    def read(sizes: tuple[str, ...]) -> RawProduct:
        return RawProduct("id", "lefties", "Retro sneakers", Decimal("29.99"), "EUR", SHOTS[0], SNEAKERS, True,
                          photo_urls=list(SHOTS), sizes=sizes)

    own = read(SHOE_SIZES)
    later = read(SHOE_SIZES + ("XS", "S", "M", "L", "XL", "One Size"))
    assert not _richness(later) > _richness(own)
    assert _richness(own) > _richness(read(()))


def test_uk_and_half_shoe_sizes_are_shoe_sizes() -> None:
    from core.product_facts import shoe_sizes

    assert shoe_sizes(("3", "4.5", "UK 6", "37½", "36/37", "XS", "One Size", "M")) == ("3", "4.5", "UK 6", "37½", "36/37")
