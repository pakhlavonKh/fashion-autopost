"""Channel style profile and product similarity ranking.

Performs analysis of products previously published to the Telegram channel
to identify style patterns, category distribution, and pricing affinity.
Uses this intelligence to rank future product candidates for selection.
"""

from collections import Counter
from decimal import Decimal
import logging
import re
from typing import Any

from adapters.base import RawProduct
from storage.models import ProductRecord
from storage.repository import ProductRepository

logger = logging.getLogger(__name__)


def extract_keywords(text: str) -> set[str]:
    """Tokenize and return fashion-relevant keywords."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", text.lower())
    stop_words = {
        "and", "the", "for", "with", "this", "that", "from", "item", "product",
        "для", "это", "как", "так", "все", "при", "под", "над", "размеры", "цена",
    }
    return {w for w in words if w not in stop_words}


class ChannelProfile:
    """Statistical profile of products currently published in the Telegram channel."""

    def __init__(self, recent_products: list[ProductRecord]) -> None:
        self.total_sample = len(recent_products)
        self.categories: Counter[str] = Counter()
        self.keywords: Counter[str] = Counter()
        self.brands: Counter[str] = Counter()
        self.prices: list[float] = []

        for p in recent_products:
            cat = (p.category or "").strip().lower()
            if cat:
                self.categories[cat] += 1
            if p.source:
                self.brands[p.source.lower()] += 1
            if p.price_original:
                self.prices.append(float(p.price_original))
            kws = extract_keywords(f"{p.title} {p.description}")
            self.keywords.update(kws)

        self.median_price = (
            sorted(self.prices)[len(self.prices) // 2] if self.prices else 50.0
        )
        self.top_keywords = {w for w, _ in self.keywords.most_common(40)}

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.total_sample,
            "top_categories": [cat for cat, _ in self.categories.most_common(5)],
            "top_keywords": list(self.top_keywords)[:15],
            "median_price": self.median_price,
        }


def analyze_channel_history(repo: ProductRepository, limit: int = 50) -> ChannelProfile:
    """Analyze the products published to Telegram channel."""
    records = repo.get_recent_published_products(limit=limit)
    profile = ChannelProfile(records)
    logger.info(
        "Analyzed %d recently published channel products: top categories=%s, median_price=%.1f",
        profile.total_sample,
        [c for c, _ in profile.categories.most_common(3)],
        profile.median_price,
    )
    return profile


def score_product_similarity(product: RawProduct, profile: ChannelProfile) -> float:
    """Calculate a compatibility score (0.0 to 1.0) of candidate against channel profile."""
    if profile.total_sample == 0:
        return 0.5  # Neutral baseline when history is empty

    score = 0.0

    # 1. Category alignment (weight: 0.35)
    prod_cat = getattr(product, "category", "") or ""
    if prod_cat and prod_cat.lower() in profile.categories:
        freq = profile.categories[prod_cat.lower()] / profile.total_sample
        score += min(0.35, freq * 0.7 + 0.15)
    else:
        score += 0.05

    # 2. Keyword overlap in style/material/cut (weight: 0.40)
    kws = extract_keywords(product.title)
    overlap = len(kws & profile.top_keywords)
    if overlap > 0:
        score += min(0.40, overlap * 0.10)

    # 3. Price proximity to channel median (weight: 0.25)
    try:
        p_val = float(product.price)
        diff_ratio = abs(p_val - profile.median_price) / (profile.median_price or 50.0)
        price_score = max(0.0, 0.25 * (1.0 - min(diff_ratio, 1.0)))
        score += price_score
    except Exception:
        score += 0.10

    return min(1.0, score)


def rank_products_by_channel_similarity(
    candidates: list[RawProduct],
    repo: ProductRepository,
    max_price_usd: Decimal | None = None,
) -> list[tuple[RawProduct, float]]:
    """Filter by brand pause and max price, then rank candidates by similarity to channel profile."""
    paused_brands = repo.get_paused_brands()
    profile = analyze_channel_history(repo, limit=50)

    scored: list[tuple[RawProduct, float]] = []
    for cand in candidates:
        # 1. Respect Brand pause settings
        if cand.source.lower() in paused_brands:
            logger.debug("Candidate %s skipped: brand '%s' is paused", cand.external_id, cand.source)
            continue

        # 2. Respect Maximum Price
        if max_price_usd is not None and cand.price > max_price_usd:
            logger.debug(
                "Candidate %s skipped: price %s > max %s",
                cand.external_id,
                cand.price,
                max_price_usd,
            )
            continue

        similarity = score_product_similarity(cand, profile)
        scored.append((cand, similarity))

    # Sort descending by similarity score
    scored.sort(key=lambda x: x[1], reverse=True)
    logger.info(
        "Ranked %d candidates by channel similarity (highest score: %.2f, lowest: %.2f)",
        len(scored),
        scored[0][1] if scored else 0.0,
        scored[-1][1] if scored else 0.0,
    )
    return scored
