"""Story collage copy, garment categories, and Highlight filing."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from PIL import Image

from publishers.instagram_highlights import InstagramHighlightClient
from publishers.instagram_story import (
    LINK_LABEL,
    STORY_HEIGHT,
    STORY_WIDTH,
    detect_highlight,
    render_story_collage,
    story_cards,
)


def test_story_cards_follow_the_boutique_layout() -> None:
    cards = story_cards(
        "Платье",
        "78$",
        "Размеры от XS до XL.\nЦвет: темно-синий.\nПлатье с завязкой на спине.\nАсимметричный подол с оборкой.",
        "Европейское качество",
    )
    assert cards == [
        "Платье-78$\nРазмеры от XS до XL.\nЦвет: темно-синий.",
        "Платье с завязкой на спине.\nАсимметричный подол с оборкой.",
        "Европейское качество",
    ]


def test_highlight_category_matches_the_account_circles() -> None:
    cases = {
        "Платье": "Платья",
        "Silk Slip Dress": "Платья",
        "Лоферы": "Обувь",
        "Кожаная сумка": "Сумки",
        "Широкие брюки": "Брюки",
        "Джинсовые шорты": "Шорты",
        "Шерстяное пальто": "Верхняя одежда",
        "Джинсовая куртка": "Верхняя одежда",
        "Костюм-двойка": "Верх+низ",
        "Плиссированная юбка": "Юбки",
    }
    for title, expected in cases.items():
        assert detect_highlight(title) == expected, title
    assert detect_highlight("Шёлковый шарф") == "Аксессуары"
    assert detect_highlight("Новинка недели") == "Одежда"
    assert all(len(name) <= 16 for name in cases.values())


def test_collage_is_a_story_frame() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        colors = ((30, 40, 70), (180, 170, 160), (20, 20, 30))
        photos = []
        for index, color in enumerate(colors):
            path = folder / f"look-{index}.jpg"
            Image.new("RGB", (800, 1200), color).save(path)
            photos.append(path)
        dest = render_story_collage(
            photos,
            folder / "story.jpg",
            title="Платье",
            price_label="78$",
            description="Размеры от XS до XL.\nЦвет: темно-синий.\nПлатье с завязкой на спине.",
            quality_line="Европейское качество",
        )
        with Image.open(dest) as image:
            assert image.size == (STORY_WIDTH, STORY_HEIGHT)
            assert image.format == "JPEG"
            # White card and the link pill are painted onto the photos.
            assert image.getpixel((STORY_WIDTH // 2, 340)) == (255, 255, 255)
            assert image.getpixel((STORY_WIDTH // 2, STORY_HEIGHT - 290)) == (255, 255, 255)
        assert LINK_LABEL


def test_existing_highlight_receives_the_story() -> None:
    client = InstagramHighlightClient("session", "1789")
    with patch("httpx.Client", side_effect=_highlight_factory(existing=True)):
        highlight_id = client.add_story("Платья", "555")
    assert highlight_id == "1800"
    posts = [item for item in _HIGHLIGHT_CALLS if item[0] == "POST"]
    assert posts[0][1].endswith("/highlights/1800/edit_reel/")
    assert "555_1789" in posts[0][2]["added_media_ids"]


def test_missing_highlight_is_created() -> None:
    client = InstagramHighlightClient("session", "1789")
    with patch("httpx.Client", side_effect=_highlight_factory(existing=False)):
        highlight_id = client.add_story("Сумки", "777")
    assert highlight_id == "1900"
    posts = [item for item in _HIGHLIGHT_CALLS if item[0] == "POST"]
    assert posts[0][1].endswith("/highlights/create_reel/")
    assert posts[0][2]["title"] == "Сумки"
    assert "777_1789" in posts[0][2]["media_ids"]


_HIGHLIGHT_CALLS: list[tuple] = []


def _highlight_factory(existing: bool):
    def factory(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client
        cookies: dict[str, str] = {}
        client.cookies.set.side_effect = lambda name, value, domain=None: cookies.__setitem__(name, value)
        client.cookies.get.side_effect = cookies.get

        def get(url, headers=None, **_kw):
            _HIGHLIGHT_CALLS.append(("GET", url, headers))
            response = MagicMock()
            response.is_success = True
            response.headers = {}
            if "highlights_tray" in url:
                tray = [{"id": "highlight:1800", "title": "Платья"}] if existing else []
                response.json.return_value = {"status": "ok", "tray": tray}
            else:
                cookies["csrftoken"] = "csrf-token"
                response.json.return_value = {}
            return response

        def post(url, headers=None, data=None, **_kw):
            _HIGHLIGHT_CALLS.append(("POST", url, dict(data or {})))
            response = MagicMock()
            response.is_success = True
            response.headers = {}
            response.json.return_value = {"status": "ok", "reel": {"id": "highlight:1900"}}
            return response

        client.get.side_effect = get
        client.post.side_effect = post
        return client

    _HIGHLIGHT_CALLS.clear()
    return factory
