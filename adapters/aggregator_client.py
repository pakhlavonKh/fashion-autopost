"""Aggregator client interface and implementations.

Per SDD §3.1 and SRS §10.1 (Open Question 1: Aggregator API contract).
Provides an isolated AggregatorClient protocol, a production-ready MockAggregatorClient,
and an HttpAggregatorClient skeleton with explicit provider contract checklists.
"""

from decimal import Decimal
import logging
from typing import Any, Protocol, runtime_checkable
import httpx

logger = logging.getLogger(__name__)


@runtime_checkable
class AggregatorClient(Protocol):
    """Low-level transport client for fetching raw product payloads from an aggregator."""

    def fetch_raw_listings(self, stores: list[str]) -> list[dict[str, Any]]:
        """Fetch raw product listing dictionaries for specified stores."""
        ...


class MockAggregatorClient:
    """Mock aggregator client providing realistic Zara and Mango fashion catalog data.
    
    Used for local testing, dry-runs, and development prior to live provider onboarding.
    """

    def __init__(self) -> None:
        self._sample_products: list[dict[str, Any]] = [
            {
                "id": "zara-dr-101",
                "brand": "zara",
                "name": "Pleated Satin Midi Dress",
                "price": 89.90,
                "currency": "EUR",
                "image": "https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=800&auto=format&fit=crop&q=80",
                "url": "https://www.zara.com/sample/pleated-satin-midi-dress-101",
                "available": True,
            },
            {
                "id": "zara-bl-204",
                "brand": "zara",
                "name": "Tailored Double-Breasted Wool Blazer",
                "price": 129.00,
                "currency": "EUR",
                "image": "https://images.unsplash.com/photo-1591047139829-d91aecb6caea?w=800&auto=format&fit=crop&q=80",
                "url": "https://www.zara.com/sample/double-breasted-blazer-204",
                "available": True,
            },
            {
                "id": "mango-ct-305",
                "brand": "mango",
                "name": "Belted Trench Coat in Camel",
                "price": 149.99,
                "currency": "USD",
                "image": "https://images.unsplash.com/photo-1544441893-675973e31985?w=800&auto=format&fit=crop&q=80",
                "url": "https://shop.mango.com/sample/belted-trench-coat-305",
                "available": True,
            },
            {
                "id": "mango-sh-402",
                "brand": "mango",
                "name": "100% Pure Linen Relaxed Shirt",
                "price": 59.99,
                "currency": "USD",
                "image": "https://images.unsplash.com/photo-1598033129183-c4f50c736f10?w=800&auto=format&fit=crop&q=80",
                "url": "https://shop.mango.com/sample/linen-relaxed-shirt-402",
                "available": True,
            },
            {
                "id": "zara-sk-508",
                "brand": "zara",
                "name": "High-Waist Wide-Leg Denim Trousers",
                "price": 69.90,
                "currency": "EUR",
                "image": "https://images.unsplash.com/photo-1582533561751-ef6f6ab93a2e?w=800&auto=format&fit=crop&q=80",
                "url": "https://www.zara.com/sample/wide-leg-denim-508",
                "available": True,
            },
            {
                "id": "mango-oo-999",
                "brand": "mango",
                "name": "Out of Stock Cashmere Knit",
                "price": 199.99,
                "currency": "USD",
                "image": "https://images.unsplash.com/photo-1576566588028-4147f3842f27?w=800&auto=format&fit=crop&q=80",
                "url": "https://shop.mango.com/sample/cashmere-knit-999",
                "available": False,  # Should be filtered out by adapter
            },
        ]

    def fetch_raw_listings(self, stores: list[str]) -> list[dict[str, Any]]:
        normalized_stores = {s.lower() for s in stores}
        results = [
            item for item in self._sample_products
            if item.get("brand", "").lower() in normalized_stores
        ]
        logger.info(
            "MockAggregatorClient: fetched %d raw products for stores %s",
            len(results),
            stores,
        )
        return results


# ==============================================================================
# TODO (SRS §10.1): CONFIRM CONTRACT WITH THIRD-PARTY AGGREGATOR PROVIDER
# ------------------------------------------------------------------------------
# Before switching aggregator mode to 'http' in production, confirm the following
# 8 architectural and commercial details with the data vendor:
#
# 1. Endpoint Architecture:
#    - Polling REST endpoint (e.g., GET /products) vs. Webhook delivery.
# 2. Authentication Mechanism:
#    - API key in header (e.g., X-API-Key or Authorization: Bearer <token>),
#      query param, or OAuth2 client credentials.
# 3. Response Schema Field Names:
#    - ID field: 'id' vs. 'product_id' vs. 'sku'
#    - Photos: Array of URLs or single URL?
#    - Price format: Float, string, or integer cents?
#    - Stock availability format: boolean 'in_stock' vs. inventory count.
# 4. Photo Format & Direct Hosting:
#    - Are photo URLs public, permanent HTTPS URLs? (Crucial for Instagram Graph API)
#    - If direct download files or authenticated URLs are returned, S3 re-hosting
#      via publishers.image_hosting.S3ImageHost is required.
# 5. Catalog Refresh Frequency & Pagination:
#    - How often does the provider update prices/stock (hourly, daily)?
#    - Pagination parameters (limit, offset, cursor).
# 6. Store Brand Filtering:
#    - Parameter name for store selection (e.g. ?brands=zara,mango).
# 7. Rate Limits & Quotas:
#    - Requests per minute / month; cost of bursting.
# 8. Terms of Service:
#    - Commercial redistribution of image assets and brand descriptions.
# ==============================================================================

class HttpAggregatorClient:
    """HTTP client communicating with the external product aggregator API."""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_raw_listings(self, stores: list[str]) -> list[dict[str, Any]]:
        """Fetch raw listings from HTTP REST endpoint."""
        endpoint = f"{self.base_url}/v1/products"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "FashionAutopost/1.0",
        }
        params = {"stores": ",".join(stores), "limit": 100}

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(endpoint, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                # Normalize response envelope if wrapped in {"data": [...]}
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    return data["data"]
                elif isinstance(data, list):
                    return data
                else:
                    logger.warning("Unexpected aggregator response shape: %s", type(data))
                    return []
        except httpx.HTTPError as exc:
            logger.error("Aggregator HTTP request failed: %s", exc)
            raise
