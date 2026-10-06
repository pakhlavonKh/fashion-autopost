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


def _shoe(external_id: str = "mango-37096012") -> RawProduct:
    return RawProduct(
        external_id=external_id,
        source="mango",
        title="Укороченные брюки с высокой посадкой",
        price=Decimal("39.99"),
        currency="EUR",
        photo_url="https://media.mango.com/is/image/punto/37096012-99-001",
        product_url="https://shop.mango.com/it/it/p/donna/pantaloni/casual/pantaloni-crop/37096012/99/01",
        in_stock=True,
    )


def test_product_already_in_the_channel_is_never_offered_again(tmp_path) -> None:
    """Instagram can fail after Telegram published. That must not cost a second channel post.

    Such a product keeps status 'failed', and the Instagram backlog cycle retries it.
    The publishing cycle must not pick it as the post of the hour.
    """
    from storage.repository import SqlAlchemyProductRepository

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'channel.db'}")
    product = _shoe()
    repo.upsert_new(product)
    repo.update_platform_post_id(product.external_id, "telegram", "-1001246015920:205778")
    repo.mark_failed(product.external_id, "instagram publish failed: image host returned 412")

    assert filter_unseen([product], repo) == []


def test_failed_product_without_a_channel_post_stays_eligible(tmp_path) -> None:
    """A product that never reached the channel must still get its chance."""
    from storage.repository import SqlAlchemyProductRepository

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'retry.db'}")
    product = _shoe("mango-37096013")
    repo.upsert_new(product)
    repo.mark_failed(product.external_id, "telegram publish failed: network timeout")

    assert [p.external_id for p in filter_unseen([product], repo)] == ["mango-37096013"]


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


def test_another_colour_of_the_same_model_is_a_new_post(fake_repo: FakeProductRepository) -> None:
    """Black and beige are different links. The beige one must not be called a repeat."""
    from core.dedup import extract_duplicate_signatures
    from adapters.scrapers.base import generate_deterministic_id

    black_url = "https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/99"
    beige_url = "https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/01"
    assert generate_deterministic_id("mango", url=black_url) == "mango-37016751-99"
    assert generate_deterministic_id("mango", url=beige_url) == "mango-37016751-01"
    assert not (
        extract_duplicate_signatures("mango", "mango-37016751-99", black_url, "Slingback")
        & extract_duplicate_signatures("mango", "mango-37016751-01", beige_url, "Slingback")
    )

    published = RawProduct(
        external_id="mango-37016751-99",
        source="mango",
        title="Slingback black",
        price=Decimal("49.99"),
        currency="EUR",
        photo_url="https://media.mango.com/is/image/punto/37016751-99-001",
        product_url=black_url,
        in_stock=True,
    )
    beige = RawProduct(
        external_id="mango-37016751-01",
        source="mango",
        title="Slingback beige",
        price=Decimal("49.99"),
        currency="EUR",
        photo_url="https://media.mango.com/is/image/punto/37016751-01-001",
        product_url=beige_url,
        in_stock=True,
    )
    fake_repo.upsert_new(published)
    fake_repo.mark_published("mango-37016751-99", "tg-1", None)
    assert [item.external_id for item in filter_unseen([beige], fake_repo)] == ["mango-37016751-01"]


def test_same_colour_is_still_a_duplicate_including_zara_v1() -> None:
    from core.dedup import extract_duplicate_signatures, normalize_url

    black = "https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/99?c=99"
    black_again = "https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/99/00"
    assert extract_duplicate_signatures("mango", "mango-37016751-99", black, "") & extract_duplicate_signatures(
        "mango", "mango-37016751-99", black_again, ""
    )

    red = "https://www.zara.com/es/es/slingback-p02756113.html?v1=111&utm_source=ig"
    red_clean = "https://www.zara.com/es/es/slingback-p02756113.html?v1=111"
    blue = "https://www.zara.com/es/es/slingback-p02756113.html?v1=222"
    assert normalize_url(red) == normalize_url(red_clean)
    assert not (
        extract_duplicate_signatures("zara", "zara-02756113-111", red, "")
        & extract_duplicate_signatures("zara", "zara-02756113-222", blue, "")
    )
    assert extract_duplicate_signatures("zara", "zara-02756113-111", red, "") & extract_duplicate_signatures(
        "zara", "zara-02756113-111", red_clean, ""
    )

