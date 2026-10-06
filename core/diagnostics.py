"""What the bot reads from one product link, for checking a store on the server.

    python main.py --inspect-url "https://shop.mango.com/.../37066365/75/00"

prints the title, price, colour, sizes, every colourway with its link and the
photos that would be posted, and saves the page under data/diag_pages/ so the
exact HTML the store served can be looked at later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlsplit

DIAG_DIR = Path("data/diag_pages")


def inspect_product_url(url: str, save_dir: Path = DIAG_DIR) -> str:
    from adapters.playwright_url_processor import clean_url_parameters
    from adapters.product_page import fetch_product_html, parse_product_html
    from core.color_variants import extract_color_variants
    from core.page_data import flight_text, unescaped_text
    from core.product_facts import extract_site_facts

    lines: list[str] = [f"Ссылка: {url}"]
    start_url = clean_url_parameters(url.strip())
    final_url, html_text = fetch_product_html(start_url)
    lines.append(f"Открыта страница: {final_url}")
    lines.append(f"Размер HTML: {len(html_text or '')} символов")
    if not html_text:
        lines.append("Магазин не отдал страницу.")
        return "\n".join(lines)

    save_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    host = re.sub(r"[^a-z0-9]+", "-", urlsplit(final_url).netloc.lower()).strip("-")
    saved = save_dir / f"{host}-{stamp}.html"
    saved.write_text(html_text, encoding="utf-8")
    lines.append(f"HTML сохранён: {saved}")

    hidden = flight_text(html_text)
    lines.append(
        "Данные страницы: "
        f"__next_f={'да' if '__next_f' in html_text else 'нет'} "
        f"(расшифровано {len(hidden)} символов), "
        f"__NEXT_DATA__={'да' if '__NEXT_DATA__' in html_text else 'нет'}, "
        f"ld+json={'да' if 'application/ld+json' in html_text else 'нет'}, "
        f"\"colors\" в HTML={html_text.count(chr(34) + 'colors' + chr(34))}, "
        f"в расшифровке={hidden.count(chr(34) + 'colors' + chr(34))}, "
        f"экранированных={'да' if unescaped_text(html_text) else 'нет'}"
    )

    color, sizes = extract_site_facts(html_text, final_url)
    lines.append(f"Цвет на странице: {color or '—'}")
    lines.append(f"Размеры: {' '.join(sizes) if sizes else '—'}")

    product = parse_product_html(html_text, final_url)
    if product is None:
        lines.append("Название, цену или фото со страницы прочитать не удалось.")
        variants = extract_color_variants(html_text, final_url, current_color=color)
    else:
        lines.append(f"Товар: {product.title} — {product.price} {product.currency} (id {product.external_id})")
        variants = list(product.color_variants)
        lines.append(f"Фото для поста: {len(product.photo_urls)}")
        lines.extend(f"  {index + 1}. {photo}" for index, photo in enumerate(product.photo_urls))

    if variants:
        lines.append(f"Цветов: {len(variants)}")
        for item in variants:
            mark = " ← по ссылке" if item.selected else ""
            lines.append(f"  • {item.name} [{item.code}] {item.url}{mark}")
    else:
        lines.append("Выбора цвета на странице не найдено.")
    return "\n".join(lines)
