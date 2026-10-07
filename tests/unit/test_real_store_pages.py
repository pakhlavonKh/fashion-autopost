"""Product pages exactly as the stores served them (tests/fixtures/pages, fetched 2026-10-07).

Synthetic HTML is how earlier fixes passed their tests and still failed on the
real sites. These pages pin what the bot must read from them: every colour, the
colour the link opens, its own name and sizes, and the complete photo gallery of
that colour, nothing from other colours or other products.
"""

import gzip
from pathlib import Path

import pytest

from adapters.product_page import parse_product_html

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"


def _page(name: str) -> str:
    return gzip.decompress((PAGES / name).read_bytes()).decode("utf-8")


def _names(urls: list[str]) -> list[str]:
    return [url.rsplit("/", 1)[-1] for url in urls]


MANGO = "https://shop.mango.com/es/es/p/mujer/marroquineria/neceseres/bolso-vanity-acolchado/37066365"


@pytest.mark.parametrize(
    ("colour", "name"),
    [("75", "Cereza"), ("99", "Negro")],
)
def test_mango_vanity_bag_each_colour_gets_its_own_name_and_photos(colour: str, name: str) -> None:
    product = parse_product_html(_page("mango-37066365-75.html.gz"), f"{MANGO}/{colour}/00")
    assert product is not None
    assert product.color == name
    assert product.sizes == ("Talla única",)
    assert _names(product.photo_urls) == [
        f"37066365-{colour}-052",
        f"37066365-{colour}-053",
        f"37066365-{colour}-054",
        f"37066365-{colour}-900",
    ]
    assert [(item.code, item.name) for item in product.color_variants] == [("99", "Negro"), ("75", "Cereza")]
    assert [item.selected for item in product.color_variants] == [colour == "99", colour == "75"]
    assert product.external_id == f"mango-37066365-{colour}"


ZARA_TEE = "https://www.zara.com/es/es/camiseta-pique-cuello-perkins-p05067177.html"
DARK = ["05067177716-p.jpg", "05067177716-a1.jpg", "05067177716-a2.jpg", "05067177716-a3.jpg",
        "05067177716-e1.jpg", "05067177716-e2.jpg", "05067177716-e3.jpg"]
LIGHT = ["05067177730-p.jpg", "05067177730-a1.jpg", "05067177730-a2.jpg",
         "05067177730-e1.jpg", "05067177730-e2.jpg", "05067177730-e3.jpg"]


def test_zara_default_colour_posts_its_whole_gallery() -> None:
    product = parse_product_html(_page("zara-05067177.html.gz"), ZARA_TEE)
    assert product is not None
    assert product.color == "Marrón oscuro"
    assert product.sizes == ("S", "M", "L")
    # The cart thumbnail (-f1) and the colour-switcher picture are not gallery photos.
    assert _names(product.photo_urls) == DARK
    assert [(item.name, item.selected) for item in product.color_variants] == [
        ("Marrón oscuro", True),
        ("Marrón claro", False),
    ]


@pytest.mark.parametrize("page", ["zara-05067177-v1-545436419.html.gz", "zara-05067177.html.gz"])
def test_zara_second_colour_link_posts_that_colour(page: str) -> None:
    # Whether the store rendered the page for that colour or for its default one.
    product = parse_product_html(_page(page), f"{ZARA_TEE}?v1=545436419")
    assert product is not None
    assert product.color == "Marrón claro"
    assert _names(product.photo_urls) == LIGHT
    assert [item.name for item in product.color_variants if item.selected] == ["Marrón claro"]
    assert product.external_id.endswith("-545436419")


def test_zara_single_colour_shoe_has_no_colour_choice_and_all_photos() -> None:
    product = parse_product_html(_page("zara-12548810.html.gz"), "https://www.zara.com/es/es/bailarina-estampada-p12548810.html")
    assert product is not None
    assert product.color == "Leopardo"
    assert product.sizes == ("35", "36", "37", "38", "39", "40", "41", "42")
    assert _names(product.photo_urls) == [
        "12548810195-p.jpg",
        "12548810195-e1.jpg",
        "12548810195-e2.jpg",
        "12548810195-e3.jpg",
        "12548810195-e4.jpg",
    ]
    assert product.color_variants == ()
