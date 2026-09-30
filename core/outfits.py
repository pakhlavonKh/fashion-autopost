"""Outfit and look coordinator.

Coordinates the selection and sequencing of 2–3 compatible products
(e.g., Top -> Bottom -> Shoes, or Dress -> Shoes -> Accessories)
so they can be published consecutively as a coherent outfit/look.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import uuid

from adapters.base import RawProduct

logger = logging.getLogger(__name__)

# Category taxonomy groups for coherent look composition
CATEGORY_GROUPS = {
    "tops": ["top", "tops", "shirt", "blouse", "sweater", "jumper", "cardigan", "knitwear", "t-shirt", "jacket", "blazer"],
    "bottoms": ["pants", "trousers", "jeans", "skirt", "shorts", "leggings"],
    "dresses": ["dress", "dresses", "jumpsuit", "robe"],
    "shoes": ["shoes", "boots", "sandals", "sneakers", "heels", "loafers", "mules"],
    "accessories": ["accessories", "bag", "handbag", "belt", "scarf", "hat"],
}


def classify_product_group(product: RawProduct) -> str:
    """Classify product into one of the look component groups."""
    corpus = f"{product.title} {getattr(product, 'category', '') or ''}".lower()
    for group, keywords in CATEGORY_GROUPS.items():
        if any(kw in corpus for kw in keywords):
            return group
    return "other"


@dataclass
class CoordinatedOutfit:
    """A coherent outfit consisting of 2 to 3 complementary products."""
    outfit_id: str
    items: list[RawProduct]
    theme: str = "Daily Look"
    positions: list[str] = field(default_factory=list)


class OutfitCoordinator:
    """Builds and sequences coordinated looks from candidate products."""

    @classmethod
    def find_coordinated_outfit(
        cls,
        candidates: list[RawProduct],
    ) -> CoordinatedOutfit | None:
        """Find 2 to 3 compatible products that form a complete look."""
        grouped: dict[str, list[RawProduct]] = {k: [] for k in CATEGORY_GROUPS}
        grouped["other"] = []

        for p in candidates:
            grp = classify_product_group(p)
            grouped[grp].append(p)

        # Combo Type 1: Top -> Bottom -> Shoes
        if grouped["tops"] and grouped["bottoms"] and grouped["shoes"]:
            top = grouped["tops"][0]
            bottom = grouped["bottoms"][0]
            shoes = grouped["shoes"][0]
            outfit_id = f"outfit_{uuid.uuid4().hex[:8]}"
            logger.info("Created 3-piece outfit: Top (%s) + Bottom (%s) + Shoes (%s)", top.external_id, bottom.external_id, shoes.external_id)
            return CoordinatedOutfit(
                outfit_id=outfit_id,
                items=[top, bottom, shoes],
                theme="Total Look",
                positions=["Топ / Верх", "Низ / Брюки", "Обувь"],
            )

        # Combo Type 2: Dress -> Shoes -> Accessories
        if grouped["dresses"] and grouped["shoes"] and grouped["accessories"]:
            dress = grouped["dresses"][0]
            shoes = grouped["shoes"][0]
            acc = grouped["accessories"][0]
            outfit_id = f"outfit_{uuid.uuid4().hex[:8]}"
            logger.info("Created 3-piece outfit: Dress (%s) + Shoes (%s) + Accessory (%s)", dress.external_id, shoes.external_id, acc.external_id)
            return CoordinatedOutfit(
                outfit_id=outfit_id,
                items=[dress, shoes, acc],
                theme="Evening / Cocktail Look",
                positions=["Платье", "Обувь", "Аксессуар"],
            )

        # 2-piece Combos: Top -> Bottom or Dress -> Shoes
        if grouped["tops"] and grouped["bottoms"]:
            top = grouped["tops"][0]
            bottom = grouped["bottoms"][0]
            outfit_id = f"outfit_{uuid.uuid4().hex[:8]}"
            logger.info("Created 2-piece outfit: Top (%s) + Bottom (%s)", top.external_id, bottom.external_id)
            return CoordinatedOutfit(
                outfit_id=outfit_id,
                items=[top, bottom],
                theme="Casual Pair Look",
                positions=["Топ / Верх", "Низ / Брюки"],
            )

        if grouped["dresses"] and grouped["shoes"]:
            dress = grouped["dresses"][0]
            shoes = grouped["shoes"][0]
            outfit_id = f"outfit_{uuid.uuid4().hex[:8]}"
            logger.info("Created 2-piece outfit: Dress (%s) + Shoes (%s)", dress.external_id, shoes.external_id)
            return CoordinatedOutfit(
                outfit_id=outfit_id,
                items=[dress, shoes],
                theme="Chic Look",
                positions=["Платье", "Обувь"],
            )

        return None
