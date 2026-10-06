"""Colourways read from a product page, and links that keep their colour."""

from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from adapters.playwright_url_processor import choose_processed_url
from adapters.product_page import _product_angles, parse_product_html
from core.color_variants import extract_color_variants, link_names_color, mango_color_url
from core.dedup import extract_duplicate_signatures, normalize_url
from core.gallery import mango_page_identity
from core.image_downloader import unique_images
from publishers.admin_intake_bot import swap_color_line


ZARA_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Linen dress","sku":"03067301",
 "image":["https://static.zara.net/photos/2026/V/0/1/p/3067/301/800/2/w/1024/3067301800_1_1_1.jpg"],
 "offers":{"@type":"Offer","price":"49.95","priceCurrency":"EUR"}}
</script></head><body>
<script>window.zara.viewPayload = {"product":{"id":364089536,"detail":{"colors":[
  {"id":"800","productId":364089536,"name":"BLACK","hexCode":"#000000","xmedia":[{"path":"a"}]},
  {"id":"710","productId":364089537,"name":"ECRU","hexCode":"#EEE8DD","xmedia":[{"path":"b"}]},
  {"id":"400","productId":364089538,"name":"NAVY BLUE","hexCode":"#1F2A44","xmedia":[]}
]}},"related":{"colors":[{"id":"001","productId":999,"name":"WHITE"}]}};</script>
</body></html>
"""


def test_zara_lists_every_colour_with_its_own_v1_link() -> None:
    url = "https://www.zara.com/uz/ru/linen-dress-p03067301.html?v1=364089537&utm_source=tg"
    variants = extract_color_variants(ZARA_PAGE, url)
    assert [item.name for item in variants] == ["BLACK", "ECRU", "NAVY BLUE"]
    assert [item.selected for item in variants] == [False, True, False]
    assert variants[0].url == "https://www.zara.com/uz/ru/linen-dress-p03067301.html?v1=364089536"
    assert all(link_names_color(item.url) for item in variants)


def test_bare_zara_link_marks_the_colour_the_page_shows() -> None:
    bare = "https://www.zara.com/uz/ru/linen-dress-p03067301.html"
    variants = extract_color_variants(ZARA_PAGE, bare, current_color="Ecru")
    assert [item.selected for item in variants] == [False, True, False]
    # Without any hint nothing is guessed.
    assert not any(item.selected for item in extract_color_variants(ZARA_PAGE, bare))


def test_parsed_product_carries_its_colourways() -> None:
    product = parse_product_html(ZARA_PAGE, "https://www.zara.com/uz/ru/linen-dress-p03067301.html?v1=364089538")
    assert product is not None
    assert len(product.color_variants) == 3
    assert next(item for item in product.color_variants if item.selected).name == "NAVY BLUE"
    # The colour stays in the id, so two colours are two products.
    assert product.external_id.endswith("-364089538")


def test_hm_colours_are_separate_articles() -> None:
    html = """<script>{"productArticleDetails":{"articleCode":"1352386006","variations":{
      "1352386006":{"name":"Beige","images":[]},
      "1352386001":{"name":"Black","images":[]}}}}</script>"""
    variants = extract_color_variants(html, "https://www2.hm.com/es_es/productpage.1352386006.html")
    assert [(item.name, item.selected) for item in variants] == [("Beige", True), ("Black", False)]
    assert variants[1].url == "https://www2.hm.com/es_es/productpage.1352386001.html"


def test_json_ld_product_group_gives_one_link_per_colour() -> None:
    html = """<script type="application/ld+json">
    {"@type":"ProductGroup","name":"Coat","hasVariant":[
      {"@type":"Product","color":"Camel","size":"S","offers":{"url":"https://shop.example.com/coat?color=camel&size=s"}},
      {"@type":"Product","color":"Camel","size":"M","offers":{"url":"https://shop.example.com/coat?color=camel&size=m"}},
      {"@type":"Product","color":"Grey","size":"S","offers":{"url":"https://shop.example.com/coat?color=grey&size=s"}}
    ]}</script>"""
    variants = extract_color_variants(html, "https://shop.example.com/coat?color=grey")
    assert [item.name for item in variants] == ["Camel", "Grey"]
    assert variants[1].selected


def test_single_colour_page_offers_no_choice() -> None:
    html = '<script>{"colors":[{"id":"800","productId":1,"name":"BLACK"}]}</script>'
    assert extract_color_variants(html, "https://www.zara.com/es/es/x-p01234567.html") == []


def test_mango_colour_links_keep_their_shape() -> None:
    assert mango_color_url("https://shop.mango.com/es/es/p/mujer/slingback/37016751/99", "01") == (
        "https://shop.mango.com/es/es/p/mujer/slingback/37016751/01"
    )
    assert mango_color_url("https://shop.mango.com/gb/en/p/women/dress_87039062?c=99", "05") == (
        "https://shop.mango.com/gb/en/p/women/dress_87039062?c=05"
    )
    assert mango_page_identity("https://shop.mango.com/gb/en/p/women/dress_87039062?c=05") == ("87039062", "05")


def test_canonical_link_never_drops_the_chosen_colour() -> None:
    beige = "https://www.zara.com/uz/ru/linen-dress-p03067301.html?v1=364089537"
    bare = "https://www.zara.com/uz/ru/linen-dress-p03067301.html"
    assert choose_processed_url(beige + "&utm_source=ig", beige, bare) == beige
    # A short link learns its colour from the redirect.
    assert choose_processed_url("https://zara.com/s/abc", beige, bare) == beige
    # Without a colour anywhere the canonical page is fine.
    assert choose_processed_url("https://zara.com/s/abc", bare, bare) == bare


def test_colour_query_keys_tell_colours_apart() -> None:
    for black, beige in (
        ("https://shop.mango.com/gb/en/p/women/dress_87039062?c=99", "https://shop.mango.com/gb/en/p/women/dress_87039062?c=05"),
        ("https://www.bershka.com/es/vestido-c0p123456789.html?colorId=800", "https://www.bershka.com/es/vestido-c0p123456789.html?colorId=710"),
    ):
        assert normalize_url(black) != normalize_url(beige)
        assert not extract_duplicate_signatures("", "", black) & extract_duplicate_signatures("", "", beige)
        assert extract_duplicate_signatures("", "", black) & extract_duplicate_signatures("", "", black + "&utm_source=x")


def test_every_hm_product_angle_is_kept() -> None:
    stills = [f"s{i}" for i in range(5)]
    details = ["d1", "d2"]
    assert _product_angles(stills, details) == ["s0", "s1", "d1", "d2", "s2", "s3", "s4"]


def _dress(back: bool) -> Image.Image:
    image = Image.new("RGB", (800, 1200), (242, 240, 238))
    draw = ImageDraw.Draw(image)
    draw.polygon([(300, 150), (500, 150), (620, 1100), (180, 1100)], fill=(30, 30, 40))
    draw.rectangle((240, 150, 560, 260), fill=(30, 30, 40))
    if back:
        draw.line((400, 170, 400, 600), fill=(90, 90, 100), width=6)
        draw.ellipse((370, 140, 430, 180), fill=(242, 240, 238))
    else:
        draw.polygon([(360, 150), (440, 150), (400, 260)], fill=(242, 240, 238))
        for top in (300, 380, 460):
            draw.ellipse((392, top, 408, top + 16), fill=(200, 180, 120))
    return image


def test_front_and_back_of_one_dress_are_both_posted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        front, copy, smaller, back = (folder / name for name in ("front.jpg", "copy.jpg", "small.jpg", "back.jpg"))
        _dress(False).save(front, quality=92)
        _dress(False).save(copy, quality=75)
        _dress(False).resize((600, 900)).save(smaller, quality=85)
        _dress(True).save(back, quality=92)
        assert unique_images([front, copy, smaller, back]) == [front, back]


def test_admin_text_gets_each_colour_line() -> None:
    text = "Платье-55$\nРазмеры от XS до L.\nЦвет: черный."
    assert swap_color_line(text, "бежевый") == "Платье-55$\nРазмеры от XS до L.\nЦвет: бежевый."
    assert swap_color_line("Платье-55$", "синий") == "Платье-55$\nЦвет: синий."


MANGO_LINK = "https://shop.mango.com/es/es/p/mujer/marroquineria/neceseres/bolso-vanity-acolchado/37066365/75/00"


def test_mango_colours_hidden_in_next_js_chunks() -> None:
    payload = (
        '{\\"colors\\":[{\\"id\\":\\"75\\",\\"label\\":\\"Burdeos\\"},{\\"id\\":\\"99\\",\\"label\\":\\"Negro\\"}]}'
    )
    html = f'<script>self.__next_f.push([1,"7:{payload}\\n"])</script>'
    variants = extract_color_variants(html, MANGO_LINK)
    assert [(item.code, item.name, item.selected) for item in variants] == [
        ("75", "Burdeos", True),
        ("99", "Negro", False),
    ]
    assert variants[1].url.endswith("/37066365/99/00")


def test_mango_colours_keyed_by_code() -> None:
    html = '<script>{"product":{"colors":{"75":{"label":"Burdeos"},"99":{"label":"Negro"}}}}</script>'
    assert [item.code for item in extract_color_variants(html, MANGO_LINK)] == ["75", "99"]


def test_recommendation_colour_list_does_not_win_over_the_product() -> None:
    html = (
        '<script>{"related":{"colors":[{"id":"01","label":"Blanco"},{"id":"05","label":"Gris"}]},'
        '"product":{"colors":[{"id":"75","label":"Burdeos"},{"id":"99","label":"Negro"}]}}</script>'
    )
    assert [item.code for item in extract_color_variants(html, MANGO_LINK)] == ["75", "99"]


def test_colour_switcher_links_are_read_when_there_is_no_json() -> None:
    html = """
    <ul class="colors">
      <li><span aria-current="true">Burdeos</span></li>
      <li><a href="/es/es/p/mujer/marroquineria/neceseres/bolso-vanity-acolchado/37066365/99/00" aria-label="Color: Negro"><span></span></a></li>
      <li><a href="/es/es/p/mujer/otro/12345678/99/00" aria-label="Negro">other product</a></li>
    </ul>"""
    variants = extract_color_variants(html, MANGO_LINK, current_color="Burdeos")
    assert [(item.code, item.name, item.selected) for item in variants] == [
        ("75", "Burdeos", True),
        ("99", "Negro", False),
    ]


def test_mango_photo_names_are_the_last_resort() -> None:
    html = (
        '<img src="https://media.mango.com/is/image/punto/37066365-75-01?wid=600">'
        '<img src="https://media.mango.com/is/image/punto/37066365-75-02?wid=600">'
        '<img src="https://media.mango.com/is/image/punto/37066365-99-99?wid=40">'
        '<img src="https://media.mango.com/is/image/punto/27099999-01-01?wid=600">'
    )
    variants = extract_color_variants(html, MANGO_LINK, current_color="Burdeos")
    assert [(item.code, item.name, item.selected) for item in variants] == [
        ("75", "Burdeos", True),
        ("99", "Цвет 99", False),
    ]


def test_sizes_hidden_in_next_js_chunks_are_read() -> None:
    from core.product_facts import extract_site_facts

    payload = (
        '{\\"colors\\":[{\\"id\\":\\"75\\",\\"label\\":\\"Burdeos\\",\\"sizes\\":[{\\"label\\":\\"S\\"},{\\"label\\":\\"M\\"}]},'
        '{\\"id\\":\\"99\\",\\"label\\":\\"Negro\\",\\"sizes\\":[{\\"label\\":\\"S\\"}]}]}'
    )
    html = f'<script>self.__next_f.push([1,"7:{payload}\\n"])</script>'
    color, sizes = extract_site_facts(html, "https://shop.mango.com/es/es/p/mujer/vestido_87039062?c=75")
    assert color == "Burdeos"
    assert sizes == ("S", "M")


def test_inspect_url_reports_colours_and_saves_the_page(tmp_path, monkeypatch) -> None:
    from core.diagnostics import inspect_product_url

    html = ZARA_PAGE + " " * 100
    monkeypatch.setattr(
        "adapters.product_page.fetch_product_html",
        lambda url, timeout_seconds=20.0: (url, html),
    )
    report = inspect_product_url("https://www.zara.com/uz/ru/linen-dress-p03067301.html?v1=364089537", save_dir=tmp_path)
    assert "Цветов: 3" in report
    assert "ECRU [364089537]" in report and "← по ссылке" in report
    assert len(list(tmp_path.glob("*.html"))) == 1
