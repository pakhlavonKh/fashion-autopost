"""Regression: Bershka slouchy ankle boots (c0p230420706, colour 131).

The post went out with a made-up colour, no heel height and without the model
photo. The HTML below keeps the parts of the real page that caused it.
"""

from adapters.base import RawProduct
from core.gallery import inditex_pdp_gallery, keep_single_product, ordered_photos, page_gallery_is_authoritative
from core.product_facts import extract_heel_height, extract_site_facts, heel_caption_line, is_heeled_footwear

URL = "https://www.bershka.com/de/slouchy-fit-stiefeletten-mit-absatz-und-schnalle-c0p230420706.html?colorId=131"
TITLE = "Slouchy-Fit-Stiefeletten mit Absatz und Schnalle"
BASE = "https://static.bershka.net/assets/public/aa/bb/cc/dd"
SHOTS = [
    f"{BASE}/11150864131-a4o/11150864131-a4o.jpg",
    f"{BASE}/11150865131-b1o/11150865131-b1o.jpg",  # model shot under a sibling reference
    f"{BASE}/11150864131-a1t/11150864131-a1t.jpg",
    f"{BASE}/11150864131-a3o/11150864131-a3o.jpg",
    f"{BASE}/11150864131-a2d/11150864131-a2d.jpg",
]


def _gallery_html() -> str:
    tags = "".join(
        f'<img data-qa-anchor="pdpMainImage" src="{url}?ts=1&amp;w=850" '
        f'data-original="{url}?ts=1&amp;w=850" alt="{TITLE}-Taupe">'
        for url in SHOTS * 2  # the carousel repeats its slides
    )
    return f"<div class=\"product-gallery\">{tags}</div>"


HTML = (
    "<html><body>"
    + _gallery_html()
    + '<div class="detail-info__color-section"><div class="color-selector">'
    '<div id="color-selector__info" class="color-selector__info">'
    '<span id="color-selector__name" class="color-selector__name bds-typography-label-xs-highlight">'
    '<span class="color-selector__sr-only">Farbe</span>\n      Taupe\n    </span> '
    '<span class="color-selector__reference bds-typography-label-xs">\n      Ref. 1150/864/131\n    </span>'
    "</div></div></div>"
    '<p class="long-article__text bds-typography-paragraph-s">'
    "Absatzhöhe: 4 cm. Schafthöhe: 18 cm. Schaftweite: 20 cm.</p>"
    '<img src="https://static.bershka.net/assets/public/x/y/z/w/iamlasss_DESKW40.jpg">'
    "</body></html>"
)


def test_colour_is_the_shade_not_the_reference_number():
    color, _ = extract_site_facts(HTML, URL)
    assert color == "Taupe"


def test_reference_number_alone_is_not_a_colour():
    html = '<span class="color-selector__reference bds-typography-label-xs">Ref. 1150/864/131</span>'
    color, _ = extract_site_facts(html, URL)
    assert color is None


def test_hashed_colour_label_class_still_matches():
    html = '<span class="ColorsSelector-module__F5Cauq__label">Midnight Sky</span>'
    color, _ = extract_site_facts(html, "https://example.com/p")
    assert color == "Midnight Sky"


def test_german_boots_get_their_heel_height():
    assert is_heeled_footwear(TITLE, URL)
    heel = extract_heel_height(HTML, TITLE)
    assert heel == "4 см"
    assert heel_caption_line(heel) == "Высота каблука: 3 см."


def test_german_footwear_words_do_not_catch_gloves():
    assert not is_heeled_footwear("Handschuhe aus Leder", "https://www.bershka.com/de/handschuhe-c0p1.html")


def test_main_gallery_keeps_the_model_shot_in_page_order():
    gallery = inditex_pdp_gallery(HTML)
    assert gallery == SHOTS
    assert page_gallery_is_authoritative(HTML, "bershka", URL)
    ordered = ordered_photos(HTML, "bershka", URL, [SHOTS[0]])
    assert ordered == SHOTS


def test_without_main_gallery_marker_the_reference_filter_still_applies():
    html = "".join(f'<img src="{url}">' for url in SHOTS)
    assert not page_gallery_is_authoritative(html, "bershka", URL)
    assert SHOTS[1] not in ordered_photos(html, "bershka", URL, [SHOTS[0]])
    assert SHOTS[1] not in keep_single_product(SHOTS, URL, anchor_url=SHOTS[0])


def test_raw_product_defaults_to_unverified_photos():
    product = RawProduct("id", "bershka", TITLE, 1, "EUR", SHOTS[0], URL, True)
    assert product.photos_verified is False
