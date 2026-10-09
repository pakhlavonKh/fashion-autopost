"""What the bot reads from one product link, for checking a store on the server.

    python main.py --inspect-url "https://shop.mango.com/.../37066365/75/00"

prints the title, price, colour, sizes, every colourway with its link and the
photos that would be posted, downloads each photo in every form the bot may
try and says what came back (a picture, an empty picture, an error), and saves
the page under data/diag_pages/ so the exact HTML the store served can be
looked at later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
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
    shadow_roots = html_text.count("data-shadow-host=")
    store_answers = html_text.count('data-bot-source="store-api"')
    lines.append(
        f"Прочитано в Chrome: скрытых деревьев компонентов={shadow_roots}, ответов с данными магазина={store_answers}"
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
        lines.extend(probe_photos(product.photo_urls))
        lines.extend(post_selection(product))

    if variants:
        lines.append(f"Цветов: {len(variants)}")
        for item in variants:
            mark = " ← по ссылке" if item.selected else ""
            lines.append(f"  • {item.name} [{item.code}] {item.url}{mark}")
    else:
        lines.append("Выбора цвета на странице не найдено.")
    return "\n".join(lines)


def probe_photos(photo_urls: list[str], limit: int = 30) -> list[str]:
    """What the store sends for each photo link, in each form the downloader tries."""
    import io

    import httpx
    from PIL import Image

    from core.image_downloader import BROWSER_HEADERS, download_candidates, is_blank_image

    lines = ["Проверка фото (как их скачает бот):"]
    usable = 0
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        for index, url in enumerate(photo_urls[:limit], start=1):
            verdicts: list[str] = []
            good = False
            for candidate in download_candidates(url):
                if candidate == url:
                    label = "как на странице"
                elif candidate == url.split("?")[0]:
                    label = "без параметров"
                else:
                    label = "крупная"
                try:
                    response = client.get(candidate)
                except Exception as exc:
                    verdicts.append(f"{label}: ошибка {type(exc).__name__}")
                    continue
                kind = response.headers.get("content-type", "?").split(";")[0]
                try:
                    with Image.open(io.BytesIO(response.content)) as image:
                        image.load()
                        described = f"{image.format} {image.mode} {image.width}×{image.height}"
                except Exception:
                    verdicts.append(f"{label}: HTTP {response.status_code} {kind}, не картинка ({len(response.content)} байт)")
                    continue
                with tempfile.NamedTemporaryFile(suffix=".img") as handle:
                    handle.write(response.content)
                    handle.flush()
                    blank = is_blank_image(Path(handle.name))
                verdicts.append(
                    f"{label}: HTTP {response.status_code} {described}, {len(response.content) // 1024} КБ"
                    + (", ПУСТАЯ" if blank else "")
                )
                if response.is_success and not blank:
                    good = True
                    break
            usable += good
            lines.append(f"  {index}. {'ок' if good else 'НЕТ ФОТО'} — " + "; ".join(verdicts))
    lines.append(f"Годных фото: {usable} из {min(len(photo_urls), limit)}")
    return lines


_OUTCOMES = {
    "kept": "в посте",
    "failed": "не скачалось",
    "blank": "пустая картинка",
    "swatch": "отсеяно как образец ткани",
    "duplicate": "отсеяно как дубль другого фото",
}


def post_selection(product) -> list[str]:
    """The photos a post of this product would carry: the same steps as publishing, without posting."""
    from core.image_downloader import ImageDownloader
    from core.pipeline import post_photo_urls

    urls = post_photo_urls(product)
    lines = [f"Отбор фото для поста (как при публикации): ссылок {len(urls)}"]
    with tempfile.TemporaryDirectory() as folder:
        downloader = ImageDownloader(dest_dir=Path(folder))
        kept = downloader.download_all(urls, external_id="inspect")
        for index, (url, outcome) in enumerate(downloader.last_report, start=1):
            name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            lines.append(f"  {index}. {_OUTCOMES.get(outcome, outcome)} — {name}")
    album = min(len(kept), 10)
    lines.append(f"В пост попадёт фото: {album}" + (f" (из {len(kept)}: альбом Telegram вмещает 10)" if len(kept) > 10 else ""))
    return lines
