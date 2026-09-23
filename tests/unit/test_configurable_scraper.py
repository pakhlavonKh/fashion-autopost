"""Unit tests for ConfigurableStoreScraper.

Tests zero-code onboarding for arbitrary new websites with:
1. Declarative CSS selectors.
2. Schema.org JSON-LD auto-detection.
3. Semantic card auto-detection.
"""

from decimal import Decimal
from playwright.sync_api import sync_playwright

from adapters.scrapers.configurable import ConfigurableStoreScraper
from config.app_config import ScraperStoreConfig, StoreSelectorConfig


def test_configurable_scraper_with_custom_selectors() -> None:
    """Admin configures custom CSS selectors for an arbitrary boutique."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <body>
        <div class="boutique-catalog">
            <div class="boutique-item" data-sku="BTQ-888">
                <a class="boutique-link" href="/shop/silk-evening-gown">
                    <h2 class="boutique-title">Silk Emerald Evening Gown</h2>
                    <span class="boutique-price">$249.00</span>
                </a>
                <img class="boutique-photo" src="https://boutique.example.com/gown.jpg" alt="Evening Gown" />
            </div>
            <div class="boutique-item" data-sku="BTQ-999">
                <a class="boutique-link" href="/shop/velvet-wrap-skirt">
                    <h2 class="boutique-title">Velvet Wrap Skirt</h2>
                    <span class="boutique-price">$89.00</span>
                    <span class="stock-badge">Sold out</span>
                </a>
                <img class="boutique-photo" src="https://boutique.example.com/skirt.jpg" alt="Wrap Skirt" />
            </div>
        </div>
    </body>
    </html>
    """

    store_cfg = ScraperStoreConfig(
        url="https://boutique.example.com/shop",
        currency="USD",
        max_items=10,
        selectors=StoreSelectorConfig(
            item=".boutique-item",
            title=".boutique-title",
            price=".boutique-price",
            image="img.boutique-photo",
            link="a.boutique-link",
            id_attr="data-sku",
        ),
    )

    scraper = ConfigurableStoreScraper(brand_name="boutique", config=store_cfg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)

        items = scraper._extract_with_custom_selectors(
            page=page,
            base_url="https://boutique.example.com",
            max_items=10,
            default_currency="USD",
        )
        browser.close()

    assert len(items) == 2
    item1 = items[0]
    assert item1["id"] == "boutique-BTQ-888"
    assert item1["name"] == "Silk Emerald Evening Gown"
    assert item1["price"] == Decimal("249.00")
    assert item1["currency"] == "USD"
    assert item1["image"] == "https://boutique.example.com/gown.jpg"
    assert item1["url"] == "https://boutique.example.com/shop/silk-evening-gown"
    assert item1["available"] is True

    item2 = items[1]
    assert item2["id"] == "boutique-BTQ-999"
    assert item2["available"] is False


def test_configurable_scraper_universal_json_ld() -> None:
    """Admin adds a new Shopify/WooCommerce website with zero selectors (auto JSON-LD)."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Cashmere Cable Knit Sweater",
            "sku": "KNIT-404",
            "image": "https://nordicfashion.example.com/sweater.jpg",
            "offers": {
                "@type": "Offer",
                "price": "180.00",
                "priceCurrency": "EUR",
                "url": "https://nordicfashion.example.com/p/sweater-404",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
    </head>
    <body>
        <h1>Nordic Fashion</h1>
    </body>
    </html>
    """

    # Zero selectors provided
    store_cfg = ScraperStoreConfig(
        url="https://nordicfashion.example.com/knitwear",
        currency="EUR",
        max_items=5,
    )

    scraper = ConfigurableStoreScraper(brand_name="nordicfashion", config=store_cfg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)

        items = scraper._extract_json_ld(
            page=page,
            base_url="https://nordicfashion.example.com",
            max_items=5,
            default_currency="EUR",
        )
        browser.close()

    assert len(items) == 1
    item = items[0]
    assert item["id"] == "nordicfashion-KNIT-404"
    assert item["name"] == "Cashmere Cable Knit Sweater"
    assert item["price"] == Decimal("180.00")
    assert item["currency"] == "EUR"
    assert item["image"] == "https://nordicfashion.example.com/sweater.jpg"
    assert item["available"] is True


def test_configurable_scraper_universal_semantic_cards() -> None:
    """Admin adds a store with zero selectors and no JSON-LD (auto semantic cards)."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <body>
        <div class="product-grid">
            <div class="product-card">
                <a href="/items/linen-blazer.html">
                    <h3 class="product-title">Relaxed Linen Blazer</h3>
                </a>
                <span class="price">89,95 €</span>
                <img src="/images/blazer.jpg" alt="Linen Blazer" />
            </div>
        </div>
    </body>
    </html>
    """

    store_cfg = ScraperStoreConfig(
        url="https://linenstore.example.com/catalog",
        currency="EUR",
        max_items=5,
    )

    scraper = ConfigurableStoreScraper(brand_name="linenstore", config=store_cfg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)

        items = scraper._extract_semantic_cards(
            page=page,
            base_url="https://linenstore.example.com",
            max_items=5,
            default_currency="EUR",
        )
        browser.close()

    assert len(items) == 1
    item = items[0]
    assert item["name"] == "Relaxed Linen Blazer"
    assert item["price"] == Decimal("89.95")
    assert item["currency"] == "EUR"
    assert item["image"] == "https://linenstore.example.com/images/blazer.jpg"
    assert item["available"] is True
