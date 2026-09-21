"""Source adapter interfaces and domain transfer objects (DTOs).

Per SDD §3.1 and SRS FR-1.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


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


@runtime_checkable
class SourceAdapter(Protocol):
    """Interface for pulling product listings from external data sources."""

    def fetch_products(self) -> list[RawProduct]:
        """Retrieve and normalize candidate product records from this source."""
        ...
