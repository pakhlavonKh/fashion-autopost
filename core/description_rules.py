"""Product description rules engine.

Provides modular and reusable description enrichment rules.
Includes specific rule for high-heeled footwear extracting heel height.
"""

from dataclasses import dataclass
import logging
import re
from typing import Protocol, runtime_checkable

from adapters.base import RawProduct

logger = logging.getLogger(__name__)


@runtime_checkable
class DescriptionRule(Protocol):
    """Protocol for a reusable product description modification rule."""

    def apply(self, product: RawProduct, current_description: str) -> str:
        """Inspect product data and return updated description text."""
        ...


class HighHeelDescriptionRule:
    """Rule that detects high-heeled footwear and extracts the heel height."""

    _HEEL_KEYWORDS = (
        "heel",
        "heels",
        "high-heel",
        "high-heeled",
        "high heel",
        "high heeled",
        "stiletto",
        "pumps",
        "topuklu",
        "каблук",
        "каблуке",
        "туфли на каблуке",
        "босоножки на каблуке",
        "сапоги на каблуке",
        "ботильоны на каблуке",
        "шпилька",
        "шпильке",
    )

    _HEEL_HEIGHT_PATTERNS = [
        # Explicit height labels: "Heel height: 8 cm", "Высота каблука: 8,5 см", "Topuk boyu: 8 cm"
        re.compile(
            r"(?:heel\s*height|высота\s*каблука|topuk\s*(?:boyu|yüksekliği))[\s:]*([0-9]+(?:[.,][0-9]+)?\s*(?:cm|см|mm|мм|in|inch(?:es)?|\"))",
            re.IGNORECASE,
        ),
        # Measurement followed by heel: "8 cm heel", "8.5 см каблук", "10 cm high heel"
        re.compile(
            r"([0-9]+(?:[.,][0-9]+)?\s*(?:cm|см|mm|мм))\s*(?:high\s*)?(?:heel|каблук|topuk)",
            re.IGNORECASE,
        ),
        # "heel of 8 cm", "каблук 8 см"
        re.compile(
            r"(?:heel\s*(?:of|is)?|каблук\s*(?:высотой)?)\s*([0-9]+(?:[.,][0-9]+)?\s*(?:cm|см|mm|мм))",
            re.IGNORECASE,
        ),
    ]

    def is_high_heeled(self, product: RawProduct) -> bool:
        """Check if product title, category, or description indicates high-heeled footwear."""
        combined = f"{product.title} {getattr(product, 'category', '') or ''} {product.photo_url} {product.product_url}".lower()
        return any(kw in combined for kw in self._HEEL_KEYWORDS)

    def extract_heel_height(self, product: RawProduct, current_description: str) -> str | None:
        """Extract heel height measurement from available product data.
        
        Returns normalized string like '8 cm' or None if not found. Does not invent a value.
        """
        # If pre-extracted attribute exists on product
        if getattr(product, "heel_height", None):
            raw = str(product.heel_height).strip()
            if raw and any(char.isdigit() for char in raw):
                return raw

        search_corpus = f"{product.title}\n{current_description}"
        for pattern in self._HEEL_HEIGHT_PATTERNS:
            match = pattern.search(search_corpus)
            if match:
                raw_val = match.group(1).strip()
                # Normalize comma to dot or uniform spacing
                return re.sub(r"\s+", " ", raw_val)

        return None

    def apply(self, product: RawProduct, current_description: str) -> str:
        """If product is identified as high-heeled footwear, append 'Heel height: X cm'."""
        if not self.is_high_heeled(product):
            return current_description

        height = self.extract_heel_height(product, current_description)
        if not height:
            # If heel height cannot be determined, do not invent a value
            return current_description

        # Check if already mentioned in description to avoid duplicate lines
        lower_desc = current_description.lower()
        if f"heel height: {height.lower()}" in lower_desc or f"каблук: {height.lower()}" in lower_desc:
            return current_description

        line = f"Heel height: {height}"
        if current_description.strip():
            return f"{current_description.strip()}\n\n{line}"
        return line


# Global default rules list
DEFAULT_DESCRIPTION_RULES: list[DescriptionRule] = [
    HighHeelDescriptionRule(),
]


def apply_description_rules(
    product: RawProduct,
    description: str,
    rules: list[DescriptionRule] | None = None,
) -> str:
    """Apply all configured description enrichment rules in sequence."""
    active_rules = rules if rules is not None else DEFAULT_DESCRIPTION_RULES
    result = description
    for rule in active_rules:
        try:
            result = rule.apply(product, result)
        except Exception as exc:
            logger.warning("Description rule %s failed for %s: %s", type(rule).__name__, product.external_id, exc)
    return result
