"""Story collage copy, garment categories, and Highlight filing."""

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from instagrapi.exceptions import LoginRequired
from PIL import Image

from publishers.instagram_private_story import InstagramPrivateStory
from publishers.instagram_highlights import InstagramHighlightClient, InstagramSessionExpired
from publishers.instagram_story import (
    LINK_LABEL,
    STORY_HEIGHT,
    STORY_WIDTH,
    detect_highlight,
    link_sticker_area,
    render_story_collage,
    story_cards,
    story_link_url,
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
            assert image.getpixel((STORY_WIDTH // 2, 340)) == (255, 255, 255)
            # The link sticker's tap area sits on the painted pill.
            x, y, width, _height = link_sticker_area()
            pill_right = int((x + width / 2) * STORY_WIDTH)
            assert image.getpixel((pill_right - 20, int(y * STORY_HEIGHT))) == (255, 255, 255)
            assert image.getpixel((pill_right + 20, int(y * STORY_HEIGHT))) != (255, 255, 255)
        assert LINK_LABEL


def test_story_links_to_the_product_post_in_the_channel() -> None:
    footer = "Наш канал: https://t.me/fashionalleyb"
    links = ("https://t.me/fashionalleyb_dev/12", "https://t.me/fashionalleyb/205728")
    assert story_link_url(links, footer) == "https://t.me/fashionalleyb/205728"
    assert story_link_url((), footer) == "https://t.me/fashionalleyb"
    assert story_link_url(("https://t.me/other/5",), None) == "https://t.me/fashionalleyb"


def test_private_story_link_opens_telegram_and_creates_missing_highlight() -> None:
    client = _FakeHighlightClient()
    publisher = InstagramPrivateStory("1" * 40, client=client)

    story_pk = publisher.publish_story(
        Path("story.jpg"),
        link_url="https://t.me/fashionalleyb/205728",
        link_title="посмотреть подробнее фото",
        highlight_title="Трикотаж",
    )

    assert story_pk == "555"
    assert client.link_url == "https://t.me/fashionalleyb/205728"
    assert client.link_title == "посмотреть подробнее фото"
    assert client.sticker == link_sticker_area()
    assert client.highlight == ("Трикотаж", "555")


def test_private_story_keeps_the_linked_story_when_highlight_fails() -> None:
    client = _FakeHighlightClient(highlight_error=RuntimeError("highlight down"))
    publisher = InstagramPrivateStory("1" * 40, client=client)

    story_pk = publisher.publish_story(
        Path("story.jpg"),
        link_url="https://t.me/fashionalleyb",
        highlight_title="Платья",
    )

    assert story_pk == "555"
    assert client.link_url == "https://t.me/fashionalleyb"


def test_app_story_uploads_the_jpeg_untouched_with_a_link_sticker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "story.jpg"
        Image.new("RGB", (1080, 1920), (20, 20, 20)).save(image, format="JPEG", quality=100)
        app = _FakeAppClient()
        client = InstagramHighlightClient("27709919492%3Atoken", client=app)
        with patch("publishers.instagram_highlights.time.sleep"):
            story_pk = client.publish_linked_story(
                image,
                link_url="https://t.me/fashionalleyb/205728",
                link_title="посмотреть подробнее фото",
                x=0.5,
                y=0.85,
                width=0.6,
                height=0.05,
            )
        original = image.read_bytes()

    assert story_pk == "555"
    assert client.user_id == "27709919492"
    assert app.uploads == [original]
    assert app.requests[0] == (
        "media/validate_reel_url/",
        {"url": "https://t.me/fashionalleyb/205728", "_uid": "27709919492", "_uuid": "device-uuid"},
    )
    width, height, stickers = app.configured[0]
    assert (width, height) == (1080, 1920)
    sticker = stickers[0]
    assert sticker.type == "story_link"
    assert (sticker.x, sticker.y, sticker.width, sticker.height) == (0.5, 0.85, 0.6, 0.05)
    assert sticker.extra["url"] == "https://t.me/fashionalleyb/205728"
    assert sticker.extra["link_title"] == "посмотреть подробнее фото"


def test_rejected_session_stops_before_upload() -> None:
    app = _FakeAppClient(error=LoginRequired("login_required"))
    client = InstagramHighlightClient("27709919492:token", client=app)
    try:
        client.publish_linked_story(
            Path("story.jpg"),
            link_url="https://t.me/fashionalleyb",
            link_title="посмотреть подробнее фото",
            x=0.5,
            y=0.85,
            width=0.6,
            height=0.05,
        )
    except InstagramSessionExpired as exc:
        assert "INSTAGRAM_SESSIONID" in str(exc)
    else:
        raise AssertionError("expired session was accepted")
    assert app.uploads == []


def test_existing_highlight_receives_the_story() -> None:
    app = _FakeAppClient(tray=[{"id": "highlight:1800", "title": "Платья"}])
    client = InstagramHighlightClient("session", "1789", client=app)

    assert client.add_story("платья", "555") == "1800"

    endpoint, data = app.requests[-1]
    assert endpoint == "highlights/highlight:1800/edit_reel/"
    assert json.loads(data["added_media_ids"]) == ["555_1789"]


def test_missing_highlight_is_created() -> None:
    app = _FakeAppClient(tray=[{"id": "highlight:1800", "title": "Платья"}])
    client = InstagramHighlightClient("session", "1789", client=app)

    assert client.add_story("Трикотаж", "777") == "1900"

    endpoint, data = app.requests[-1]
    assert endpoint == "highlights/create_reel/"
    assert data["title"] == "Трикотаж"
    assert json.loads(data["media_ids"]) == ["777_1789"]
    assert json.loads(data["cover"])["media_id"] == "777_1789"


class _FakeHighlightClient:
    def __init__(self, highlight_error: Exception | None = None) -> None:
        self.link_url = ""
        self.link_title = ""
        self.sticker: tuple[float, float, float, float] | None = None
        self.highlight: tuple[str, str] | None = None
        self.highlight_error = highlight_error

    def publish_linked_story(self, _image, *, link_url: str, link_title: str, x: float, y: float, width: float, height: float) -> str:
        self.link_url = link_url
        self.link_title = link_title
        self.sticker = (x, y, width, height)
        return "555"

    def add_story(self, title: str, story_pk: str) -> str:
        if self.highlight_error:
            raise self.highlight_error
        self.highlight = (title, story_pk)
        return "hl-new"


class _FakeAppClient:
    """The slice of instagrapi.Client the highlight client uses."""

    uuid = "device-uuid"

    def __init__(self, tray: list[dict] | None = None, error: Exception | None = None) -> None:
        self.tray = tray or []
        self.error = error
        self.requests: list[tuple[str, dict]] = []
        self.uploads: list[bytes] = []
        self.configured: list[tuple] = []
        self.private = MagicMock()
        self.private.post.side_effect = self._upload

    def private_request(self, endpoint: str, data: dict | None = None, **_kwargs) -> dict:
        if self.error:
            raise self.error
        self.requests.append((endpoint, dict(data or {})))
        if endpoint.endswith("/highlights_tray/"):
            return {"status": "ok", "tray": self.tray}
        if endpoint == "highlights/create_reel/":
            return {"status": "ok", "reel": {"id": "highlight:1900"}}
        return {"status": "ok"}

    def private_headers(self, headers: dict | None = None) -> dict:
        return dict(headers or {})

    def photo_configure_to_story(self, _upload_id, width, height, _caption, stickers=(), **_kwargs) -> dict:
        self.configured.append((width, height, list(stickers)))
        return {"status": "ok", "media": {"pk": 555, "story_link_stickers": [{}]}}

    def _upload(self, _url: str, data: bytes, headers: dict) -> MagicMock:
        self.uploads.append(data)
        response = MagicMock()
        response.status_code = 200
        response.text = '{"status":"ok"}'
        return response
