"""Playwright scraper for Zara product catalog pages."""

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

logger = logging.getLogger(__name__)


class ZaraScraper:
    """Extracts clothing product listings from Zara category pages."""

    BRAND_NAME = "zara"

    def scrape(
        self,
        page: Any,
        url: str,
        max_items: int = 10,
        default_currency: str = "EUR",
    ) -> list[dict[str, Any]]:
        """Navigate to Zara category URL and extract product dictionaries."""
        logger.info("ZaraScraper: navigating to %s", url)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if resp and resp.status in (403, 401, 429):
            logger.warning(
                "ZaraScraper: received HTTP %d from %s (anti-bot WAF protection)",
                resp.status,
                url,
            )
        elif "access denied" in (page.title() or "").lower():
            logger.warning(
                "ZaraScraper: Access Denied / anti-bot challenge detected at %s",
                url,
            )

        # Allow initial render & dismiss cookie dialog
        dismiss_cookie_banner(page)
        scroll_page_down(page, steps=3, wait_ms=1200)

        # Candidate item selectors on Zara
        item_selectors = [
            "li.product-grid-product",
            "article.product-grid-product-info",
            "div.product-grid-product-info",
            "ul.product-grid__product-list > li",
            "section.product-grid li",
        ]

        elements = []
        for selector in item_selectors:
            loc = page.locator(selector)
            count = loc.count()
            if count > 0:
                logger.debug("ZaraScraper found %d elements with selector '%s'", count, selector)
                elements = [loc.nth(i) for i in range(min(count, max_items * 3))]
                break

        products: list[dict[str, Any]] = []
        for el in elements:
            if len(products) >= max_items:
                break

            try:
                item_data = self._extract_element_data(el, base_url=url, default_currency=default_currency)
                if item_data:
                    products.append(item_data)
            except Exception as exc:
                logger.debug("ZaraScraper: failed to extract item: %s", exc)
                continue

        logger.info("ZaraScraper: successfully extracted %d products from %s", len(products), url)
        return products

    def _extract_element_data(
        self,
        el: Any,
        base_url: str,
        default_currency: str,
    ) -> dict[str, Any] | None:
        """Extract product attributes from a Zara product card element."""
        # 1. Product Link & ID
        link_el = el.locator("a.product-link, a.product-grid-product__link, a[href*='-p']").first
        href = ""
        if link_el.count() > 0:
            href = link_el.get_attribute("href") or ""
        if not href:
            # Fallback to any anchor in element
            any_a = el.locator("a").first
            if any_a.count() > 0:
                href = any_a.get_attribute("href") or ""

        if not href:
            return None

        product_url = urljoin(base_url, href)

        # External ID
        ext_id = (
            el.get_attribute("data-product-id")
            or el.get_attribute("data-id")
            or ""
        )
        if not ext_id:
            # Parse from URL, e.g., "-p01234567.html" or "p12345"
            match = re.search(r"-p([0-9A-Za-z]+)\.html", product_url) or re.search(r"p(\d{5,})", product_url)
            if match:
                ext_id = f"zara-{match.group(1)}"
            else:
                ext_id = f"zara-{abs(hash(product_url)) % 10000000}"
        else:
            ext_id = f"zara-{ext_id}"

        # 2. Product Name / Title
        name_el = el.locator(
            "h2, .product-grid-product-info__name, .product-link-info-slug, [data-qa-qualifier='product-name']"
        ).first
        title = ""
        if name_el.count() > 0:
            title = name_el.inner_text().strip()
        if not title and link_el.count() > 0:
            title = link_el.inner_text().strip()
        if not title:
            # Try aria-label or img alt
            img_el = el.locator("img").first
            if img_el.count() > 0:
                title = img_el.get_attribute("alt") or ""

        if not title:
            return None

        # Clean multiple spaces/newlines
        title = " ".join(title.split())

        # 3. Price
        price_el = el.locator(
            ".money-amount__main, [data-qa-qualifier='price-amount'], .price-current, .price__amount, span.price"
        ).first
        price_text = ""
        if price_el.count() > 0:
            price_text = price_el.inner_text().strip()

        if not price_text:
            # Look for money pattern in entire card text
            all_text = el.inner_text()
            price_match = re.search(r"(\d+[\d.,]*\s*[€$£]|[\d.,]+\s*EUR|[\d.,]+\s*USD|[€$£]\s*[\d.,]+)", all_text)
            if price_match:
                price_text = price_match.group(0)

        if not price_text:
            return None

        price, currency = parse_price(price_text, default_currency=default_currency)

        # 4. Image URL
        img_el = el.locator("img.media-image__image, picture img, img").first
        img_src = ""
        srcset = ""
        if img_el.count() > 0:
            img_src = (
                img_el.get_attribute("src")
                or img_el.get_attribute("data-src")
                or ""
            )
            srcset = img_el.get_attribute("srcset") or ""

        # Check picture source
        source_el = el.locator("picture source").first
        if source_el.count() > 0 and not srcset:
            srcset = source_el.get_attribute("srcset") or ""

        photo_url = extract_best_image_url(img_src, srcset=srcset, base_url=base_url)
        if not photo_url:
            return None

        # 5. Availability
        card_text_lower = el.inner_text().lower()
        is_sold_out = any(phrase in card_text_lower for phrase in ["out of stock", "agotado", "sold out"])
        in_stock = not is_sold_out

        return {
            "id": ext_id,
            "brand": self.BRAND_NAME,
            "name": title,
            "price": price,
            "currency": currency,
            "image": photo_url,
            "url": product_url,
            "available": in_stock,
        }
