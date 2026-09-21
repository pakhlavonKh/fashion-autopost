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
