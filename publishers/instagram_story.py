"""Instagram story collage: the same product photos as the feed post, with the boutique card on top.

The story is a 9:16 JPEG. Black cards carry white italic copy — the name,
price, size, color and a short description, plus «Европейское качество» — in
Instagram's «Elegant» story lettering. Both the card stack and the link pill
are placed over the calmest part of the collage so the garment itself stays
visible, the way the boutique lays out its own stories. Instagram draws nothing
for a link sticker posted through the API, so the pill is painted into the
image and the tappable sticker is placed exactly over it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from core.pricing import whole_price
from publishers.instagram_media import _cover_rgb, _open_rgb, save_publish_jpeg

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
LINK_LABEL = "посмотреть подробнее фото"
LINK_PILL_FONT_SIZE = 40
LINK_PILL_HEIGHT = 96
LINK_PILL_BOTTOM = STORY_HEIGHT - 240
DEFAULT_QUALITY_LINE = "Европейское качество"

# Instagram's own chrome — the avatar row on top, the «Отправить сообщение»
# bar below — eats into the frame, so nothing is drawn outside this band.
SAFE_TOP = 300
SAFE_BOTTOM = 1700
SAFE_MARGIN = 48

CARD_BG = (0, 0, 0)
CARD_FG = (255, 255, 255)
CARD_MAX_TEXT_WIDTH = 720
CARD_FONT_MAX = 56
CARD_FONT_MIN = 38
CARD_PAD_X = 44
CARD_PAD_Y = 16
CARD_GAP = 22

# Where the last rendered collage painted its link pill, so the tappable
# sticker can be laid over it; see link_sticker_area().
_LAST_PILL_BOX: tuple[int, int, int, int] | None = None

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
# Instagram paints its link sticker in a plain sans, not in the story font.
_SANS_CANDIDATES = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    _FONT_DIR / "CormorantGaramond-Medium.ttf",
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


def story_link_url(telegram_links: Sequence[str], footer: str | None) -> str:
    """The product's own post in the boutique channel, or the channel when it is unknown."""
    channel = telegram_channel_url(footer)
    prefix = channel.rstrip("/").casefold() + "/"
    for url in telegram_links:
        if url.casefold().startswith(prefix):
            return url
    return channel


def link_sticker_area() -> tuple[float, float, float, float]:
    """Center x, center y, width and height of the painted link pill, as frame fractions.

    The pill moves with the photos, so this reports where the last collage put
    it. Publishers render and then read it back before uploading.
    """
    left, top, right, bottom = _LAST_PILL_BOX or _default_pill_box()
    return (
        (left + right) / 2 / STORY_WIDTH,
        (top + bottom) / 2 / STORY_HEIGHT,
        (right - left) / STORY_WIDTH,
        (bottom - top) / STORY_HEIGHT,
    )


def render_story_collage(
    sources: list[Path],
    dest: Path,
    *,
    title: str,
    price_label: str,
    description: str,
    quality_line: str,
) -> Path:
    """Paint a 1080×1920 story from the same photos that go into the feed post."""
    global _LAST_PILL_BOX

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
    busyness = _Busyness(canvas)
    cards = story_cards(title, price_label, description, quality_line)

    font, blocks, stack_width, stack_height = _fit_cards(draw, cards)
    stack_box = _place(busyness, stack_width, stack_height)
    _draw_cards(draw, blocks, font, stack_box)

    pill_box = _place(busyness, _pill_width(), LINK_PILL_HEIGHT, avoid=stack_box)
    _draw_link_pill(draw, pill_box)
    _LAST_PILL_BOX = pill_box

    dest.parent.mkdir(parents=True, exist_ok=True)
    save_publish_jpeg(canvas, dest)
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
        return _cover_rgb(_open_rgb(image), width, height)


class _Busyness:
    """How crowded any rectangle of the collage is, from 0 (flat) to 1 (noisy).

    Edge strength is averaged into a coarse grid and summed into an integral
    image, so every candidate placement costs one lookup. Garments and faces
    carry detail; walls, floors and studio backdrops do not, which is what
    keeps the cards off the product.
    """

    CELL = 20

    def __init__(self, canvas: Image.Image) -> None:
        self.cols = STORY_WIDTH // self.CELL
        self.rows = STORY_HEIGHT // self.CELL
        gray = canvas.convert("L").resize(
            (STORY_WIDTH // 4, STORY_HEIGHT // 4), Image.Resampling.BILINEAR
        )
        edges = gray.filter(ImageFilter.FIND_EDGES).resize(
            (self.cols, self.rows), Image.Resampling.BOX
        )
        cells = edges.tobytes()
        # Integral image with a zero row and column, so sums never special-case the border.
        self._sums = [[0] * (self.cols + 1) for _ in range(self.rows + 1)]
        for row in range(self.rows):
            running = 0
            for col in range(self.cols):
                running += cells[row * self.cols + col]
                self._sums[row + 1][col + 1] = self._sums[row][col + 1] + running

    def of(self, box: tuple[int, int, int, int]) -> float:
        left, top, right, bottom = box
        x0 = max(0, min(self.cols, left // self.CELL))
        y0 = max(0, min(self.rows, top // self.CELL))
        x1 = max(x0 + 1, min(self.cols, -(-right // self.CELL)))
        y1 = max(y0 + 1, min(self.rows, -(-bottom // self.CELL)))
        total = (
            self._sums[y1][x1]
            - self._sums[y0][x1]
            - self._sums[y1][x0]
            + self._sums[y0][x0]
        )
        return total / ((x1 - x0) * (y1 - y0) * 255)


def _fit_cards(
    draw: ImageDraw.ImageDraw, cards: list[str]
) -> tuple[ImageFont.FreeTypeFont, list[list[str]], int, int]:
    """Largest Elegant size whose card stack still fits between the safe margins."""
    size = CARD_FONT_MAX
    while True:
        font = _load_font(size, italic=True)
        blocks = [_wrap(card, font, CARD_MAX_TEXT_WIDTH, draw) for card in cards]
        width, height = _stack_size(draw, blocks, font)
        if height <= SAFE_BOTTOM - SAFE_TOP or size <= CARD_FONT_MIN:
            return font, blocks, width, height
        size -= 2


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    return font.size + max(8, font.size // 3)


def _card_size(
    draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    widths = [draw.textlength(line, font=font) for line in lines]
    width = int(max(widths) if widths else 0) + 2 * CARD_PAD_X
    return width, _line_height(font) * len(lines) + 2 * CARD_PAD_Y


def _stack_size(
    draw: ImageDraw.ImageDraw, blocks: list[list[str]], font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    width = 0
    height = -CARD_GAP
    for lines in blocks:
        card_w, card_h = _card_size(draw, lines, font)
        width = max(width, card_w)
        height += card_h + CARD_GAP
    return width, max(0, height)


def _place(
    busyness: _Busyness,
    width: int,
    height: int,
    *,
    avoid: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Slide the block down the three gutter alignments and keep the calmest spot.

    Horizontal alignment stays on one of three rails so the frame still reads
    as a designed layout; vertically the block is free to settle wherever the
    garment is not, with a nudge towards the lower half the boutique favours.
    """
    width = min(width, STORY_WIDTH - 2 * SAFE_MARGIN)
    height = min(height, SAFE_BOTTOM - SAFE_TOP)
    rails = (
        (SAFE_MARGIN, 0.012),
        ((STORY_WIDTH - width) // 2, 0.0),
        (STORY_WIDTH - SAFE_MARGIN - width, 0.006),
    )
    lowest = SAFE_BOTTOM - height
    travel = max(1, lowest - SAFE_TOP)

    best_box = (rails[1][0], lowest, rails[1][0] + width, SAFE_BOTTOM)
    best_score = float("inf")
    for top in range(SAFE_TOP, lowest + 1, 32):
        drop_bias = 0.030 * (lowest - top) / travel
        for left, rail_bias in rails:
            box = (left, top, left + width, top + height)
            if avoid is not None and _overlaps(box, avoid, margin=28):
                continue
            score = busyness.of(box) + rail_bias + drop_bias
            if score < best_score:
                best_box, best_score = box, score
    return best_box


def _overlaps(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int], *, margin: int = 0
) -> bool:
    return not (
        a[2] + margin <= b[0]
        or a[0] >= b[2] + margin
        or a[3] + margin <= b[1]
        or a[1] >= b[3] + margin
    )


def _draw_cards(
    draw: ImageDraw.ImageDraw,
    blocks: list[list[str]],
    font: ImageFont.FreeTypeFont,
    stack: tuple[int, int, int, int],
) -> None:
    center_x = (stack[0] + stack[2]) / 2
    top = stack[1]
    for lines in blocks:
        top = _draw_card(draw, lines, font, center_x, top) + CARD_GAP


def _draw_card(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    center_x: float,
    top: int,
) -> int:
    box_width, box_height = _card_size(draw, lines, font)
    left = center_x - box_width / 2
    draw.rectangle((left, top, left + box_width, top + box_height), fill=CARD_BG)
    line_height = _line_height(font)
    y = top + CARD_PAD_Y
    for line in lines:
        line_width = draw.textlength(line, font=font)
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text((center_x - line_width / 2, y - bbox[1]), line, font=font, fill=CARD_FG)
        y += line_height
    return top + box_height


def _pill_width() -> int:
    return int(_load_font(LINK_PILL_FONT_SIZE, italic=False).getlength(LINK_LABEL)) + 150


def _default_pill_box() -> tuple[int, int, int, int]:
    width = _pill_width()
    left = (STORY_WIDTH - width) // 2
    return left, LINK_PILL_BOTTOM - LINK_PILL_HEIGHT, left + width, LINK_PILL_BOTTOM


def _draw_link_pill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    font = _load_font(LINK_PILL_FONT_SIZE, italic=False)
    left, top, right, bottom = box
    height = bottom - top
    draw.rounded_rectangle((left, top, right, bottom), radius=height // 2, fill=(255, 255, 255))

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
