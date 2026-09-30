"""Story collage copy, garment categories, and Highlight filing."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from PIL import Image

from publishers.instagram_private_story import InstagramPrivateStory
from publishers.instagram_highlights import InstagramHighlightClient, InstagramSessionExpired
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


def test_private_story_link_opens_telegram_and_creates_missing_highlight() -> None:
    client = _FakeWebSession()
    publisher = InstagramPrivateStory("1" * 40, client=client)

    story_pk = publisher.publish_story(
        Path("story.jpg"),
        link_url="https://t.me/fashionalleyb",
        link_title="посмотреть подробнее фото",
        highlight_title="Трикотаж",
    )

    assert story_pk == "555"
    assert client.link_url == "https://t.me/fashionalleyb"
    assert client.link_title == "посмотреть подробнее фото"
    assert client.sticker == (0.50, 0.84, 0.68, 0.055)
    assert client.highlight == ("Трикотаж", "555")


def test_private_story_keeps_the_linked_story_when_highlight_fails() -> None:
    client = _FakeWebSession(highlight_error=RuntimeError("highlight down"))
    publisher = InstagramPrivateStory("1" * 40, client=client)

    story_pk = publisher.publish_story(
        Path("story.jpg"),
        link_url="https://t.me/fashionalleyb",
        highlight_title="Платья",
    )

    assert story_pk == "555"
    assert client.link_url == "https://t.me/fashionalleyb"


def test_web_story_sends_the_telegram_link_sticker() -> None:
    import json

    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "story.jpg"
        Image.new("RGB", (1080, 1920), (20, 20, 20)).save(image, format="JPEG")
        client = InstagramHighlightClient("27709919492%3Atoken", "27709919492")
        calls: list[tuple] = []
        with patch("httpx.Client", side_effect=_story_upload_factory(calls)):
            story_pk = client.publish_linked_story(
                image,
                link_url="https://t.me/fashionalleyb",
                link_title="посмотреть подробнее фото",
                x=0.5,
                y=0.84,
                width=0.68,
                height=0.055,
            )
    assert story_pk == "555"
    configure = [item for item in calls if item[0].endswith("configure_to_story/")]
    assert configure
    sticker = json.loads(configure[0][1]["tap_models"])[0]
    assert sticker["url"] == "https://t.me/fashionalleyb"
    assert sticker["custom_cta"] == "посмотреть подробнее фото"
    assert sticker["type"] == "story_link"
    assert client.session_id == "27709919492:token"


def test_login_redirect_rejects_the_session_before_upload() -> None:
    client = InstagramHighlightClient("27709919492:token", "27709919492")
    with patch("httpx.Client", side_effect=_login_redirect_factory()):
        try:
            client.publish_linked_story(
                Path("story.jpg"),
                link_url="https://t.me/fashionalleyb",
                link_title="посмотреть подробнее фото",
                x=0.5,
                y=0.84,
                width=0.68,
                height=0.055,
            )
        except InstagramSessionExpired as exc:
            assert "INSTAGRAM_SESSIONID" in str(exc)
        else:
            raise AssertionError("expired session was accepted")


def test_existing_highlight_receives_the_story() -> None:
    client = InstagramHighlightClient("session", "1789")
    with patch("httpx.Client", side_effect=_highlight_factory(existing=True)):
        highlight_id = client.add_story("Платья", "555")
    assert highlight_id == "1800"
    posts = [item for item in _HIGHLIGHT_CALLS if item[0] == "POST"]
    assert posts[0][1].endswith("/highlights/highlight:1800/edit_reel/")
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


class _FakeWebSession:
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
            response.status_code = 200
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
            response.status_code = 200
            response.headers = {}
            response.json.return_value = {"status": "ok", "reel": {"id": "highlight:1900"}}
            return response

        client.get.side_effect = get
        client.post.side_effect = post
        return client

    _HIGHLIGHT_CALLS.clear()
    return factory


def _ok_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.is_success = True
    response.status_code = 200
    response.headers = {}
    response.json.return_value = payload
    return response


def _story_upload_factory(calls: list[tuple]):
    def factory(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client
        cookies: dict[str, str] = {}
        client.cookies.set.side_effect = lambda name, value, domain=None: cookies.__setitem__(name, value)
        client.cookies.get.side_effect = cookies.get

        def get(url, headers=None, **_kw):
            if url.rstrip("/").endswith("instagram.com"):
                cookies["csrftoken"] = "csrf-token"
                return _ok_response({})
            return _ok_response({"status": "ok", "form_data": {"username": "shop"}})

        def post(url, headers=None, data=None, content=None, **_kw):
            calls.append((url, dict(data or {})))
            if url.endswith("configure_to_story/"):
                return _ok_response({
                    "status": "ok",
                    "media": {
                        "pk": "555",
                        "story_link_stickers": [{"story_link": {"url": "https://t.me/fashionalleyb"}}],
                    },
                })
            return _ok_response({"status": "ok"})

        client.get.side_effect = get
        client.post.side_effect = post
        return client

    return factory


def _login_redirect_factory():
    def factory(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client
        cookies: dict[str, str] = {}
        client.cookies.set.side_effect = lambda name, value, domain=None: cookies.__setitem__(name, value)
        client.cookies.get.side_effect = cookies.get

        def get(url, headers=None, **_kw):
            cookies["csrftoken"] = "csrf-token"
            response = MagicMock()
            response.is_success = False
            response.status_code = 302
            response.headers = {"location": "https://www.instagram.com/accounts/login/"}
            response.json.return_value = {}
            response.text = ""
            return response

        client.get.side_effect = get
        client.post.side_effect = get
        return client

    return factory
