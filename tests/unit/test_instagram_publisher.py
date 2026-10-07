"""Instagram caption format and Graph API publishing flow."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import yaml
from PIL import Image, ImageDraw

from adapters.base import RawProduct
from config.app_config import DEFAULT_INSTAGRAM_CAPTION_FOOTER
from core.composer import compose_post
from publishers.dry_run_publisher import DryRunPublisher
from publishers.image_hosting import LitterboxImageHost
from publishers.instagram_media import (
    FEED_HEIGHT,
    FEED_WIDTH,
    prepare_feed_jpeg,
    render_feed_collage,
)
from publishers.instagram_publisher import (
    InstagramPublisher,
    _media_fetch_failure,
    footer_for_carousel,
    format_instagram_caption,
)


def _photo(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    """A stand-in product photo: a garment on a plain backdrop (a flat canvas is an empty picture)."""
    image = Image.new("RGB", size, color)
    width, height = size
    garment = tuple(255 - channel for channel in color)
    ImageDraw.Draw(image).rectangle((width // 4, height // 4, width * 3 // 4, height * 3 // 4), fill=garment)
    return image


def _sample_post(price: str = "101.39") -> object:
    product = RawProduct(
        external_id="sku-100",
        source="zara",
        title="Silk Slip Dress",
        price=Decimal("79.99"),
        currency="EUR",
        photo_url="https://images.example.com/dress.jpg",
        product_url="https://www.zara.com/dress-100",
        in_stock=True,
    )
    return compose_post(
        product=product,
        description="Размеры от XS до XL.\nЦвет: черный.",
        price=Decimal(price),
        target_currency="USD",
        include_link=True,
    )


def test_caption_matches_boutique_card_and_instagram_limits() -> None:
    post = _sample_post()
    caption = format_instagram_caption(post, DEFAULT_INSTAGRAM_CAPTION_FOOTER)

    assert caption.startswith("Silk Slip Dress-100$\nРазмеры от XS до XL.")
    assert "Обращаться: @nigora_7" in caption
    assert "https://t.me/fashionalleyb" in caption
    carousel = format_instagram_caption(post, footer_for_carousel(DEFAULT_INSTAGRAM_CAPTION_FOOTER))
    assert "Обращаться" not in carousel
    assert "Отзывы" not in carousel
    assert "в наличии" not in carousel
    assert "t.me" not in carousel
    assert "Telegram" not in carousel
    assert "Европейское качество" in carousel
    assert "Тел:+998998484044" in carousel
    assert "https://www.zara.com/dress-100" not in caption
    assert "🏷" not in caption
    assert "Наш Instagram" not in caption
    assert "#" not in caption
    assert len(caption) <= 2200


def test_whole_dollar_price_has_no_decimals() -> None:
    caption = format_instagram_caption(_sample_post("78"), DEFAULT_INSTAGRAM_CAPTION_FOOTER)
    assert caption.startswith("Silk Slip Dress-78$")


def test_feed_jpeg_is_four_by_five() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "tall.png"
        Image.new("RGB", (800, 1600), (250, 20, 20)).save(source)
        dest = prepare_feed_jpeg(source, Path(tmp) / "out.jpg")
        with Image.open(dest) as image:
            assert image.size == (FEED_WIDTH, FEED_HEIGHT)
            assert image.format == "JPEG"
            # The photo fills the frame. There is no gray side bar.
            assert _close(image.getpixel((0, 0)), (250, 20, 20))
            assert _close(image.getpixel((FEED_WIDTH - 1, FEED_HEIGHT - 1)), (250, 20, 20))


def test_single_photo_publishes_after_container_is_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        _photo((900, 1200), (255, 255, 255)).save(photo)
        post = _sample_post()
        post = type(post)(
            photo_url=str(photo),
            text=post.text,
            price=post.price,
            currency=post.currency,
            product_url=post.product_url,
            title=post.title,
            source=post.source,
            photo_urls=[str(photo)],
        )
        calls: list[tuple[str, str, dict]] = []

        publisher = InstagramPublisher(
            access_token="token",
            account_id="1789",
            image_host=_Host(),
            username="mukhsinius",
        )

        with patch("httpx.Client", side_effect=_client_factory(calls)):
            result = publisher.publish(post)

        assert result.success is True
        assert result.platform_post_id == "id-2"
        posts = [item for item in calls if item[0] == "POST"]
        assert len(posts) == 4
        assert posts[0][2]["image_url"].startswith("https://files.example.com/")
        assert posts[0][2]["caption"].startswith("Silk Slip Dress-100$")
        assert "is_carousel_item" not in posts[0][2]
        assert "Обращаться: @nigora_7" in posts[0][2]["caption"]
        assert posts[1][2]["creation_id"] == "id-1"
        assert posts[2][2]["media_type"] == "STORIES"
        assert posts[2][2]["image_url"].endswith("_story.jpg")
        assert "caption" not in posts[2][2]
        assert posts[3][2]["creation_id"] == "id-3"
        assert any(item[0] == "GET" and item[2]["fields"] == "status_code,status" for item in calls)


def test_feed_collage_matches_boutique_cover() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        colors = ((20, 20, 20), (210, 180, 140), (40, 70, 120))
        photos = []
        for index, color in enumerate(colors):
            path = folder / f"look-{index}.jpg"
            Image.new("RGB", (800, 1200), color).save(path)
            photos.append(path)
        dest = render_feed_collage(photos, folder / "cover.jpg")
        with Image.open(dest) as image:
            assert image.size == (FEED_WIDTH, FEED_HEIGHT)
            assert image.format == "JPEG"
            # Hero (first photo) fills the right tile; the next two stack on the left.
            assert _close(image.getpixel((FEED_WIDTH * 3 // 4, FEED_HEIGHT // 2)), colors[0])
            assert _close(image.getpixel((FEED_WIDTH // 4, FEED_HEIGHT // 4)), colors[1])
            assert _close(image.getpixel((FEED_WIDTH // 4, FEED_HEIGHT * 7 // 8)), colors[2])
            # White gutter between the columns.
            assert _close(image.getpixel((FEED_WIDTH // 2, FEED_HEIGHT // 2)), (255, 255, 255))


def test_several_photos_become_a_carousel() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photos = []
        for index, color in enumerate(((240, 30, 30), (30, 30, 240))):
            path = Path(tmp) / f"look-{index}.jpg"
            _photo((700, 1400), color).save(path)
            photos.append(str(path))
        post = _sample_post()
        post = type(post)(
            photo_url=photos[0],
            text=post.text,
            price=post.price,
            currency=post.currency,
            product_url=post.product_url,
            title=post.title,
            source=post.source,
            photo_urls=photos,
        )
        calls: list[tuple[str, str, dict]] = []
        publisher = InstagramPublisher("token", "1789", image_host=_Host())

        with patch("httpx.Client", side_effect=_client_factory(calls)):
            result = publisher.publish(post)

        assert result.success is True
        posts = [item for item in calls if item[0] == "POST"]
        assert len(posts) == 7
        slides = [item[2]["image_url"] for item in posts[:3]]
        assert all(item[2]["is_carousel_item"] == "true" for item in posts[:3])
        assert all("caption" not in item[2] for item in posts[:3])
        assert slides[0].endswith("look-0_ig_cover.jpg")
        assert slides[1].endswith("look-0_ig.jpg")
        assert slides[2].endswith("look-1_ig.jpg")
        carousel = posts[3][2]
        assert carousel["media_type"] == "CAROUSEL"
        assert carousel["children"] == "id-1,id-2,id-3"
        assert carousel["caption"].startswith("Silk Slip Dress-100$")
        assert "Обращаться" not in carousel["caption"]
        assert "Отзывы" not in carousel["caption"]
        assert "в наличии" not in carousel["caption"]
        assert "t.me" not in carousel["caption"]
        assert "Европейское качество" in carousel["caption"]
        assert "Тел:+998998484044" in carousel["caption"]
        assert posts[4][2]["creation_id"] == "id-4"
        assert posts[5][2]["media_type"] == "STORIES"
        assert posts[6][2]["creation_id"] == "id-6"


def test_full_carousel_keeps_the_product_angles_after_the_cover() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photos = []
        for index in range(10):
            path = Path(tmp) / f"look-{index}.jpg"
            frame = Image.new("RGB", (700, 1400), (20 * index, 90, 200 - 15 * index))
            # Every angle frames the garment differently; flat fills would read as repeats.
            ImageDraw.Draw(frame).rectangle(
                (60, 60 + 120 * index, 640, 520 + 70 * index),
                fill=(240, 230 - 10 * index, 20 * index),
            )
            frame.save(path)
            photos.append(str(path))
        post = _with_photos(_sample_post(), photos)
        calls: list[tuple[str, str, dict]] = []
        publisher = InstagramPublisher("token", "1789", image_host=_Host())

        with patch("httpx.Client", side_effect=_client_factory(calls)):
            result = publisher.publish(post)

        assert result.success is True
        slides = [
            Path(item[2]["image_url"]).name
            for item in calls
            if item[0] == "POST" and item[2].get("is_carousel_item") == "true"
        ]
        assert slides == [
            "look-0_ig_cover.jpg",
            *(f"look-{index}_ig.jpg" for index in (0, 1, 2, 3, 4, 5, 7, 8, 9)),
        ]


def test_story_is_filed_into_the_dress_highlight() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        _photo((900, 1200), (40, 40, 80)).save(photo)
        post = _sample_post()
        post = type(post)(
            photo_url=str(photo),
            text=post.text,
            price=post.price,
            currency=post.currency,
            product_url=post.product_url,
            title="Платье",
            source=post.source,
            photo_urls=[str(photo)],
        )
        private = _PrivateStory()
        publisher = InstagramPublisher("token", "1789", image_host=_Host(), private_story=private)
        calls: list[tuple[str, str, dict]] = []

        with patch("httpx.Client", side_effect=_client_factory(calls)):
            result = publisher.publish(post)

        assert result.success is True
        assert len(private.calls) == 1
        call = private.calls[0]
        assert call["link_url"] == "https://t.me/fashionalleyb"
        assert call["link_title"] == "посмотреть подробнее фото"
        assert call["highlight_title"] == "Платья"
        posts = [item for item in calls if item[0] == "POST"]
        assert len(posts) == 2
        assert all(item[2].get("media_type") != "STORIES" for item in posts)


def test_story_links_to_the_telegram_post_of_the_product() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        _photo((900, 1200), (40, 40, 80)).save(photo)
        post = replace(
            _with_photos(_sample_post(), [str(photo)]),
            telegram_links=("https://t.me/fashionalleyb/205728",),
        )
        private = _PrivateStory()
        publisher = InstagramPublisher("token", "1789", image_host=_Host(), private_story=private)

        with patch("httpx.Client", side_effect=_client_factory([])):
            publisher.publish(post)

        assert private.calls[0]["link_url"] == "https://t.me/fashionalleyb/205728"


def test_rejected_session_does_not_publish_a_plain_story() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        _photo((900, 1200), (40, 40, 80)).save(photo)
        sample = _sample_post()
        post = type(sample)(
            photo_url=str(photo),
            text=sample.text,
            price=sample.price,
            currency=sample.currency,
            product_url=sample.product_url,
            title="Платье",
            source=sample.source,
            photo_urls=[str(photo)],
        )

        class _Rejected:
            def publish_story(self, image, *, link_url: str, link_title: str, highlight_title: str) -> str:
                raise RuntimeError("Instagram rejected INSTAGRAM_SESSIONID")

        publisher = InstagramPublisher("token", "1789", image_host=_Host(), private_story=_Rejected())
        calls: list[tuple[str, str, dict]] = []
        with patch("httpx.Client", side_effect=_client_factory(calls)):
            result = publisher.publish(post)

        assert result.success is True
        story_posts = [
            item for item in calls
            if item[0] == "POST" and item[2].get("media_type") == "STORIES"
        ]
        assert len(story_posts) == 1


def test_account_login_enables_the_linked_story() -> None:
    publisher = InstagramPublisher(
        "token", "1789", image_host=_Host(), login="shop@example.com", password="secret"
    )
    assert publisher.private_story is not None
    assert publisher.private_story.login == "shop@example.com"
    assert InstagramPublisher("token", "1789", image_host=_Host()).private_story is None


def test_fetch_error_keeps_only_the_image_url() -> None:
    failed, url = _media_fetch_failure({
        "code": 9004,
        "error_subcode": 2207052,
        "message": "Only photo or video can be accepted as media type.",
        "error_user_msg": "The media could not be fetched from this uri: https://files.catbox.moe/yl8135.jpg.Не удалось",
    })
    assert failed is True
    assert url == "https://files.catbox.moe/yl8135.jpg"


def test_catbox_host_returns_a_verified_jpeg_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        photo.write_bytes(b"jpeg-bytes")
        host = LitterboxImageHost()

        with patch("httpx.Client") as mock_client_cls:
            client = _host_client("https://files.catbox.moe/abc.jpg")
            mock_client_cls.return_value = client

            assert host.ensure_public_url(str(photo)) == "https://files.catbox.moe/abc.jpg"
            assert client.post.call_args.kwargs["data"]["reqtype"] == "fileupload"
            assert "time" not in client.post.call_args.kwargs["data"]


def test_unfetchable_host_is_skipped_for_the_next_upload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        photo.write_bytes(b"jpeg-bytes")
        host = LitterboxImageHost()
        jpeg = b"\xff\xd8\xff" + b"jpeg-bytes" * 30

        with patch("httpx.Client") as mock_client_cls:
            client = MagicMock()
            client.__enter__.return_value = client

            def post(url, data=None, files=None):
                response = MagicMock()
                response.raise_for_status = MagicMock()
                if "catbox.moe/user" in url:
                    response.text = "https://files.catbox.moe/blocked.jpg"
                else:
                    response.text = "https://0x0.st/good.jpg"
                return response

            def get(url, headers=None):
                fetched = MagicMock()
                if "catbox" in url:
                    fetched.status_code = 403
                    fetched.content = b"denied"
                else:
                    fetched.status_code = 200
                    fetched.content = jpeg
                return fetched

            client.post.side_effect = post
            client.get.side_effect = get
            mock_client_cls.return_value = client

            assert host.ensure_public_url(str(photo)) == "https://0x0.st/good.jpg"
            assert host.replace_unfetchable("https://0x0.st/good.jpg") == "https://0x0.st/good.jpg"


def _host_client(public_url: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.text = public_url
    response.raise_for_status = MagicMock()
    fetched = MagicMock()
    fetched.status_code = 200
    fetched.content = b"\xff\xd8\xff" + b"jpeg-bytes" * 30
    client.__enter__.return_value = client
    client.post.return_value = response
    client.get.return_value = fetched
    return client


def test_dry_run_logs_instagram_caption() -> None:
    publisher = DryRunPublisher(InstagramPublisher("token", "1789", image_host=_Host()))
    result = publisher.publish(_sample_post())
    assert result.success is True
    assert result.platform_post_id.startswith("DRYRUN_INSTAGRAM_")


def test_config_points_instagram_at_test_account() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["instagram"]["username"] in ("mukhsinius", "invito.live")
    assert "t.me/fashionalleyb" in data["instagram"]["caption_footer"]
    assert "instagram.com" not in data["instagram"]["caption_footer"]


def _with_photos(post, photos: list[str]):
    return replace(post, photo_url=photos[0], photo_urls=photos)


def _close(pixel: tuple[int, ...], color: tuple[int, int, int], tolerance: int = 8) -> bool:
    return all(abs(channel - expected) <= tolerance for channel, expected in zip(pixel, color))


class _PrivateStory:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def publish_story(self, image, *, link_url: str, link_title: str, highlight_title: str) -> str:
        self.calls.append({
            "link_url": link_url,
            "link_title": link_title,
            "highlight_title": highlight_title,
        })
        return "story-1"


class _Host:
    def ensure_public_url(self, photo_url_or_path: str) -> str:
        return "https://files.example.com/" + Path(photo_url_or_path).name


def _client_factory(calls: list[tuple[str, str, dict]]):
    def factory(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client

        def post(url, data=None, **_kw):
            payload = dict(data or {})
            calls.append(("POST", url, payload))
            response = MagicMock()
            response.is_success = True
            response.json.return_value = {"id": f"id-{len([c for c in calls if c[0] == 'POST'])}"}
            response.text = "{}"
            response.request = MagicMock()
            return response

        def get(url, params=None, **_kw):
            payload = dict(params or {})
            calls.append(("GET", url, payload))
            response = MagicMock()
            response.is_success = True
            response.json.return_value = {"id": "container", "status_code": "FINISHED"}
            response.text = "{}"
            response.request = MagicMock()
            return response

        client.post.side_effect = post
        client.get.side_effect = get
        return client

    return factory
