"""Instagram story collage: the same product photos as the feed post, with the boutique card on top.

The story is a 9:16 JPEG. White italic cards carry the name, price, size, color
and a short description, plus «Европейское качество». A link-style pill sits at
the bottom, matching the story layout used by the boutique.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from core.pricing import whole_price

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
LINK_LABEL = "посмотреть подробнее фото"
DEFAULT_QUALITY_LINE = "Европейское качество"

# Highlight titles stay within Instagram's 16-character limit and match the
# circles already used on the account: Обувь, Верхняя одежда, Сумки, Брюки,
# Шорты, Верх+низ, Платья.
_HIGHLIGHT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Шорты", ("шорт", "бермуд", "shorts", "bermuda")),
    ("Обувь", (
        "обув", "туфл", "ботин", "сапог", "кроссов", "лофер", "мюл", "босонож",
        "сандал", "кеды", "сабо", "мокасин", "shoe", "boot", "sneaker", "loafer",
        "sandal", "heel", "mule",
    )),
    ("Сумки", ("сумк", "клатч", "шопер", "tote", "clutch", "handbag", "bag")),
    ("Верх+низ", ("костюм", "комплект", "двойк", "co-ord", "coord", "matching set")),
    ("Верхняя одежда", (
        "пальто", "куртк", "плащ", "тренч", "пиджак", "жакет", "блейзер", "парк",
        "пуховик", "бомбер", "дублен", "жилет", "coat", "jacket", "blazer",
        "trench", "parka",
    )),
    ("Платья", ("плать", "сарафан", "комбинезон", "dress", "vestido", "robe")),
    ("Юбки", ("юбк", "skirt", "falda")),
    ("Брюки", ("брюк", "джинс", "jeans", "denim", "trouser", "pants", "legging")),
    ("Трикотаж", ("свитер", "джемпер", "кардиган", "трикотаж", "sweater", "cardigan", "knit", "pullover")),
    ("Топы", ("блуз", "рубаш", "футбол", "боди", "blouse", "shirt", "t-shirt", "топ")),
    ("Аксессуары", ("ремень", "шапк", "шарф", "очк", "belt", "scarf", "hat")),
)

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_ITALIC_CANDIDATES = (
    _FONT_DIR / "CormorantGaramond-SemiBoldItalic.ttf",
    Path("C:/Windows/Fonts/timesi.ttf"),
    Path("C:/Windows/Fonts/georgiai.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
)
_SANS_CANDIDATES = (
    _FONT_DIR / "CormorantGaramond-Medium.ttf",
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def detect_highlight(title: str, description: str = "", url: str = "") -> str:
    """Pick the Highlights circle for this garment."""
    text = f"{title}\n{description}\n{url}".casefold()
    for highlight, keywords in _HIGHLIGHT_RULES:
        if _contains_any(text, keywords):
            return highlight
    return "Одежда"


def story_cards(title: str, price_label: str, description: str, quality_line: str) -> list[str]:
    """Split the product card into the stacked white boxes on the story."""
    size_lines: list[str] = []
    detail_lines: list[str] = []
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.casefold()
        if lowered.startswith("размер") or lowered.startswith("цвет"):
            size_lines.append(line)
        else:
            detail_lines.append(line)

    header = f"{title.strip()}-{price_label}"
    first = header if not size_lines else header + "\n" + "\n".join(size_lines)
    cards = [first]
    if detail_lines:
        cards.append("\n".join(detail_lines[:6]))
    cards.append((quality_line or DEFAULT_QUALITY_LINE).strip() or DEFAULT_QUALITY_LINE)
    return cards


def format_story_price(price, currency: str) -> str:
    amount = int(whole_price(price))
    if currency.upper() == "USD":
        return f"{amount}$"
    return f"{amount} {currency.upper()}"


def product_description(post_text: str) -> str:
    """Keep the garment copy and drop the title, price and link blocks."""
    chunks = [part.strip() for part in post_text.split("\n\n") if part.strip()]
    body: list[str] = []
    for chunk in chunks:
        if chunk.startswith(("✨", "🏷", "🔗")):
            continue
        body.append(chunk)
    return "\n\n".join(body).strip()


def quality_line_from_footer(footer: str | None) -> str:
    if footer:
        for line in footer.splitlines():
            if line.strip():
                return line.strip()
    return DEFAULT_QUALITY_LINE


def telegram_channel_url(footer: str | None) -> str:
    """The boutique Telegram channel linked from the story sticker."""
    if footer:
        match = re.search(r"https://t\.me/[A-Za-z0-9_]+", footer)
        if match:
            return match.group(0)
    return "https://t.me/fashionalleyb"


def render_story_collage(
    sources: list[Path],
    dest: Path,
    *,
    title: str,
    price_label: str,
    description: str,
    quality_line: str,
    include_link_pill: bool = True,
) -> Path:
    """Paint a 1080×1920 story from the same photos that go into the feed post."""
    photos = [path for path in sources if path.is_file()][:3]
    if not photos:
        raise RuntimeError("Story collage has no photos")

    canvas = Image.new("RGB", (STORY_WIDTH, STORY_HEIGHT), (255, 255, 255))
    frames = _frames(len(photos), STORY_WIDTH, STORY_HEIGHT)
    ordered = photos if len(photos) == 1 else photos[1:] + photos[:1]
    for frame, path in zip(frames, ordered):
        tile = _cover(path, frame[2], frame[3])
        canvas.paste(tile, (frame[0], frame[1]))

    draw = ImageDraw.Draw(canvas)
    cards = story_cards(title, price_label, description, quality_line)
    _draw_cards(draw, cards)
    if include_link_pill:
        _draw_link_pill(draw)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=90, optimize=True)
    return dest


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    for keyword in keywords:
        if len(keyword) <= 5:
            if re.search(rf"(?<![0-9a-zа-яё]){re.escape(keyword)}", text):
                return True
        elif keyword in text:
            return True
    return False


def _frames(count: int, width: int, height: int) -> list[tuple[int, int, int, int]]:
    gap = 8
    if count <= 1:
        return [(0, 0, width, height)]
    left_w = (width - gap) // 2
    right_x = left_w + gap
    right = (right_x, 0, width - right_x, height)
    if count == 2:
        return [(0, 0, left_w, height), right]
    top_h = int(height * 0.56)
    bottom_y = top_h + gap
    return [
        (0, 0, left_w, top_h),
        (0, bottom_y, left_w, height - bottom_y),
        right,
    ]


def _cover(path: Path, width: int, height: int) -> Image.Image:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        scale = max(width / image.width, height / image.height)
        resized = image.resize(
            (max(width, int(image.width * scale)), max(height, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        left = max(0, (resized.width - width) // 2)
        top = max(0, (resized.height - height) // 2)
        return resized.crop((left, top, left + width, top + height))


def _draw_cards(draw: ImageDraw.ImageDraw, cards: list[str]) -> None:
    max_text_width = 860
    font_size = 48
    while font_size >= 34:
        font = _load_font(font_size, italic=True)
        blocks = [_wrap(card, font, max_text_width, draw) for card in cards]
        height = _stack_height(blocks, font_size)
        if 300 + height < 1500:
            break
        font_size -= 2
    font = _load_font(font_size, italic=True)
    blocks = [_wrap(card, font, max_text_width, draw) for card in cards]
    top = 300
    line_gap = max(8, font_size // 5)
    for lines in blocks:
        top = _draw_card(draw, lines, font, top, line_gap) + 18


def _stack_height(blocks: list[list[str]], font_size: int) -> int:
    line_gap = max(8, font_size // 5)
    line_h = font_size + line_gap
    total = 0
    for lines in blocks:
        total += line_h * len(lines) + 36 + 18
    return total


def _draw_card(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    top: int,
    line_gap: int,
) -> int:
    widths = [draw.textlength(line, font=font) for line in lines]
    text_width = max(widths) if widths else 0
    box_width = int(text_width) + 56
    line_height = font.size + line_gap
    box_height = line_height * len(lines) + 28
    left = (STORY_WIDTH - box_width) // 2
    draw.rectangle((left, top, left + box_width, top + box_height), fill=(255, 255, 255))
    y = top + 14
    for line in lines:
        line_width = draw.textlength(line, font=font)
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((STORY_WIDTH - line_width) / 2, y - bbox[1]), line, font=font, fill=(22, 22, 22))
        y += line_height
    return top + box_height


def _draw_link_pill(draw: ImageDraw.ImageDraw) -> None:
    font = _load_font(34, italic=False)
    text_width = draw.textlength(LINK_LABEL, font=font)
    height = 86
    width = int(text_width) + 130
    bottom = STORY_HEIGHT - 250
    top = bottom - height
    left = (STORY_WIDTH - width) // 2
    draw.rounded_rectangle((left, top, left + width, bottom), radius=height // 2, fill=(255, 255, 255))

    circle_d = height - 18
    circle_x = left + 12
    circle_y = top + 9
    draw.ellipse(
        (circle_x, circle_y, circle_x + circle_d, circle_y + circle_d),
        fill=(92, 164, 214),
    )
    _draw_chain(draw, circle_x + circle_d / 2, circle_y + circle_d / 2)

    bbox = draw.textbbox((0, 0), LINK_LABEL, font=font)
    text_x = circle_x + circle_d + 16
    text_y = top + (height - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((text_x, text_y), LINK_LABEL, font=font, fill=(20, 20, 20))


def _draw_chain(draw: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
    """Two interlocking links, the same mark as Instagram's link sticker."""
    color = (255, 255, 255)
    draw.rounded_rectangle((cx - 16, cy - 6, cx + 1, cy + 6), radius=5, outline=color, width=3)
    draw.rounded_rectangle((cx - 1, cy - 6, cx + 16, cy + 6), radius=5, outline=color, width=3)


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


def _load_font(size: int, *, italic: bool) -> ImageFont.FreeTypeFont:
    for path in (_ITALIC_CANDIDATES if italic else _SANS_CANDIDATES):
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()
