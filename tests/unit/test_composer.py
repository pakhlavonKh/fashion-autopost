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
    assert "$100" in post.text
    assert "$101" not in post.text
    assert "$101.39" not in post.text
    assert "https://zara.com/dress-100" in post.text
    assert post.price == Decimal("100")
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


def test_compose_post_copies_heel_height_from_the_product_page() -> None:
    product = RawProduct(
        external_id="sku-heel",
        source="zara",
        title="Leather high-heel shoes",
        price=Decimal("69.95"),
        currency="EUR",
        photo_url="https://images.example.com/shoe.jpg",
        product_url="https://www.zara.com/es/es/leather-high-heel-shoes-p12345678.html",
        in_stock=True,
        heel_height="9 cm",
    )
    post = compose_post(
        product=product,
        description="Размеры от 36 до 41.\nЦвет: Black.",
        price=Decimal("89"),
        target_currency="USD",
        include_link=True,
    )
    assert post.text.count("Высота каблука") == 1
    assert "Высота каблука: 8 см." in post.text

    again = compose_post(
        product=product,
        description="Размеры от 36 до 41.\nЦвет: Black.\nВысота каблука: 8 см.",
        price=Decimal("89"),
        target_currency="USD",
    )
    assert again.text.count("Высота каблука") == 1
