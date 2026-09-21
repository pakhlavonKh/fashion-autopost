"""Post composition module.

Per SDD §3.5 and SRS FR-4.
Constructs a platform-agnostic ComposedPost containing photo URL, copy, price, and link.
"""

from dataclasses import dataclass
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
        f"🏷 Price: {price_str}",
    ]

    link_to_include = product.product_url if (include_link and product.product_url) else None
    if link_to_include:
        lines.extend(["", f"🔗 Product Link: {link_to_include}"])

    text = "\n".join(lines)

    return ComposedPost(
        photo_url=product.photo_url,
        text=text,
        price=price,
        currency=target_currency.upper(),
        product_url=link_to_include,
        title=clean_title,
        source=product.source,
    )
