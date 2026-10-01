"""Post composition module.

Per SDD §3.5 and SRS FR-4.
Constructs a platform-agnostic ComposedPost containing photo URL, copy, price, and link.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from adapters.base import RawProduct
from core.description_rules import apply_description_rules
from core.pricing import whole_price


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
    # external_id, so the story worker can load the Telegram link for this product.
    product_id: str = ""
    photo_urls: list[str] = field(default_factory=list)
    # Public t.me links of this post once Telegram has published it.
    telegram_links: tuple[str, ...] = ()


def compose_post(
    product: RawProduct,
    description: str,
    price: Decimal,
    target_currency: str = "USD",
    include_link: bool = True,
    outfit_info: str | None = None,
) -> ComposedPost:
    """Compose a clean, platform-agnostic post text from product details and AI copy."""
    # Apply reusable description enrichment rules (e.g. high-heel footwear heel height)
    enriched_desc = apply_description_rules(product, description)
    clean_desc = enriched_desc.strip()
    clean_title = product.title.strip()
    brand = product.source.upper()

    # Sale price is always an even whole unit floored down to the closest even number.
    shown_price = whole_price(price)
    symbol = "$" if target_currency.upper() == "USD" else f"{target_currency.upper()} "
    price_str = f"{symbol}{int(shown_price)}"

    lines = [
        f"✨ {clean_title} | {brand}",
    ]
    if outfit_info:
        lines.append(f"👗 Образ дня: {outfit_info}")
    lines.extend([
        "",
        clean_desc,
        "",
        f"🏷 Цена: {price_str}",
    ])

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
        price=shown_price,
        currency=target_currency.upper(),
        product_url=link_to_include,
        title=clean_title,
        source=product.source,
        product_id=product.external_id,
        photo_urls=photos,
    )
