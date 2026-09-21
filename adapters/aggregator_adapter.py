"""AggregatorAPIAdapter implementing the SourceAdapter protocol.

Per SDD §3.1 and SRS FR-1.
Encapsulates client communication and normalizes raw JSON/dict records into RawProduct DTOs.
"""

from decimal import Decimal, InvalidOperation
import logging
from typing import Any

from adapters.base import RawProduct, SourceAdapter
from adapters.aggregator_client import AggregatorClient

logger = logging.getLogger(__name__)


class AggregatorAPIAdapter:
    """Production SourceAdapter implementation wrapping an AggregatorClient."""

    def __init__(self, client: AggregatorClient, stores: list[str] | None = None) -> None:
        self.client = client
        self.stores = stores or ["zara", "mango"]

    def fetch_products(self) -> list[RawProduct]:
        """Fetch raw listings via client and normalize into RawProduct DTOs.
        
        Discards out-of-stock or invalid products.
        """
        raw_items = self.client.fetch_raw_listings(self.stores)
        normalized: list[RawProduct] = []

        for item in raw_items:
            try:
                product = self._normalize_item(item)
                if not product.in_stock:
                    logger.debug("Skipping out-of-stock product: %s", product.external_id)
                    continue
                normalized.append(product)
            except (KeyError, ValueError, InvalidOperation) as exc:
                logger.warning("Failed to normalize raw product item %s: %s", item.get("id"), exc)
                continue

        logger.info(
            "AggregatorAPIAdapter: normalized %d in-stock products from %d raw items",
            len(normalized),
            len(raw_items),
        )
        return normalized

    def _normalize_item(self, item: dict[str, Any]) -> RawProduct:
        """Parse raw dictionary into a strictly typed RawProduct."""
        external_id = str(item.get("id") or item.get("external_id") or item["sku"])
        source = str(item.get("brand") or item.get("source") or "unknown").lower()
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            raise ValueError("Product missing title/name")

        raw_price = item.get("price") or item.get("price_original")
        if raw_price is None:
            raise ValueError("Product missing price")
        price = Decimal(str(raw_price)).quantize(Decimal("0.01"))

        currency = str(item.get("currency") or item.get("currency_original") or "EUR").upper()
        photo_url = str(item.get("image") or item.get("photo_url") or item.get("image_url") or "")
        product_url = str(item.get("url") or item.get("product_url") or "")

        # Availability flag
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
