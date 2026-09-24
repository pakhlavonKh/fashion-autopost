"""Configurable store scraper enabling zero-code addition of arbitrary e-commerce websites.

Supports both:
1. Declarative custom CSS selector overrides (via ScraperStoreConfig.selectors).
2. Zero-configuration automatic extraction via Schema.org JSON-LD, Microdata, and semantic card heuristics.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

from adapters.scrapers.base import (
    dismiss_cookie_banner,
    extract_best_image_url,
    parse_price,
    scroll_page_down,
)
from config.app_config import ScraperStoreConfig

logger = logging.getLogger(__name__)


class ConfigurableStoreScraper:
    """Universal scraper for any website configured by the admin in config.yaml."""

    def __init__(self, brand_name: str, config: ScraperStoreConfig | None = None) -> None:
        self.brand_name = brand_name.lower()
        self.config = config

    def scrape(
        self,
        page: Any,
        url: str,
        max_items: int = 10,
        default_currency: str = "EUR",
    ) -> list[dict[str, Any]]:
        """Navigate to store URL and extract products using custom selectors or universal fallback."""
        logger.info("ConfigurableStoreScraper (%s): navigating to %s", self.brand_name, url)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if resp and resp.status in (403, 401, 429):
            logger.warning(
                "ConfigurableStoreScraper (%s): received HTTP %d from %s (anti-bot WAF protection)",
                self.brand_name,
                resp.status,
                url,
            )
        elif "access denied" in (page.title() or "").lower() or "forbidden" in (page.title() or "").lower():
            logger.warning(
                "ConfigurableStoreScraper (%s): anti-bot challenge detected at %s (Title: '%s')",
                self.brand_name,
                url,
                page.title(),
            )

        cookie_button = self.config.cookie_button if self.config else None
        dismiss_cookie_banner(page, custom_button=cookie_button)

        scroll_steps = (self.config.scroll_steps if self.config and self.config.scroll_steps is not None else 3)
        scroll_page_down(page, steps=scroll_steps, wait_ms=1200)

        # 1. If admin provided custom selectors, use declarative extraction
        if self.config and self.config.selectors and self.config.selectors.item:
            products = self._extract_with_custom_selectors(
                page=page,
                base_url=url,
                max_items=max_items,
                default_currency=default_currency,
            )
            if products:
                logger.info(
                    "ConfigurableStoreScraper (%s): extracted %d products with custom selectors",
                    self.brand_name,
                    len(products),
                )
                return products

        # 2. Universal extraction strategy 1: Schema.org JSON-LD
        products = self._extract_json_ld(
            page=page,
            base_url=url,
            max_items=max_items,
            default_currency=default_currency,
        )
        if products:
            logger.info(
                "ConfigurableStoreScraper (%s): extracted %d products via JSON-LD",
                self.brand_name,
                len(products),
            )
            return products

        # 3. Universal extraction strategy 2: Microdata and semantic fashion cards
        products = self._extract_semantic_cards(
            page=page,
            base_url=url,
            max_items=max_items,
            default_currency=default_currency,
        )
        logger.info(
            "ConfigurableStoreScraper (%s): extracted %d products via semantic cards",
            self.brand_name,
            len(products),
        )
        return products

    def _extract_with_custom_selectors(
        self,
        page: Any,
        base_url: str,
        max_items: int,
        default_currency: str,
    ) -> list[dict[str, Any]]:
        """Extract items using admin-configured CSS selectors."""
        selectors = self.config.selectors  # type: ignore[union-attr]
        items_locator = page.locator(selectors.item)
        count = items_locator.count()
        if count == 0:
            logger.warning(
                "Custom selector '%s' matched 0 elements on %s",
                selectors.item,
                base_url,
            )
            return []

        products: list[dict[str, Any]] = []
        for i in range(min(count, max_items * 3)):
            if len(products) >= max_items:
                break
            el = items_locator.nth(i)
            try:
                # Link
                href = ""
                if selectors.link:
                    link_el = el.locator(selectors.link).first
                    if link_el.count() > 0:
                        href = link_el.get_attribute("href") or ""
                if not href:
                    if el.get_attribute("href"):
                        href = el.get_attribute("href") or ""
                    else:
                        first_a = el.locator("a[href]").first
                        if first_a.count() > 0:
                            href = first_a.get_attribute("href") or ""

                if not href:
                    continue
                product_url = urljoin(base_url, href)

                # Title
                title = ""
                if selectors.title:
                    t_el = el.locator(selectors.title).first
                    if t_el.count() > 0:
                        title = t_el.inner_text().strip()
                if not title:
                    t_el = el.locator("h1, h2, h3, h4, .title, .product-title, .name, a").first
                    if t_el.count() > 0:
                        title = t_el.inner_text().strip()
                if not title:
                    img_alt = el.locator("img[alt]").first
                    if img_alt.count() > 0:
                        title = img_alt.get_attribute("alt") or ""
                if not title:
                    continue
                title = " ".join(title.split())

                # Price
                price_text = ""
                if selectors.price:
                    p_el = el.locator(selectors.price).first
                    if p_el.count() > 0:
                        price_text = p_el.inner_text().strip()
                if not price_text:
                    all_text = el.inner_text()
                    price_match = re.search(r"(\d+[\d.,]*\s*[€$£]|[\d.,]+\s*EUR|[\d.,]+\s*USD|[€$£]\s*[\d.,]+)", all_text)
                    if price_match:
                        price_text = price_match.group(0)
                if not price_text:
                    continue

                price, currency = parse_price(price_text, default_currency=default_currency)

                # Image
                img_src = ""
                srcset = ""
                if selectors.image:
                    img_el = el.locator(selectors.image).first
                    if img_el.count() > 0:
                        img_src = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""
                        srcset = img_el.get_attribute("srcset") or ""
                if not img_src:
                    img_el = el.locator("img").first
                    if img_el.count() > 0:
                        img_src = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""
                        srcset = img_el.get_attribute("srcset") or ""

                photo_url = extract_best_image_url(img_src, srcset=srcset, base_url=base_url)
                if not photo_url:
                    continue

                # ID
                ext_id = ""
                if selectors.id_attr:
                    ext_id = el.get_attribute(selectors.id_attr) or ""
                    if not ext_id:
                        id_el = el.locator(f"[{selectors.id_attr}]").first
                        if id_el.count() > 0:
                            ext_id = id_el.get_attribute(selectors.id_attr) or ""
                if not ext_id:
                    ext_id = el.get_attribute("data-product-id") or el.get_attribute("data-sku") or el.get_attribute("data-id") or ""
                if not ext_id:
                    ext_id = f"{abs(hash(product_url)) % 10000000}"

                ext_id = f"{self.brand_name}-{ext_id}"

                # Stock
                card_text_lower = el.inner_text().lower()
                is_out = any(phrase in card_text_lower for phrase in ["out of stock", "sold out", "agotado", "rupture de stock"])

                products.append({
                    "id": ext_id,
                    "brand": self.brand_name,
                    "name": title,
                    "price": price,
                    "currency": currency,
                    "image": photo_url,
                    "url": product_url,
                    "available": not is_out,
                })
            except Exception as exc:
                logger.debug("Failed to extract item with custom selectors: %s", exc)
                continue

        return products

    def _extract_json_ld(
        self,
        page: Any,
        base_url: str,
        max_items: int,
        default_currency: str,
    ) -> list[dict[str, Any]]:
        """Extract products from Schema.org JSON-LD scripts."""
        scripts = page.locator("script[type='application/ld+json']")
        count = scripts.count()
        results: list[dict[str, Any]] = []

        for i in range(count):
            if len(results) >= max_items:
                break
            raw_json = scripts.nth(i).inner_text().strip()
            if not raw_json:
                continue

            try:
                data = json.loads(raw_json)
            except json.JSONDecodeError:
                continue

            candidates = self._find_products_in_json(data)
            for c in candidates:
                if len(results) >= max_items:
                    break
                try:
                    product = self._normalize_json_product(c, base_url=base_url, default_currency=default_currency)
                    if product:
                        results.append(product)
                except Exception as exc:
                    logger.debug("Failed to normalize JSON-LD product: %s", exc)

        return results

    def _find_products_in_json(self, data: Any) -> list[dict[str, Any]]:
        """Traverse JSON to find Product objects."""
        found = []
        if isinstance(data, list):
            for item in data:
                found.extend(self._find_products_in_json(item))
        elif isinstance(data, dict):
            item_type = str(data.get("@type", ""))
            if item_type == "Product":
                found.append(data)
            elif item_type == "ItemList" and "itemListElement" in data:
                for el in data["itemListElement"]:
                    if isinstance(el, dict) and "item" in el and isinstance(el["item"], dict):
                        found.append(el["item"])
                    elif isinstance(el, dict) and el.get("@type") == "Product":
                        found.append(el)
            else:
                for v in data.values():
                    if isinstance(v, (dict, list)):
                        found.extend(self._find_products_in_json(v))
        return found

    def _normalize_json_product(
        self,
        data: dict[str, Any],
        base_url: str,
        default_currency: str,
    ) -> dict[str, Any] | None:
        """Map JSON-LD Product dict to normalized dictionary."""
        name = str(data.get("name", "")).strip()
        if not name:
            return None

        offers = data.get("offers", {})
        if isinstance(offers, list) and offers:
            offers = offers[0]
        elif not isinstance(offers, dict):
            offers = {}

        raw_price = offers.get("price") or data.get("price")
        if raw_price is None:
            return None

        currency = str(offers.get("priceCurrency") or default_currency).upper()
        price, currency = parse_price(str(raw_price), default_currency=currency)

        img_data = data.get("image")
        photo_url = ""
        if isinstance(img_data, list) and img_data:
            photo_url = str(img_data[0])
        elif isinstance(img_data, dict):
            photo_url = str(img_data.get("url") or img_data.get("contentUrl") or "")
        elif isinstance(img_data, str):
            photo_url = img_data

        photo_url = extract_best_image_url(photo_url, base_url=base_url)
        if not photo_url:
            return None

        prod_url = str(data.get("url") or offers.get("url") or base_url)
        prod_url = urljoin(base_url, prod_url)
        ext_id = str(data.get("sku") or data.get("productID") or data.get("id") or "")
        if not ext_id:
            ext_id = f"{self.brand_name}-{abs(hash(prod_url)) % 10000000}"
        else:
            ext_id = f"{self.brand_name}-{ext_id}"

        availability = str(offers.get("availability", "")).lower()
        in_stock = "outofstock" not in availability

        return {
            "id": ext_id,
            "brand": self.brand_name,
            "name": name,
            "price": price,
            "currency": currency,
            "image": photo_url,
            "url": prod_url,
            "available": in_stock,
        }

    def _extract_semantic_cards(
        self,
        page: Any,
        base_url: str,
        max_items: int,
        default_currency: str,
    ) -> list[dict[str, Any]]:
        """Fallback card-based extraction using common fashion e-commerce selectors."""
        card_selectors = [
            "[itemtype*='Product']",
            ".product-card",
            ".product-item",
            "article.product",
            ".grid-product",
            ".product-grid-item",
            ".card-product",
            "[data-component='product-card']",
            "li[class*='product']",
        ]

        elements = []
        for sel in card_selectors:
            loc = page.locator(sel)
            count = loc.count()
            if count > 0:
                elements = [loc.nth(i) for i in range(min(count, max_items * 2))]
                break

        products = []
        for el in elements:
            if len(products) >= max_items:
                break
            try:
                link_el = el.locator("a[href]").first
                if link_el.count() == 0:
                    continue
                href = link_el.get_attribute("href") or ""
                product_url = urljoin(base_url, href)

                title_el = el.locator("h2, h3, h4, .product-title, .title, a").first
                title = title_el.inner_text().strip() if title_el.count() > 0 else ""
                if not title:
                    img_alt = el.locator("img[alt]").first
                    if img_alt.count() > 0:
                        title = img_alt.get_attribute("alt") or ""
                if not title:
                    continue

                all_text = el.inner_text()
                price_match = re.search(r"(\d+[\d.,]*\s*[€$£]|[\d.,]+\s*EUR|[\d.,]+\s*USD|[€$£]\s*[\d.,]+)", all_text)
                if not price_match:
                    continue
                price, currency = parse_price(price_match.group(0), default_currency=default_currency)

                img_el = el.locator("img").first
                img_src = img_el.get_attribute("src") or img_el.get_attribute("data-src") if img_el.count() > 0 else ""
                photo_url = extract_best_image_url(img_src, base_url=base_url)
                if not photo_url:
                    continue

                ext_id = f"{self.brand_name}-{abs(hash(product_url)) % 10000000}"
                products.append({
                    "id": ext_id,
                    "brand": self.brand_name,
                    "name": title,
                    "price": price,
                    "currency": currency,
                    "image": photo_url,
                    "url": product_url,
                    "available": True,
                })
            except Exception:
                continue

        return products
