"""European storefront selection: Turkey is skipped, one country per scrape."""

from pathlib import Path

from adapters.europe_markets import is_european_store_url, next_region_index
from adapters.playwright_adapter import PlaywrightScraperAdapter
from config.app_config import EuropeanMarketConfig, ScraperSettings, ScraperStoreConfig


def test_turkey_urls_are_rejected() -> None:
    assert is_european_store_url("https://www.zara.com/tr/tr/kadin-yeni-l1180.html") is False
    assert is_european_store_url("https://shop.mango.com/tr/tr/c/kadin/new-now/56b5c5ed") is False
    assert is_european_store_url("https://www.zara.com/us/en/woman-new-in-l1180.html") is False


def test_european_urls_are_allowed() -> None:
    assert is_european_store_url("https://www.zara.com/es/es/mujer-nuevo-l1180.html") is True
    assert is_european_store_url("https://shop.mango.com/gb/en/c/women/new-now/56b5c5ed") is True
    assert is_european_store_url("https://www.stradivarius.com/es/mujer/novedades-n1474") is True
    assert is_european_store_url("https://www.stradivarius.com/tr/kadin/yeni") is False
    assert is_european_store_url("https://www2.hm.com/es_es/mujer/novedades/ver-todo.html") is True
    assert is_european_store_url("https://www.cos.com/es-es/women/new-arrivals.html") is True
    assert is_european_store_url("https://boutique.example.com/catalog") is False


def test_resolve_rotates_one_european_market(tmp_path: Path) -> None:
    index_path = tmp_path / "region_index.txt"
    settings = ScraperSettings(
        regions=[
            EuropeanMarketConfig(
                code="es",
                currency="EUR",
                urls={
                    "zara": "https://www.zara.com/es/es/mujer-nuevo-l1180.html",
                    "mango": "https://shop.mango.com/es/es/c/mujer/new-now/56b5c5ed",
                },
            ),
            EuropeanMarketConfig(
                code="fr",
                currency="EUR",
                urls={
                    "zara": "https://www.zara.com/fr/fr/femme-nouveau-l1180.html",
                    "mango": "https://shop.mango.com/fr/fr/c/femme/new-now/56b5c5ed",
                },
            ),
        ],
        stores={
            "zara": ScraperStoreConfig(
                url="https://www.zara.com/tr/tr/kadin-yeni-l1180.html",
                currency="TRY",
                max_items=60,
            ),
            "mango": ScraperStoreConfig(
                url="https://shop.mango.com/tr/tr/c/kadin/new-now/56b5c5ed",
                currency="TRY",
                max_items=60,
            ),
            "usa": ScraperStoreConfig(
                url="https://www.zara.com/us/en/woman-new-in-l1180.html",
                currency="USD",
            ),
        },
    )
    adapter = PlaywrightScraperAdapter(config=settings)

    from adapters import europe_markets

    original = europe_markets.REGION_INDEX_PATH
    europe_markets.REGION_INDEX_PATH = index_path
    try:
        first = adapter._resolve_store_targets(settings)
        second = adapter._resolve_store_targets(settings)
    finally:
        europe_markets.REGION_INDEX_PATH = original

    assert "usa" not in first
    assert "/es/" in first["zara"].url
    assert first["zara"].currency == "EUR"
    assert "/tr/" not in first["mango"].url
    assert "/fr/" in second["zara"].url
    assert second["mango"].currency == "EUR"


def test_marks_and_spencer_main_site_is_the_uk_shop() -> None:
    from adapters.europe_markets import currency_for_market, market_code_from_url

    # The customer's link: no country in the address, still a European (UK) catalog.
    assert market_code_from_url("https://marksandspencer.com/") == "gb"
    assert is_european_store_url("https://marksandspencer.com/") is True
    assert currency_for_market(market_code_from_url("https://www.marksandspencer.com/l/women/new-in")) == "GBP"
    # Its other countries are named in the path, and that wins.
    assert market_code_from_url("https://www.marksandspencer.com/ie/l/women") == "ie"


def test_an_address_without_a_country_is_still_not_taken_for_europe() -> None:
    assert is_european_store_url("https://www.zara.com/") is False
    assert is_european_store_url("https://boutique.example.com/catalog") is False
