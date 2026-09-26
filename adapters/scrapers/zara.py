import json
import logging
import re
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from adapters.scrapers.base import (
    dismiss_cookie_banner,
    extract_best_image_url,
    generate_deterministic_id,
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

        products_by_id: dict[str, dict[str, Any]] = {}

        # 1. High-fidelity Schema.org ItemList JSON-LD extraction
        ld_products = self._extract_from_json_ld(page, base_url=url, max_items=max_items, default_currency=default_currency)
        for p in ld_products:
            products_by_id[p["id"]] = p

        # 2. Extract from DOM product cards if more items are requested
        if len(products_by_id) < max_items:
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
                    elements = [loc.nth(i) for i in range(count)]
                    break

            for el in elements:
                if len(products_by_id) >= max_items:
                    break
                try:
                    item_data = self._extract_element_data(el, base_url=url, default_currency=default_currency)
                    if item_data and item_data["id"] not in products_by_id:
                        products_by_id[item_data["id"]] = item_data
                except Exception as exc:
                    logger.debug("ZaraScraper: failed to extract item: %s", exc)

        # 3. Discover and crawl subcategories across the website if more items are needed
        if len(products_by_id) < max_items:
            cat_loc = page.locator("a.layout-categories-category-wrapper, a[href*='kadin-'][href*='-l']")
            cat_urls = []
            try:
                for i in range(min(cat_loc.count(), 25)):
                    href = cat_loc.nth(i).get_attribute("href")
                    if href and "-l" in href and href not in cat_urls and href != url:
                        cat_urls.append(urljoin(url, href))
            except Exception as e:
                logger.debug("ZaraScraper: category discovery error: %s", e)

            for cat_url in cat_urls:
                if len(products_by_id) >= max_items:
                    break
                try:
                    logger.info("ZaraScraper: crawling category: %s", cat_url)
                    page.goto(cat_url, wait_until="domcontentloaded", timeout=25000)
                    dismiss_cookie_banner(page)
                    sub_ld = self._extract_from_json_ld(
                        page,
                        base_url=cat_url,
                        max_items=max_items - len(products_by_id),
                        default_currency=default_currency,
                    )
                    for p in sub_ld:
                        if p["id"] not in products_by_id:
                            products_by_id[p["id"]] = p
                except Exception as cat_err:
                    logger.warning("ZaraScraper: failed to crawl category %s: %s", cat_url, cat_err)

        products = list(products_by_id.values())[:max_items]
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
        raw_id = (
            el.get_attribute("data-product-id")
            or el.get_attribute("data-id")
            or ""
        )
        ext_id = generate_deterministic_id(self.BRAND_NAME, raw_id=raw_id, url=product_url)

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
            price_match = re.search(r"(\d+[\d.,]*\s*[€$£₺]|[\d.,]+\s*(?:EUR|USD|TRY|TL)|[€$£₺]\s*[\d.,]+)", all_text)
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
        is_sold_out = any(phrase in card_text_lower for phrase in ["out of stock", "agotado", "sold out", "tükendi", "stokta yok"])
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

    def _extract_from_json_ld(
        self,
        page: Any,
        base_url: str,
        max_items: int = 10,
        default_currency: str = "EUR",
    ) -> list[dict[str, Any]]:
        """Extract high-fidelity product listings from Schema.org ItemList JSON-LD."""
        products: list[dict[str, Any]] = []
        try:
            scripts = page.locator("script[type='application/ld+json']").all()
            for s in scripts:
                txt = s.inner_text().strip()
                if not txt:
                    continue
                try:
                    data = json.loads(txt)
                except Exception:
                    continue

                items_raw: list[Any] = []
                if isinstance(data, dict):
                    if data.get("@type") == "ItemList" and "itemListElement" in data:
                        items_raw = data["itemListElement"]
                    elif data.get("@type") in ("Product", "ProductGroup"):
                        items_raw = [{"item": data}]
                elif isinstance(data, list):
                    items_raw = [{"item": x} for x in data if isinstance(x, dict)]

                for item_entry in items_raw:
                    if len(products) >= max_items:
                        break
                    it = item_entry.get("item", item_entry) if isinstance(item_entry, dict) else {}
                    if not isinstance(it, dict):
                        continue

                    name = it.get("name")
                    if not name:
                        continue

                    offers = it.get("offers", {})
                    if isinstance(offers, list) and offers:
                        offers = offers[0]

                    price_val = offers.get("price") if isinstance(offers, dict) else None
                    if price_val is None:
                        continue

                    try:
                        price = Decimal(str(price_val)).quantize(Decimal("0.01"))
                    except Exception:
                        continue

                    curr = (offers.get("priceCurrency") or default_currency).upper() if isinstance(offers, dict) else default_currency
                    prod_url = offers.get("url") or it.get("url") or "" if isinstance(offers, dict) else it.get("url") or ""
                    if not prod_url:
                        continue
                    prod_url = urljoin(base_url, prod_url)

                    image = it.get("image")
                    if isinstance(image, list) and image:
                        image = image[0]
                    if not image or not isinstance(image, str):
                        continue

                    ext_id = generate_deterministic_id(self.BRAND_NAME, url=prod_url)

                    products.append({
                        "id": ext_id,
                        "brand": self.BRAND_NAME,
                        "name": str(name).strip(),
                        "price": price,
                        "currency": curr,
                        "image": image,
                        "url": prod_url,
                        "available": True,
                    })
        except Exception as exc:
            logger.debug("ZaraScraper: JSON-LD extraction error: %s", exc)

        return products
