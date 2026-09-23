"""Unit tests for Playwright scrapers and utility functions.

Tests HTML parsing, price extraction, image resolution, and JSON-LD schema handling.
"""

from decimal import Decimal
import pytest
from playwright.sync_api import sync_playwright

from adapters.scrapers.base import extract_best_image_url, parse_price
from adapters.scrapers.generic import GenericFashionScraper
from adapters.scrapers.mango import MangoScraper
from adapters.scrapers.zara import ZaraScraper


def test_parse_price_formats() -> None:
    """Validate price parsing across different currency symbols and decimal formats."""
    # European comma format with euro symbol
    price, currency = parse_price("89,90 €")
    assert price == Decimal("89.90")
    assert currency == "EUR"

    # US dollar format
    price, currency = parse_price("$129.00")
    assert price == Decimal("129.00")
    assert currency == "USD"

    # Suffix code
    price, currency = parse_price("149.99 EUR")
    assert price == Decimal("149.99")
    assert currency == "EUR"

    # British Pound
    price, currency = parse_price("£59.99")
    assert price == Decimal("59.99")
    assert currency == "GBP"

    # Turkish Lira
    price, currency = parse_price("1.499,90 TL")
    assert price == Decimal("1499.90")
    assert currency == "TRY"

    # Plain decimal with default currency
    price, currency = parse_price("49.95", default_currency="USD")
    assert price == Decimal("49.95")
    assert currency == "USD"


def test_parse_price_invalid() -> None:
    """Invalid price strings raise ValueError."""
    with pytest.raises(ValueError):
        parse_price("")

    with pytest.raises(ValueError):
        parse_price("No price available")


def test_extract_best_image_url() -> None:
    """Extract highest resolution URL from srcset or fallback to src."""
    srcset = (
        "https://static.zara.net/photo/400.jpg 400w, "
        "https://static.zara.net/photo/800.jpg 800w, "
        "https://static.zara.net/photo/1200.jpg 1200w"
    )
    best = extract_best_image_url("https://static.zara.net/photo/400.jpg", srcset=srcset)
    assert best == "https://static.zara.net/photo/1200.jpg"

    # Protocol relative
    proto_rel = extract_best_image_url("//images.mango.com/item.jpg")
    assert proto_rel == "https://images.mango.com/item.jpg"

    # Relative path with base_url
    rel = extract_best_image_url("/catalog/img1.jpg", base_url="https://shop.mango.com")
    assert rel == "https://shop.mango.com/catalog/img1.jpg"


def test_zara_scraper_html_extraction() -> None:
    """Test ZaraScraper parsing against realistic Zara HTML structure."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <body>
        <ul class="product-grid__product-list">
            <li class="product-grid-product" data-product-id="10101">
                <a class="product-link" href="/es/en/pleated-satin-dress-p010101.html">
                    <div class="product-grid-product-info">
                        <h2 class="product-grid-product-info__name">Pleated Satin Midi Dress</h2>
                        <span class="money-amount__main">89,90 €</span>
                    </div>
                </a>
                <img class="media-image__image" src="https://static.zara.net/dress-800.jpg" alt="Pleated Satin Midi Dress" />
            </li>
            <li class="product-grid-product" data-product-id="20202">
                <a class="product-link" href="/es/en/tailored-wool-blazer-p020202.html">
                    <div class="product-grid-product-info">
                        <h2 class="product-grid-product-info__name">Tailored Wool Blazer</h2>
                        <span class="money-amount__main">129,00 €</span>
                        <span>Out of stock</span>
                    </div>
                </a>
                <img class="media-image__image" src="https://static.zara.net/blazer-800.jpg" alt="Tailored Wool Blazer" />
            </li>
        </ul>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)

        scraper = ZaraScraper()
        # Mock navigate step by directly extracting from loaded DOM
        elements = [page.locator("li.product-grid-product").nth(i) for i in range(2)]
        products = []
        for el in elements:
            data = scraper._extract_element_data(el, base_url="https://www.zara.com", default_currency="EUR")
            if data:
                products.append(data)

        browser.close()

    assert len(products) == 2
    item1 = products[0]
    assert item1["id"] == "zara-10101"
    assert item1["name"] == "Pleated Satin Midi Dress"
    assert item1["price"] == Decimal("89.90")
    assert item1["currency"] == "EUR"
    assert item1["image"] == "https://static.zara.net/dress-800.jpg"
    assert item1["url"] == "https://www.zara.com/es/en/pleated-satin-dress-p010101.html"
    assert item1["available"] is True

    item2 = products[1]
    assert item2["id"] == "zara-20202"
    assert item2["available"] is False


def test_mango_scraper_html_extraction() -> None:
    """Test MangoScraper parsing against realistic Mango HTML structure."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <body>
        <div class="product-list">
            <li data-testid="plp.product.card" data-product-id="67010203">
                <a data-testid="product.link" href="/es/en/p/women/clothing/coats/belted-trench-coat_67010203.html">
                    <h3 data-testid="product.title" class="text-title-m">Belted Trench Coat</h3>
                    <span data-testid="product.price" class="current-price">149,99 €</span>
                </a>
                <img data-testid="product.image" src="https://st.mngbcn.com/trench.jpg" alt="Belted Trench Coat" />
            </li>
        </div>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)

        scraper = MangoScraper()
        el = page.locator("li[data-testid='plp.product.card']").first
        data = scraper._extract_element_data(el, base_url="https://shop.mango.com", default_currency="EUR")

        browser.close()

    assert data is not None
    assert data["id"] == "mango-67010203"
    assert data["name"] == "Belted Trench Coat"
    assert data["price"] == Decimal("149.99")
    assert data["currency"] == "EUR"
    assert data["image"] == "https://st.mngbcn.com/trench.jpg"
    assert data["url"] == "https://shop.mango.com/es/en/p/women/clothing/coats/belted-trench-coat_67010203.html"
    assert data["available"] is True


def test_generic_json_ld_extraction() -> None:
    """Test GenericFashionScraper extracting Schema.org JSON-LD."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "Product",
                    "sku": "GEN-999",
                    "name": "Classic Linen Shirt",
                    "image": "https://example.com/shirt.jpg",
                    "offers": {
                        "@type": "Offer",
                        "price": "65.00",
                        "priceCurrency": "USD",
                        "availability": "https://schema.org/InStock",
                        "url": "https://example.com/products/classic-linen-shirt"
                    }
                }
            ]
        }
        </script>
    </head>
    <body>
        <h1>Store Products</h1>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)

        scraper = GenericFashionScraper(brand_name="linenstore")
        products = scraper._extract_json_ld(page, base_url="https://example.com", max_items=5, default_currency="USD")

        browser.close()

    assert len(products) == 1
    item = products[0]
    assert item["id"] == "linenstore-GEN-999"
    assert item["name"] == "Classic Linen Shirt"
    assert item["price"] == Decimal("65.00")
    assert item["currency"] == "USD"
    assert item["image"] == "https://example.com/shirt.jpg"
    assert item["available"] is True
