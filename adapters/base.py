"""Source adapter interfaces and domain transfer objects (DTOs).

Per SDD §3.1 and SRS FR-1.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RawProduct:
    """Normalized product data transfer object from any aggregator or source."""
    external_id: str
    source: str             # e.g., "zara", "mango"
    title: str
    price: Decimal
    currency: str
    photo_url: str          # may need re-hosting later for Instagram if not public HTTPS
    product_url: str
    in_stock: bool
    photo_urls: list[str] = field(default_factory=list)
    original_product_url: str | None = None
    heel_height: str | None = None
    # Color name and the full size grid, copied from the product page.
    color: str | None = None
    sizes: tuple[str, ...] = ()
    # True when photo_urls is the store's own main gallery for this product,
    # already isolated, so the reference filter must not trim it again.
    photos_verified: bool = False
    # Every colourway the page offers (core.color_variants.ColorVariant), the
    # one this link opens marked as selected. Empty for single-colour products.
    color_variants: tuple[Any, ...] = ()


@runtime_checkable
class SourceAdapter(Protocol):
    """Interface for pulling product listings from external data sources."""

    def fetch_products(self) -> list[RawProduct]:
        """Retrieve and normalize candidate product records from this source."""
        ...
