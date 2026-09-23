"""Scraper module registry and factory."""

from typing import Any
from adapters.scrapers.base import StoreScraper, parse_price, extract_best_image_url
from adapters.scrapers.zara import ZaraScraper
from adapters.scrapers.mango import MangoScraper
from adapters.scrapers.generic import GenericFashionScraper
from adapters.scrapers.configurable import ConfigurableStoreScraper
from config.app_config import ScraperStoreConfig

SCRAPER_REGISTRY: dict[str, Any] = {
    "zara": ZaraScraper,
    "mango": MangoScraper,
}


def get_scraper_for_store(store: str, store_cfg: ScraperStoreConfig | None = None) -> StoreScraper:
    """Retrieve appropriate store scraper or fallback to ConfigurableStoreScraper."""
    normalized = store.strip().lower()

    # If the admin provided custom selectors, always use the ConfigurableStoreScraper
    if store_cfg and store_cfg.selectors and store_cfg.selectors.item:
        return ConfigurableStoreScraper(brand_name=normalized, config=store_cfg)

    scraper_cls = SCRAPER_REGISTRY.get(normalized)
    if scraper_cls:
        return scraper_cls()

    return ConfigurableStoreScraper(brand_name=normalized, config=store_cfg)


__all__ = [
    "StoreScraper",
    "ZaraScraper",
    "MangoScraper",
    "GenericFashionScraper",
    "ConfigurableStoreScraper",
    "get_scraper_for_store",
    "parse_price",
    "extract_best_image_url",
]
