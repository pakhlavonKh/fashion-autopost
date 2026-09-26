"""Post composition module.

Per SDD §3.5 and SRS FR-4.
Constructs a platform-agnostic ComposedPost containing photo URL, copy, price, and link.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from adapters.base import RawProduct


@dataclass(frozen=True)
class ComposedPost:
    """Platform-agnostic representation of a post ready for social publishing."""
    photo_url: str
    text: str
    price: Decimal
    currency: str
    product_url: str | None
    title: str
    source: str
    photo_urls: list[str] = field(default_factory=list)


def compose_post(
    product: RawProduct,
    description: str,
    price: Decimal,
    target_currency: str = "USD",
    include_link: bool = True,
) -> ComposedPost:
    """Compose a clean, platform-agnostic post text from product details and AI copy."""
    clean_desc = description.strip()
    clean_title = product.title.strip()
    brand = product.source.upper()

    # Form formatted price string, e.g. "$104.90" or "104.90 USD"
    symbol = "$" if target_currency.upper() == "USD" else f"{target_currency.upper()} "
    price_str = f"{symbol}{price:,.2f}"

    lines = [
        f"✨ {clean_title} | {brand}",
        "",
        clean_desc,
        "",
        f"🏷 Цена: {price_str}",
    ]

    link_to_include = product.product_url if (include_link and product.product_url) else None
    if link_to_include:
        lines.extend(["", f"🔗 Ссылка на товар: {link_to_include}"])

    text = "\n".join(lines)

    photos = list(product.photo_urls) if getattr(product, "photo_urls", None) else []
    if not photos and product.photo_url:
        photos = [product.photo_url]

    return ComposedPost(
        photo_url=product.photo_url,
        text=text,
        price=price,
        currency=target_currency.upper(),
        product_url=link_to_include,
        title=clean_title,
        source=product.source,
        photo_urls=photos,
    )
