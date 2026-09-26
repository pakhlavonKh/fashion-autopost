"""Unit tests for deduplication service.

Per SDD §3.2 and §9.
"""

from decimal import Decimal
from adapters.base import RawProduct
from core.dedup import filter_unseen
from tests.conftest import FakeProductRepository


def test_filter_unseen_all_new(fake_repo: FakeProductRepository, sample_products: list[RawProduct]) -> None:
    """When repo has no published items, all candidate products should be returned."""
    result = filter_unseen(sample_products, fake_repo)
    assert len(result) == 3
    assert [p.external_id for p in result] == ["p-1", "p-2", "p-3"]


def test_filter_unseen_drops_published(fake_repo: FakeProductRepository, sample_products: list[RawProduct]) -> None:
    """Products marked published in repo must be dropped from candidates."""
    # Pre-populate repo with p-2 as published
    fake_repo.upsert_new(sample_products[1])
    fake_repo.mark_published("p-2", "tg-102", "ig-202")

    result = filter_unseen(sample_products, fake_repo)
    assert len(result) == 2
    assert [p.external_id for p in result] == ["p-1", "p-3"]


def test_filter_unseen_retains_failed_or_selected(fake_repo: FakeProductRepository, sample_products: list[RawProduct]) -> None:
    """Products in 'failed' or 'selected' status remain eligible unless published."""
    fake_repo.upsert_new(sample_products[0])
    fake_repo.mark_failed("p-1", "Network timeout")

    # p-1 should still be eligible for publication retry
    result = filter_unseen(sample_products, fake_repo)
    assert len(result) == 3
    assert "p-1" in [p.external_id for p in result]


def test_filter_unseen_drops_by_canonical_url(fake_repo: FakeProductRepository) -> None:
    """When a product has a different external_id but matches a published product's canonical URL, it must be dropped."""
    published_item = RawProduct(
        external_id="mango-8957033",  # Old randomized hash ID
        source="mango",
        title="Kruvaze saf yün palto - Siyah",
        price=Decimal("199.99"),
        currency="USD",
        photo_url="https://example.com/photo.jpg",
        product_url="https://shop.mango.com/tr/tr/p/kadın/palto/palto/kruvaze-saf-yun-palto/37016751/99/00?c=99",
        in_stock=True,
    )
    fake_repo.upsert_new(published_item)
    fake_repo.mark_published("mango-8957033", "tg-10", None)

    # Scraped candidate has the new deterministic ID and clean URL
    new_candidate = RawProduct(
        external_id="mango-37016751",  # New deterministic SKU ID
        source="mango",
        title="Kruvaze saf yün palto - Siyah",
        price=Decimal("199.99"),
        currency="USD",
        photo_url="https://example.com/photo.jpg",
        product_url="https://shop.mango.com/tr/tr/p/kadın/palto/palto/kruvaze-saf-yun-palto/37016751/99/00",
        in_stock=True,
    )

    unseen = filter_unseen([new_candidate], fake_repo)
    assert len(unseen) == 0, "Duplicate with matching canonical URL/SKU must be discarded!"


def test_filter_unseen_in_batch_duplicates(fake_repo: FakeProductRepository) -> None:
    """Duplicate items within the same scraped batch must be deduplicated to a single item."""
    p1 = RawProduct(
        external_id="mango-37016751",
        source="mango",
        title="Kruvaze saf yün palto - Siyah",
        price=Decimal("199.99"),
        currency="USD",
        photo_url="https://example.com/photo1.jpg",
        product_url="https://shop.mango.com/tr/tr/p/kadın/palto/palto/kruvaze-saf-yun-palto/37016751/99/00",
        in_stock=True,
    )
    # Duplicate item in the batch (e.g. from variant card or scraper re-fetch)
    p2 = RawProduct(
        external_id="mango-37016751",
        source="mango",
        title="Kruvaze saf yün palto - Siyah",
        price=Decimal("199.99"),
        currency="USD",
        photo_url="https://example.com/photo2.jpg",
        product_url="https://shop.mango.com/tr/tr/p/kadın/palto/palto/kruvaze-saf-yun-palto/37016751/99/00",
        in_stock=True,
    )
    p3 = RawProduct(
        external_id="mango-9999999",
        source="mango",
        title="Different Coat",
        price=Decimal("149.99"),
        currency="USD",
        photo_url="https://example.com/photo3.jpg",
        product_url="https://shop.mango.com/tr/tr/p/kadın/palto/palto/different-coat/9999999/99/00",
        in_stock=True,
    )

    result = filter_unseen([p1, p2, p3], fake_repo)
    assert len(result) == 2
    assert [p.external_id for p in result] == ["mango-37016751", "mango-9999999"]

