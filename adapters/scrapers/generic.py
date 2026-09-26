"""Generic fashion e-commerce scraper using JSON-LD and standard schema selectors."""

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from adapters.scrapers.base import (
    dismiss_cookie_banner,
    extract_best_image_url,
    generate_deterministic_id,
    parse_price,
    scroll_page_down,
)

logger = logging.getLogger(__name__)


class GenericFashionScraper:
    """Extracts products from generic fashion sites using Schema.org JSON-LD or meta/card selectors."""

    def __init__(self, brand_name: str = "generic") -> None:
        self.brand_name = brand_name.lower()

    def scrape(
        self,
        page: Any,
        url: str,
        max_items: int = 10,
        default_currency: str = "EUR",
    ) -> list[dict[str, Any]]:
        """Navigate to URL and extract products via JSON-LD or fallback card selectors."""
        logger.info("GenericFashionScraper (%s): navigating to %s", self.brand_name, url)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        dismiss_cookie_banner(page)
        scroll_page_down(page, steps=2, wait_ms=1000)

        # 1. Attempt JSON-LD extraction
        products = self._extract_json_ld(page, base_url=url, max_items=max_items, default_currency=default_currency)
        if products:
            logger.info("GenericFashionScraper: extracted %d products via JSON-LD", len(products))
            return products

        # 2. Fallback to generic card selectors
        products = self._extract_cards(page, base_url=url, max_items=max_items, default_currency=default_currency)
        logger.info("GenericFashionScraper: extracted %d products via card selectors", len(products))
        return products

    def _extract_json_ld(
        self,
        page: Any,
        base_url: str,
        max_items: int,
        default_currency: str,
    ) -> list[dict[str, Any]]:
        """Extract products from <script type="application/ld+json"> tags."""
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

        # Price & currency
        offers = data.get("offers", {})
        if isinstance(offers, list) and offers:
            offers = offers[0]
        elif not isinstance(offers, dict):
            offers = {}

        raw_price = offers.get("price") or data.get("price")
        if raw_price is None:
            return None

        price, currency = parse_price(str(raw_price), default_currency=offers.get("priceCurrency") or default_currency)

        # Image
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

        # Product URL & ID
        prod_url = str(data.get("url") or offers.get("url") or base_url)
        prod_url = urljoin(base_url, prod_url)
        raw_id = str(data.get("sku") or data.get("productID") or data.get("id") or "")
        ext_id = generate_deterministic_id(self.brand_name, raw_id=raw_id, url=prod_url)

        # Availability
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

    def _extract_cards(
        self,
        page: Any,
        base_url: str,
        max_items: int,
        default_currency: str,
    ) -> list[dict[str, Any]]:
        """Fallback card-based extraction."""
        card_selectors = [
            "[itemtype*='Product']",
            ".product-card",
            ".product-item",
            "article.product",
            ".card-product",
        ]

        elements = []
        for sel in card_selectors:
            loc = page.locator(sel)
            if loc.count() > 0:
                elements = [loc.nth(i) for i in range(min(loc.count(), max_items * 2))]
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

                title_el = el.locator("h2, h3, .product-title, .title, a").first
                title = title_el.inner_text().strip() if title_el.count() > 0 else ""
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

                ext_id = generate_deterministic_id(self.brand_name, url=product_url)
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
