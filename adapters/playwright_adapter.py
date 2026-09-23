"""PlaywrightScraperAdapter implementing the SourceAdapter protocol.

Per SDD §3.1 and SRS FR-1.
Autonomous web scraping adapter using Playwright to scrape real clothing items
from e-commerce stores (Zara, Mango, etc.) and convert them to RawProduct DTOs.
"""

from decimal import Decimal, InvalidOperation
import logging
from typing import Any
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from adapters.base import RawProduct, SourceAdapter
from adapters.scrapers import get_scraper_for_store
from config.app_config import ScraperSettings, ScraperStoreConfig

logger = logging.getLogger(__name__)


class PlaywrightScraperAdapter:
    """Production SourceAdapter implementation that scrapes e-commerce websites with Playwright."""

    def __init__(
        self,
        config: ScraperSettings | None = None,
        selected_stores: list[str] | None = None,
        app_config: Any | None = None,
    ) -> None:
        self._static_config = config
        self.app_config = app_config
        self._selected_stores = [s.lower() for s in selected_stores] if selected_stores else None

    @property
    def current_config(self) -> ScraperSettings:
        """Dynamically return active config (allowing hot-reloading without restart)."""
        if self.app_config and hasattr(self.app_config, "scraper"):
            return self.app_config.scraper
        return self._static_config or ScraperSettings()

    @property
    def config(self) -> ScraperSettings:
        return self.current_config

    def fetch_products(self) -> list[RawProduct]:
        """Scrape active stores using Playwright and normalize items into RawProduct DTOs.
        
        Isolates errors per store so a temporary failure or anti-bot block on one store
        does not prevent scraping remaining stores.
        """
        active_config = self.current_config
        stores_to_scrape = (
            self._selected_stores
            if self._selected_stores is not None
            else list(active_config.stores.keys())
        )

        all_raw_items: list[dict[str, Any]] = []

        logger.info(
            "PlaywrightScraperAdapter: starting scrape for stores: %s (headless=%s)",
            stores_to_scrape,
            active_config.headless,
        )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=active_config.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                try:
                    context = browser.new_context(
                        user_agent=active_config.user_agent,
                        viewport={"width": 1920, "height": 1080},
                        locale="en-US",
                    )
                    context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    )

                    for store_name in stores_to_scrape:
                        store_cfg = active_config.stores.get(store_name)
                        if not store_cfg:
                            logger.warning(
                                "No scraper configuration found for store '%s', using default URL",
                                store_name,
                            )
                            store_cfg = ScraperStoreConfig(
                                enabled=True,
                                url=f"https://www.{store_name}.com",
                                currency="EUR",
                                max_items=10,
                            )

                        if not store_cfg.enabled:
                            logger.info("Store '%s' is disabled in scraper configuration, skipping", store_name)
                            continue

                        try:
                            store_items = self._scrape_single_store(context, store_name, store_cfg)
                            all_raw_items.extend(store_items)
                        except Exception as exc:
                            logger.error("Error scraping store '%s': %s", store_name, exc)

                    context.close()
                finally:
                    browser.close()
        except Exception as exc:
            logger.error("Playwright browser session failed: %s", exc)

        normalized: list[RawProduct] = []
        for item in all_raw_items:
            try:
                product = self._normalize_item(item)
                if not product.in_stock:
                    logger.debug("Skipping out-of-stock product: %s", product.external_id)
                    continue
                normalized.append(product)
            except (KeyError, ValueError, InvalidOperation) as exc:
                logger.warning(
                    "Failed to normalize scraped product %s: %s", item.get("id"), exc
                )
                continue

        logger.info(
            "PlaywrightScraperAdapter: collected %d valid in-stock products from %d total scraped",
            len(normalized),
            len(all_raw_items),
        )
        return normalized

    def _scrape_single_store(
        self,
        context: BrowserContext,
        store_name: str,
        store_cfg: ScraperStoreConfig,
    ) -> list[dict[str, Any]]:
        """Scrape a single store with isolated page lifecycle and error handling."""
        logger.info("Scraping store '%s' at URL: %s", store_name, store_cfg.url)
        page: Page | None = None
        try:
            page = context.new_page()
            page.set_default_timeout(self.current_config.timeout_seconds * 1000)
            scraper = get_scraper_for_store(store_name, store_cfg)
            items = scraper.scrape(
                page=page,
                url=store_cfg.url,
                max_items=store_cfg.max_items,
                default_currency=store_cfg.currency,
            )
            return items
        except Exception as exc:
            logger.error("Failed to scrape store '%s' (%s): %s", store_name, store_cfg.url, exc)
            return []
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    def _normalize_item(self, item: dict[str, Any]) -> RawProduct:
        """Parse raw dictionary into a strictly typed RawProduct."""
        external_id = str(item.get("id") or item.get("external_id") or item.get("sku") or "")
        if not external_id:
            raise ValueError("Scraped item missing ID/SKU")

        source = str(item.get("brand") or item.get("source") or "unknown").lower()
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            raise ValueError("Scraped item missing title/name")

        raw_price = item.get("price") or item.get("price_original")
        if raw_price is None:
            raise ValueError("Scraped item missing price")

        if isinstance(raw_price, Decimal):
            price = raw_price.quantize(Decimal("0.01"))
        else:
            price = Decimal(str(raw_price)).quantize(Decimal("0.01"))

        currency = str(item.get("currency") or "EUR").upper()
        photo_url = str(item.get("image") or item.get("photo_url") or item.get("image_url") or "")
        if not photo_url:
            raise ValueError("Scraped item missing image URL")

        product_url = str(item.get("url") or item.get("product_url") or "")
        in_stock = bool(item.get("available", item.get("in_stock", True)))

        return RawProduct(
            external_id=external_id,
            source=source,
            title=title,
            price=price,
            currency=currency,
            photo_url=photo_url,
            product_url=product_url,
            in_stock=in_stock,
        )
