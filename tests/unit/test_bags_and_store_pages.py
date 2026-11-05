"""The November round: the bag size line, the colour last, and the stores that read badly.

Charles & Keith posted no sizes and no colours, Marks & Spencer could not be
read at all, Stradivarius bags were posted with the size buttons of the other
products on the page, and the admin bot had no one-tap «publish now».
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

import adapters.product_page as product_page
from adapters.base import RawProduct
from adapters.playwright_url_processor import resolve_product_url
from adapters.product_page import parse_product_html
from core.description_rules import apply_description_rules
from core.gallery import canonical_photo_key
from core.product_facts import (
    eu_shoe_sizes,
    extract_bag_size,
    extract_heel_height,
    extract_site_facts,
    is_bag,
    site_description,
)
from tests.unit.test_admin_intake import ADMIN_ID, _bot, _callback, _message


# --- caption order -----------------------------------------------------------


def test_the_colour_closes_part_one_after_sizes_heel_and_bag_size() -> None:
    shoe = site_description("Black", ("36", "41"), heel_height="9 cm")
    assert shoe == "Размеры от 36 до 41.\nВысота каблука: 8 см.\nЦвет: черный."

    bag = site_description("Black", ("ONE SIZE",), bag_size="высота 22 см, ширина 25,5 см, глубина 11 см")
    assert bag == "Размер сумки: высота 22 см, ширина 25,5 см, глубина 11 см.\nЦвет: черный."


def test_a_bag_without_measurements_keeps_its_size_line() -> None:
    assert site_description("Black", ("M",)) == "Размер: M.\nЦвет: черный."


def test_the_heel_rule_puts_the_heel_above_the_colour() -> None:
    product = RawProduct(
        external_id="mango-1",
        source="mango",
        title="Slingback heels",
        price=Decimal("49.99"),
        currency="EUR",
        photo_url="https://example.com/1.jpg",
        product_url="https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/99",
        in_stock=True,
        heel_height="9 cm",
    )
    text = apply_description_rules(product, "Размеры от 35 до 42.\nЦвет: черный.")
    assert text == "Размеры от 35 до 42.\nВысота каблука: 8 см.\nЦвет: черный."


# --- bag measurements --------------------------------------------------------


def test_bags_are_recognised_in_every_store_language() -> None:
    for title in ("Leather Cross Body Bag", "Bolso shopper de hombro", "Sac à main", "Borsa a tracolla", "Сумка-тоут"):
        assert is_bag(title), title
    assert is_bag("", "https://www.marksandspencer.com/leather-cross-body-bag/p/clp61266456")
    for title in ("Baggy jeans", "Slingback heels", "Vestido corto", "Paperbag trousers"):
        assert not is_bag(title), title


def test_named_measurements_on_marks_and_spencer() -> None:
    page = "<div>Item details • Height: 22 cm • Width: 25.5 cm • Depth: 11 cm Style • Zip fastening</div>"
    assert extract_bag_size(page) == "высота 22 см, ширина 25,5 см, глубина 11 см"


def test_strap_and_handle_lengths_are_not_the_bag_size() -> None:
    page = (
        "<dl><dt>Handle drop length</dt><dd>26-36 cm</dd><dt>Strap length</dt><dd>49-68 cm</dd>"
        "<dt>Weight</dt><dd>433 g</dd><dt>Width</dt><dd>30 cm</dd><dt>Depth</dt><dd>11 cm</dd>"
        "<dt>Height</dt><dd>23 cm</dd></dl>"
    )
    assert extract_bag_size(page) == "высота 23 см, ширина 30 см, глубина 11 см"


def test_measurements_with_their_legend_on_mango_and_zara() -> None:
    mango = "<p>Tamaño grande. Cierre magnético. 52.0x30.0x16.0 cm (Largo x Alto x Ancho).</p>"
    assert extract_bag_size(mango) == "высота 30 см, ширина 16 см, длина 52 см"
    zara = "<p>Alto x Ancho x Fondo: 21 x 29 x 9 cm</p>"
    assert extract_bag_size(zara) == "высота 21 см, ширина 29 см, глубина 9 см"
    bare = "<p>Medidas: 25 x 18 cm</p>"
    assert extract_bag_size(bare) == "25 × 18 см"


def test_inditex_measurements_come_from_the_store_data() -> None:
    data = {
        "sizes": [{
            "name": "M",
            "skuDimensions": [
                {"dimensionId": "14", "value": 45.0, "dimensionName": "ANCHO"},
                {"dimensionId": "51", "value": 32.0, "dimensionName": "ALTO"},
                {"dimensionId": "54", "value": 18.0, "dimensionName": "PROFUNDIDAD"},
                {"dimensionId": "76", "value": 23.0, "dimensionName": "ALTO VERTICAL DEL ASA"},
                {"dimensionId": "93", "value": 110.0, "dimensionName": "LONGITUD DE LA CORREA CRUZADA"},
            ],
        }]
    }
    page = f'<html><body><script type="application/json">{json.dumps(data)}</script></body></html>'
    assert extract_bag_size(page) == "высота 32 см, ширина 45 см, глубина 18 см"


def test_size_buttons_of_other_products_on_the_page_are_not_the_product_sizes() -> None:
    tiles = "".join(
        f'<button class="sc-x grid-product-size-item" id="product-513370024-size-button-{size}">'
        f'<span class="size-label">{size}</span></button>'
        for size in ("XS", "S", "M", "35", "36", "Talla única")
    )
    page = f"<html><head><title>Bolso bandolera</title></head><body>{tiles}</body></html>"
    assert extract_site_facts(page, "https://www.stradivarius.com/es/bolso-bandolera-tachas-l03691211")[1] == ()


# --- Charles & Keith ---------------------------------------------------------

CK_URL = "https://www.charleskeith.eu/de-en/CK1-61720261_BEIGE.html"


def _ck_variation(sizes: list[str]) -> dict:
    def colour(code: str, name: str, pid: str, selected: bool) -> dict:
        return {
            "id": code, "value": code, "displayValue": name, "selected": selected, "selectable": True,
            "url": f"https://www.charleskeith.eu/on/demandware.store/Sites-ck-group2-Site/en_DE/"
                   f"Product-VariationCache?dwvar_CK1-61720261__BEIGE_color={code}&pid={pid}&pview=variation",
        }

    return {"product": {"id": "CK1-61720261_BEIGE", "variationAttributes": [
        {"attributeId": "color", "displayName": "Colour", "values": [
            colour("05", "Beige", "CK1-61720261_BEIGE", True),
            colour("HS", "Natural", "CK1-61720261_NATURA", False),
            colour("01", "Black", "CK1-61720261_BLACK", False),
        ]},
        {"attributeId": "size", "displayName": "Size", "values": [
            {"id": f"0{index}", "value": f"0{index}", "displayValue": label, "selectable": True}
            for index, label in enumerate(sizes, start=1)
        ]},
    ]}}


def _ck_page() -> str:
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "Product", "name": "Buckle-Strap Slingback Pumps",
        "image": [f"https://www.charleskeith.eu/images/2026-L3-CK1-61720261-05-{shot}.jpg" for shot in (1, 2, 3)],
        "offers": {"@type": "Offer", "price": "55.20", "priceCurrency": "EUR"},
    })
    return (
        f'<html><head><script type="application/ld+json">{ld}</script>'
        '<link href="/on/demandware.store/Sites-ck-group2-Site/en_DE/Cart-MiniCartShow"></head><body>'
        '<div class="container product-detail product-wrapper" data-pid="CK1-61720261_BEIGE">'
        '<div class="pdp-attr pdp-attr--size" data-attr="size"><div class="skeleton_loader"></div></div>'
        '<span class="pdp-product_attribute-title">Shoe width</span><span><span>7.6 cm</span></span>'
        '<span class="pdp-product_attribute-title">Heel height</span><span><span>4.5 cm</span></span>'
        "</div></body></html>"
    )


def test_charles_and_keith_sizes_and_colours_come_from_product_variation(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    class _Response:
        status_code = 200
        text = json.dumps(_ck_variation([str(size) for size in range(34, 42)]))

    def fake_get(url: str, **_kwargs):
        asked.append(url)
        return _Response()

    import curl_cffi.requests

    monkeypatch.setattr(curl_cffi.requests, "get", fake_get)
    html = product_page._with_sfcc_variations(_ck_page(), CK_URL)
    assert asked == [
        "https://www.charleskeith.eu/on/demandware.store/Sites-ck-group2-Site/en_DE"
        "/Product-Variation?pid=CK1-61720261_BEIGE&quantity=1"
    ]
    product = parse_product_html(html, CK_URL)
    assert product is not None
    assert product.sizes == ("34", "35", "36", "37", "38", "39", "40", "41")
    assert product.color == "Beige"
    assert [(item.name, item.url, item.selected) for item in product.color_variants] == [
        ("Beige", CK_URL, True),
        ("Natural", "https://www.charleskeith.eu/de-en/CK1-61720261_NATURA.html", False),
        ("Black", "https://www.charleskeith.eu/de-en/CK1-61720261_BLACK.html", False),
    ]
    assert product.heel_height == "4.5 см"


def test_charles_and_keith_numbered_shoe_sizes_are_posted_as_eu_sizes() -> None:
    assert eu_shoe_sizes(("1", "2", "9"), CK_URL) == ("34", "35", "42")
    assert eu_shoe_sizes(("01", "05"), CK_URL) == ("34", "38")
    assert eu_shoe_sizes(("34", "35"), CK_URL) == ("34", "35")
    # Another store's numbers are left as printed.
    assert eu_shoe_sizes(("1", "2"), "https://www.zara.com/es/es/zapato-p1.html") == ("1", "2")


def test_a_shoe_width_before_the_heel_height_is_not_a_second_heel() -> None:
    assert extract_heel_height(_ck_page(), "Buckle-Strap Slingback Pumps") == "4.5 см"


# --- Marks & Spencer ---------------------------------------------------------

MS_URL = "https://www.marksandspencer.com/leather-cross-body-bag/p/clp61266456"


def _ms_page() -> str:
    images = [
        f"https://assets.digitalcontent.marksandspencer.app/images/q_auto,f_auto/SD_01_T01_9102A_Y0_X_EC_{shot}/Leather-Cross-Body-Bag"
        for shot in (90, 1, 2)
    ]
    ld = json.dumps({
        "@context": "https://schema.org/", "@type": "Product", "name": "Leather Cross Body Bag", "sku": "61266456",
        "image": images,
        "offers": {"@type": "Offer", "priceSpecification": {"@type": "UnitPriceSpecification", "price": "55",
                                                            "priceCurrency": "GBP"}},
    })
    next_data = json.dumps({"props": {"pageProps": {"productDetails": {"variants": [{
        "colour": "BLACK",
        "colourSwatchAssetId": "SD_01_T01_9102A_Y0_X_EC_88",
        "assets": [
            {"type": "ALTERNATIVE", "assetId": "SD_01_T01_9102A_Y0_X_EC_1"},
            {"type": "ALTERNATIVE", "assetId": "SD_01_T01_9102A_Y0_X_EC_2"},
            {"type": "ALTERNATIVE", "assetId": "SD_01_T01_9102A_Y0_X_EC_94"},
            {"type": "MAIN", "assetId": "SD_01_T01_9102A_Y0_X_EC_90"},
        ],
    }]}}}})
    return (
        f'<html><head><script type="application/ld+json">{ld}</script></head><body>'
        "<h1>Leather Cross Body Bag</h1><div>Item details • Height: 22 cm • Width: 25.5 cm • Depth: 11 cm</div>"
        f'<script id="__NEXT_DATA__" type="application/json">{next_data}</script></body></html>'
    )


def test_marks_and_spencer_is_read_with_its_whole_gallery() -> None:
    product = parse_product_html(_ms_page(), MS_URL)
    assert product is not None
    assert product.price == Decimal("55")
    assert product.currency == "GBP"
    assert product.color == "BLACK"
    assert [canonical_photo_key(url) for url in product.photo_urls] == [
        "sd_01_t01_9102a_y0_x_ec_90",
        "sd_01_t01_9102a_y0_x_ec_1",
        "sd_01_t01_9102a_y0_x_ec_2",
        "sd_01_t01_9102a_y0_x_ec_94",
    ]
    assert all("/image/upload/" in url for url in product.photo_urls)
    assert product.bag_size == "высота 22 см, ширина 25,5 см, глубина 11 см"


# --- the link is resolved without a browser ----------------------------------


def test_a_link_is_resolved_from_the_plain_page_and_that_page_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []
    page = '<html><head><link rel="canonical" href="https://www.example.com/p/coat-123"></head></html>'

    def fake_fetch(url: str, timeout_seconds: float) -> tuple[str, str]:
        fetched.append(url)
        return url, page

    monkeypatch.setattr(product_page, "_HTML_CACHE", {})
    monkeypatch.setattr(product_page, "_fetch_html_uncached", fake_fetch)
    resolved, _html = resolve_product_url("https://www.example.com/p/coat-123?utm_source=ig")
    assert resolved == "https://www.example.com/p/coat-123"
    assert product_page.fetch_html_fast(resolved) == ("https://www.example.com/p/coat-123", page)
    assert fetched == ["https://www.example.com/p/coat-123"]


# --- the admin bot: «Сейчас» / «Указать время» --------------------------------


def _draft_waiting_for_time(bot, sent) -> int:
    bot.handle_update(_message(ADMIN_ID, "https://www.zara.com/es/es/wool-coat-p12345678.html", update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    assert "какое время" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None and draft.status == "awaiting_time"
    return draft.id


def test_the_now_button_asks_only_for_the_channel(tmp_path: Path) -> None:
    bot, sent, scheduler, _runner = _bot(tmp_path)
    post_id = _draft_waiting_for_time(bot, sent)
    bot.handle_update(_callback(ADMIN_ID, f"time:now:{post_id}", update_id=3))
    assert "время: сейчас" in sent[-1][1].lower()
    assert "куда опубликовать" in sent[-1][1].lower()
    bot.handle_update(_callback(ADMIN_ID, f"dest:telegram:{post_id}", update_id=4))
    bot.handle_update(_callback(ADMIN_ID, f"approve:{post_id}", update_id=5))
    assert sent[-1][1] == "Публикую сейчас в Telegram"
    assert len(scheduler.jobs) == 1


def test_the_now_button_goes_straight_to_the_preview_when_instagram_is_off(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path, instagram_enabled=False)
    post_id = _draft_waiting_for_time(bot, sent)
    bot.handle_update(_callback(ADMIN_ID, f"time:now:{post_id}", update_id=3))
    assert "предпросмотр" in sent[-1][1].lower()
    assert "сейчас" in sent[-1][1].lower()


def test_the_set_time_button_asks_for_a_typed_time(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path)
    post_id = _draft_waiting_for_time(bot, sent)
    bot.handle_update(_callback(ADMIN_ID, f"time:ask:{post_id}", update_id=3))
    assert "напишите время" in sent[-1][1].lower()
    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=4))
    assert "куда опубликовать" in sent[-1][1].lower()


def test_a_time_button_pressed_after_the_time_is_set_changes_nothing(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path)
    post_id = _draft_waiting_for_time(bot, sent)
    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=3))
    bot.handle_update(_callback(ADMIN_ID, f"time:now:{post_id}", update_id=4))
    assert "уже выбрано" in sent[-1][1]
    assert bot.repo.get_manual_post(post_id).status == "awaiting_destination"
