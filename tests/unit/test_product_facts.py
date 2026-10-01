"""Color and the full size grid must come from the product page."""

from decimal import Decimal

from adapters.base import RawProduct
from adapters.product_page import parse_product_html
from core.product_facts import attach_site_facts, extract_site_facts, site_description


MANGO_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Knitted cardigan",
 "image":["https://media.mango.com/is/image/punto/cardigan.jpg"],
 "offers":{"@type":"Offer","price":"49.99","priceCurrency":"EUR"}}
</script>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"product":{"name":"Knitted cardigan","colors":[
  {"id":"99","label":"Black","sizes":[
    {"label":"S","available":true},{"label":"M","available":true}
  ]},
  {"id":"70","label":"Light beige","selected":true,"sizes":[
    {"label":"7XL","available":false},
    {"label":"M","available":true},
    {"label":"XSS","available":true},
    {"label":"XS","available":true},
    {"label":"S","available":false},
    {"label":"L","available":true},
    {"label":"XL","available":true},
    {"label":"XXL","available":true},
    {"label":"3XL","available":true},
    {"label":"4XL","available":true},
    {"label":"5XL","available":true},
    {"label":"6XL","available":true}
  ]}
]}}}}
</script>
</head></html>
"""

ZARA_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Wool coat","sku":"12345678",
 "image":["https://static.zara.net/photos/coat.jpg"],
 "offers":{"@type":"Offer","price":"89.95","priceCurrency":"EUR",
           "availability":"https://schema.org/InStock"}}
</script>
<script>window.zara={"product":{"detail":{"colors":[
  {"id":"800","name":"Ecru","productId":"321000111","sizes":[
    {"name":"XL","availability":"out_of_stock"},
    {"name":"XS","availability":"in_stock"},
    {"name":"S","availability":"in_stock"},
    {"name":"M","availability":"in_stock"},
    {"name":"L","availability":"in_stock"}
  ]},
  {"id":"251","name":"Black","productId":"321000222","sizes":[
    {"name":"S"},{"name":"M"}
  ]}
]}}}</script>
</head></html>
"""


def test_mango_color_and_full_size_grid_come_from_the_page() -> None:
    url = "https://shop.mango.com/es/es/p/mujer/cardigan_87051234?c=70"
    color, sizes = extract_site_facts(MANGO_HTML, url)
    assert color == "Light beige"
    assert sizes[0] == "XSS"
    assert sizes[-1] == "7XL"
    assert "S" in sizes
    text = site_description(color, sizes)
    assert text.startswith("Размеры от XSS до 7XL.")
    assert "Цвет: Light beige." in text
    assert "молочный" not in text
    assert "XS до XL" not in text


def test_zara_uses_the_color_id_from_the_url_and_keeps_sold_out_sizes() -> None:
    url = "https://www.zara.com/es/es/wool-coat-p01234567.html?v1=321000111"
    color, sizes = extract_site_facts(ZARA_HTML, url)
    assert color == "Ecru"
    assert sizes == ("XS", "S", "M", "L", "XL")
    assert site_description(color, sizes) == "Размеры от XS до XL.\nЦвет: Ecru."


def test_missing_facts_are_left_blank() -> None:
    html = "<html><title>Coat</title></html>"
    assert extract_site_facts(html, "https://shop.mango.com/p/1") == (None, ())
    assert site_description(None, ()) == ""


def test_parse_product_html_keeps_the_site_color_and_sizes() -> None:
    product = parse_product_html(
        MANGO_HTML,
        "https://shop.mango.com/es/es/p/mujer/cardigan_87051234?c=70",
    )
    assert product is not None
    assert product.color == "Light beige"
    assert product.sizes[0] == "XSS"
    assert product.sizes[-1] == "7XL"


def test_size_buttons_are_read_when_json_has_no_grid() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"Linen shirt","color":"Crudo",
     "image":["https://media.mango.com/is/image/punto/1.jpg"],
     "offers":{"price":"39.99","priceCurrency":"EUR"}}
    </script>
    </head><body>
      <button class="size-selector__item">XSS</button>
      <button class="size-selector__item">M</button>
      <button class="size-selector__item">7XL</button>
    </body></html>
    """
    color, sizes = extract_site_facts(html, "https://shop.mango.com/es/p/shirt")
    assert color == "Crudo"
    assert sizes == ("XSS", "M", "7XL")
    assert site_description(color, sizes) == "Размеры от XSS до 7XL.\nЦвет: Crudo."


def _product(url: str) -> RawProduct:
    return RawProduct(
        external_id="sku-1",
        source="mango",
        title="Knitted cardigan",
        price=Decimal("49.99"),
        currency="EUR",
        photo_url="https://media.mango.com/is/image/punto/cardigan.jpg",
        product_url=url,
        in_stock=True,
    )


def test_placeholder_links_are_not_opened(monkeypatch) -> None:
    def boom(url: str, timeout_seconds: float) -> tuple[str, str]:
        raise AssertionError(url)

    monkeypatch.setattr("adapters.product_page._fetch_html_fast", boom)
    product = _product("https://zara.com/p1")
    assert attach_site_facts(product) is product


def test_attach_copies_facts_from_the_product_page(monkeypatch) -> None:
    def fake(url: str, timeout_seconds: float) -> tuple[str, str]:
        return url, MANGO_HTML

    monkeypatch.setattr("adapters.product_page._fetch_html_fast", fake)
    product = attach_site_facts(_product("https://shop.mango.com/es/es/p/mujer/cardigan_87051234?c=70"))
    assert product.color == "Light beige"
    assert product.sizes[0] == "XSS"
    assert product.sizes[-1] == "7XL"
