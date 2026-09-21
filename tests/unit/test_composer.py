"""Unit tests for post composer.

Per SDD §3.5 and §9.
"""

from decimal import Decimal
from adapters.base import RawProduct
from core.composer import compose_post


def test_compose_post_with_link() -> None:
    product = RawProduct(
        external_id="sku-100",
        source="zara",
        title="Silk Slip Dress",
        price=Decimal("79.99"),
        currency="EUR",
        photo_url="https://images.example.com/dress.jpg",
        product_url="https://zara.com/dress-100",
        in_stock=True,
    )
    desc = "A timeless silhouette rendered in lustrous silk for effortless evening glamour."
    final_price = Decimal("101.39")

    post = compose_post(
        product=product,
        description=desc,
        price=final_price,
        target_currency="USD",
        include_link=True,
    )

    assert post.photo_url == "https://images.example.com/dress.jpg"
    assert "Silk Slip Dress | ZARA" in post.text
    assert desc in post.text
    assert "$101.39" in post.text
    assert "https://zara.com/dress-100" in post.text
    assert post.price == final_price
    assert post.currency == "USD"


def test_compose_post_without_link() -> None:
    product = RawProduct(
        external_id="sku-200",
        source="mango",
        title="Wool Overcoat",
        price=Decimal("150.00"),
        currency="USD",
        photo_url="https://images.example.com/coat.jpg",
        product_url="https://mango.com/coat-200",
        in_stock=True,
    )
    post = compose_post(
        product=product,
        description="Warm and sophisticated.",
        price=Decimal("165.00"),
        target_currency="USD",
        include_link=False,
    )

    assert "Product Link:" not in post.text
    assert post.product_url is None
