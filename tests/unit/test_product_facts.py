"""Color and the full size grid must come from the product page."""

from decimal import Decimal

from adapters.base import RawProduct
from adapters.product_page import parse_product_html
from core.product_facts import (
    attach_site_facts,
    extract_heel_height,
    extract_site_facts,
    site_description,
)


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
    assert "Цвет: светло-бежевый." in text
    assert "XS до XL" not in text


def test_zara_uses_the_color_id_from_the_url_and_keeps_sold_out_sizes() -> None:
    url = "https://www.zara.com/es/es/wool-coat-p01234567.html?v1=321000111"
    color, sizes = extract_site_facts(ZARA_HTML, url)
    assert color == "Ecru"
    assert sizes == ("XS", "S", "M", "L", "XL")
    assert site_description(color, sizes) == "Размеры от XS до XL.\nЦвет: экрю."


def test_the_shade_the_store_prints_is_said_in_russian() -> None:
    """Stores name colours in their own language; the caption is Russian."""
    from core.color_names import russian_color

    assert russian_color("Marron") == "коричневый"
    assert russian_color("Marrón") == "коричневый"
    assert russian_color("Bleu marine") == "темно-синий"
    assert russian_color("Siyah") == "черный"
    assert russian_color("OFF WHITE") == "молочный"
    # «Light» and «dark» are read off a known base shade.
    assert russian_color("Gris clair") == "светло-серый"
    assert russian_color("Dark Green") == "темно-зеленый"
    # Already Russian, and unknown wording, are both left as the page said.
    assert russian_color("Темно-синий") == "темно-синий"
    assert russian_color("Atlantic Haze") == "Atlantic Haze"
    assert russian_color("") == ""


def test_compound_color_decomposition() -> None:
    """Multi-word shades are decomposed and translated with proper hyphenated Russian grammar."""
    from core.color_names import russian_color

    assert russian_color("Sand Brown") == "песочно-коричневый"
    assert russian_color("sand-brown") == "песочно-коричневый"
    assert russian_color("Olive Green") == "оливково-зеленый"
    assert russian_color("Grey Blue") == "серо-синий"
    assert russian_color("Dusty Pink") == "пыльно-розовый"
    assert russian_color("Smoky Grey") == "дымчато-серый"


def test_expanded_creative_color_shades() -> None:
    """Creative brand shades from European and Turkish stores are mapped to Russian."""
    from core.color_names import russian_color

    assert russian_color("Lagoon") == "морская волна"
    assert russian_color("LAGOON") == "морская волна"
    assert russian_color("petrol") == "петроль"
    assert russian_color("cognac") == "коньячный"
    assert russian_color("sage") == "шалфейный"
    assert russian_color("rust") == "терракотовый"
    assert russian_color("butter") == "сливочный"
    assert russian_color("lime") == "лаймовый"
    assert russian_color("cappuccino") == "капучино"
    assert russian_color("cinnamon") == "коричный"
    assert russian_color("peach") == "персиковый"


def test_color_cache_and_ai_fallback(tmp_path, monkeypatch) -> None:
    """Unknown shades cached on disk or translated by LLM are resolved in Russian."""
    from core import color_names
    from core.color_names import russian_color, save_color_translation

    # Direct cache save & retrieval
    save_color_translation("Midnight Sky", "полуночно-синий")
    assert russian_color("Midnight Sky") == "полуночно-синий"
    assert russian_color("midnight sky") == "полуночно-синий"

    # Pipeline runner LLM fallback resolution
    from unittest.mock import MagicMock
    from core.pipeline import PipelineRunner

    mock_llm = MagicMock()
    mock_llm.translate_color.return_value = "туманный беж"

    runner = object.__new__(PipelineRunner)
    runner.llm = mock_llm

    resolved = runner._resolve_color("Misty Beige")
    assert resolved == "туманный беж"
    mock_llm.translate_color.assert_called_once_with("Misty Beige")

    # Already translated or Cyrillic colors do not invoke LLM
    assert runner._resolve_color("черный") == "черный"
    assert runner._resolve_color("Sand Brown") == "песочно-коричневый"
    assert mock_llm.translate_color.call_count == 1


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
    assert site_description(color, sizes) == "Размеры от XSS до 7XL.\nЦвет: молочный."


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


def _shoe_page(name: str, body: str, extra_json: str = "") -> str:
    extra = f",{extra_json}" if extra_json else ""
    return f"""
    <html><head>
    <script type="application/ld+json">
    {{"@type":"Product","name":"{name}",
     "image":["https://static.zara.net/photos/shoe.jpg"],
     "offers":{{"price":"69.95","priceCurrency":"EUR"}}{extra}}}
    </script>
    </head><body>{body}</body></html>
    """


def test_heel_height_is_copied_from_the_visible_page_label() -> None:
    html = _shoe_page("Leather slingback shoes", "<p>Heel height: 9 cm</p>")
    assert extract_heel_height(html, "Leather slingback shoes") == "9 см"
    product = parse_product_html(html, "https://www.zara.com/es/es/leather-slingback-shoes-p12345678.html")
    assert product is not None
    assert product.heel_height == "9 см"
    assert site_description(product.color, product.sizes, product.heel_height) == "Высота каблука: 8 см."


def test_heel_caption_line_is_one_cm_less_than_website() -> None:
    from core.product_facts import heel_caption_line

    assert heel_caption_line("9 cm") == "Высота каблука: 8 см."
    assert heel_caption_line("9 см") == "Высота каблука: 8 см."
    assert heel_caption_line("8.5 cm") == "Высота каблука: 7.5 см."
    assert heel_caption_line("7,5 cm") == "Высота каблука: 6,5 см."
    assert heel_caption_line("90 mm") == "Высота каблука: 80 мм."
    assert heel_caption_line("1 cm") is None
    assert heel_caption_line("10 mm") is None
    assert heel_caption_line(None) is None


def test_heel_height_is_read_from_product_json_and_keeps_the_site_number() -> None:
    html = _shoe_page(
        "Leather slingback shoes",
        "",
        '"attributes":[{"name":"Heel height","value":"7,5 cm"}]',
    )
    assert extract_heel_height(html, "Leather slingback shoes") == "7,5 см"


def test_heel_height_split_across_tags_is_still_the_site_value() -> None:
    html = _shoe_page("Ankle boots", "<span>Altura del tacón</span><span>8 cm</span>")
    assert extract_heel_height(html, "Ankle boots") == "8 см"


def test_heel_height_without_a_unit_or_with_two_values_is_left_blank() -> None:
    bare = _shoe_page("Leather shoes", "", '"heelHeight":"90"')
    assert extract_heel_height(bare, "Leather shoes") is None
    mixed = _shoe_page("Leather shoes", "<p>Heel height: 9 cm</p><p>Heel height: 5 cm</p>")
    assert extract_heel_height(mixed, "Leather shoes") is None
    assert site_description("Black", ("36", "41"), None) == "Размеры от 36 до 41.\nЦвет: черный."


def test_heel_height_is_not_taken_from_a_dress_or_from_sneakers() -> None:
    dress = _shoe_page("Silk dress", "<p>Heel height: 9 cm</p>")
    product = parse_product_html(dress, "https://www.zara.com/es/es/silk-dress-p12345678.html")
    assert product is not None
    assert product.heel_height is None

    sneakers = _shoe_page("Running sneakers", "<p>Heel height: 4 cm</p>")
    product = parse_product_html(sneakers, "https://www.zara.com/es/es/running-sneakers-p12345678.html")
    assert product is not None
    assert product.heel_height is None


def test_heel_height_follows_this_product_not_a_recommendation() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"Leather slingback shoes",
     "image":["https://static.zara.net/photos/shoe.jpg"],
     "offers":{"price":"69.95","priceCurrency":"EUR"},
     "detail":{"extraInfo":[{"name":"Heel height","value":"9 cm"}]},
     "recommendations":[{"name":"Other sandal","extraInfo":[{"name":"Heel height","value":"5 cm"}]}]}
    </script>
    </head></html>
    """
    assert extract_heel_height(html, "Leather slingback shoes") == "9 см"


def test_heel_height_labels_in_other_languages_keep_the_printed_number() -> None:
    spanish = _shoe_page("Zapatos de tacón", "<p>Zapatos de tacón 8 cm</p>")
    assert extract_heel_height(spanish, "Zapatos de tacón") == "8 см"
    turkish = _shoe_page("Topuklu ayakkabı", "<p>Topuk boyu: 8,5 cm</p>")
    assert extract_heel_height(turkish, "Topuklu ayakkabı") == "8,5 см"
    millimetres = _shoe_page("Leather heels", "<p>Heel height: 90 mm</p>")
    assert extract_heel_height(millimetres, "Leather heels") == "90 мм"


def test_attach_copies_facts_from_the_product_page(monkeypatch) -> None:
    def fake(url: str, timeout_seconds: float) -> tuple[str, str]:
        return url, MANGO_HTML

    monkeypatch.setattr("adapters.product_page.fetch_product_html", fake)
    product = attach_site_facts(_product("https://shop.mango.com/es/es/p/mujer/cardigan_87051234?c=70"))
    assert product.color == "Light beige"
    assert product.sizes[0] == "XSS"
    assert product.sizes[-1] == "7XL"


def test_a_store_that_blocks_plain_requests_is_opened_in_chrome(monkeypatch) -> None:
    """Zara answers a plain request with a script shell, so the size grid needs Chrome."""
    import adapters.product_page as product_page

    monkeypatch.setattr(product_page, "_page_cache", None)
    monkeypatch.setattr(
        product_page,
        "_fetch_html_fast",
        lambda url, timeout_seconds: (url, "<html><body><script>app()</script></body></html>"),
    )
    opened: list[str] = []

    def chrome(url: str, timeout_seconds: float) -> tuple[str, str]:
        opened.append(url)
        return MANGO_HTML + " " * 20000, url

    monkeypatch.setattr(product_page, "_fetch_html_browser", chrome)

    url = "https://www.zara.com/fr/fr/veste-100-laine-extra-douce-p09598007.html"
    product = attach_site_facts(_product(url))

    assert opened == [url]
    assert product.sizes[0] == "XSS"


def test_the_page_is_read_once_for_sizes_and_for_the_gallery(monkeypatch) -> None:
    """One post must not open the same product page in Chrome twice."""
    import adapters.product_page as product_page

    monkeypatch.setattr(product_page, "_page_cache", None)
    monkeypatch.setattr(product_page, "_fetch_html_fast", lambda url, timeout_seconds: (url, ""))
    calls: list[str] = []

    def chrome(url: str, timeout_seconds: float) -> tuple[str, str]:
        calls.append(url)
        return MANGO_HTML + " " * 20000, url

    monkeypatch.setattr(product_page, "_fetch_html_browser", chrome)

    url = "https://www.zara.com/fr/fr/veste-p09598007.html"
    first = product_page.fetch_product_html(url)
    second = product_page.fetch_product_html(url)

    assert calls == [url]
    assert first == second


# Zara's newer locales ship no JSON-LD and leave the amount out of their meta
# tags: the price is only in the block the shopper reads.
SHELF_PRICE_HTML = """
<html><head>
  <meta property="og:title" content="BASIC KNIT JUMPER">
  <meta property="og:image" content="https://static.zara.net/photos/08851172704-p.jpg">
</head><body>
  <div class="product-detail-info__price">
    <span class="price-current price__amount" data-qa-id="price-container-current">
      <data data-currency="EUR" value="25.95"><span>25.95 EUR</span></data>
    </span>
  </div>
  <p class="product-color-extended-name">
    <span class="product-color-extended-name__name">camel</span>
  </p>
</body></html>
"""


def test_a_page_without_structured_data_still_gives_up_its_price() -> None:
    product = parse_product_html(
        SHELF_PRICE_HTML,
        "https://www.zara.com/de/en/basic-knit-jumper-p08851172.html",
    )

    assert product is not None
    assert product.title == "BASIC KNIT JUMPER"
    assert product.price == Decimal("25.95")
    assert product.currency == "EUR"


def test_the_caption_says_the_shade_the_shopper_reads_not_the_swatch_hex() -> None:
    """A storefront names the shade twice: as a word and as the hex it paints."""
    html = SHELF_PRICE_HTML.replace(
        "<body>",
        '<body><script type="application/json">'
        '{"color":{"name":"ffffff"},"sizes":[{"label":"S"},{"label":"M"}]}</script>',
    )

    color, _sizes = extract_site_facts(html, "https://www.zara.com/de/en/jumper-p08851172.html")

    assert color == "camel"


def test_shopify_productgroup_variants_and_dom_inputs() -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@type": "ProductGroup",
        "name": "Context Black Heeled Sock Boots",
        "image": "https://www.linzi.com/cdn/shop/files/CONTEXTBLACK.jpg",
        "description": "Heel Height: 8cm",
        "hasVariant": [
          {"@type": "Product", "name": "Context Black Heeled Sock Boots - 3", "sku": "CONTEXT-3", "offers": {"price": "69.00", "priceCurrency": "EUR"}},
          {"@type": "Product", "name": "Context Black Heeled Sock Boots - 4", "sku": "CONTEXT-4", "offers": {"price": "69.00", "priceCurrency": "EUR"}},
          {"@type": "Product", "name": "Context Black Heeled Sock Boots - 5", "sku": "CONTEXT-5", "offers": {"price": "69.00", "priceCurrency": "EUR"}}
        ]
      }
      </script>
    </head><body>
      <input type="hidden" name="properties[Colour]" value="Black">
      <input type="radio" name="Size-1" value="3">
      <input type="radio" name="Size-1" value="4">
      <input type="radio" name="Size-1" value="5">
    </body></html>
    """
    url = "https://www.linzi.com/en-de/products/context-black"
    color, sizes = extract_site_facts(html, url)
    assert color == "Black"
    assert sizes == ("3", "4", "5")
    product = parse_product_html(html, url)
    assert product is not None
    assert product.title == "Context Black Heeled Sock Boots"
    assert product.price == Decimal("69.00")
    assert product.currency == "EUR"
    assert product.color == "Black"
    assert product.sizes == ("3", "4", "5")
    assert product.heel_height == "8 см"
    desc = site_description(product.color, product.sizes, heel_height=product.heel_height)
    assert "Размеры от 3 до 5." in desc
    assert "Цвет: черный." in desc
    assert "Высота каблука: 7 см." in desc
