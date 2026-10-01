"""Product description rules engine.

Heel height is copied from the product page. The caption text is never
searched for a number, so a missing measurement stays missing.
"""

import logging
from typing import Protocol, runtime_checkable

from adapters.base import RawProduct
from core.product_facts import heel_caption_line, is_heeled_footwear

logger = logging.getLogger(__name__)


@runtime_checkable
class DescriptionRule(Protocol):
    """Protocol for a reusable product description modification rule."""

    def apply(self, product: RawProduct, current_description: str) -> str:
        """Inspect product data and return updated description text."""
        ...


class HighHeelDescriptionRule:
    """Append the heel height already taken from the product page."""

    def apply(self, product: RawProduct, current_description: str) -> str:
        """Add the site's heel height for heeled shoes. Never invent one."""
        if not is_heeled_footwear(product.title, product.product_url or ""):
            return current_description
        line = heel_caption_line(getattr(product, "heel_height", None))
        if not line:
            return current_description
        if "высота каблука" in current_description.casefold():
            return current_description
        if current_description.strip():
            return f"{current_description.strip()}\n{line}"
        return line


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
