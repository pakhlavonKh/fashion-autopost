"""Instagram caption format and Graph API publishing flow."""

from decimal import Decimal
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import yaml
from PIL import Image

from adapters.base import RawProduct
from config.app_config import DEFAULT_INSTAGRAM_CAPTION_FOOTER
from core.composer import compose_post
from publishers.dry_run_publisher import DryRunPublisher
from publishers.image_hosting import LitterboxImageHost
from publishers.instagram_media import FEED_HEIGHT, FEED_WIDTH, prepare_feed_jpeg
from publishers.instagram_publisher import InstagramPublisher, format_instagram_caption


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

    assert caption.startswith("Silk Slip Dress-101$\nРазмеры от XS до XL.")
    assert "Обращаться: @nigora_7" in caption
    assert "https://t.me/fashionalleyb" in caption
    assert "https://www.zara.com/dress-100" not in caption
    assert "🏷" not in caption
    assert "Наш Instagram" not in caption
    assert "#zara #fashion #style #outfit #одежда #стиль #lookoftheday" in caption
    assert caption.count("#") <= 30
    assert len(caption) <= 2200


def test_whole_dollar_price_has_no_decimals() -> None:
    caption = format_instagram_caption(_sample_post("78"), DEFAULT_INSTAGRAM_CAPTION_FOOTER)
    assert caption.startswith("Silk Slip Dress-78$")


def test_feed_jpeg_is_four_by_five() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "tall.png"
        Image.new("RGB", (800, 1600), (250, 250, 250)).save(source)
        dest = prepare_feed_jpeg(source, Path(tmp) / "out.jpg")
        with Image.open(dest) as image:
            assert image.size == (FEED_WIDTH, FEED_HEIGHT)
            assert image.format == "JPEG"


def test_single_photo_publishes_after_container_is_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        Image.new("RGB", (900, 1200), (255, 255, 255)).save(photo)
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
        assert posts[0][2]["caption"].startswith("Silk Slip Dress-101$")
        assert "is_carousel_item" not in posts[0][2]
        assert posts[1][2]["creation_id"] == "id-1"
        assert posts[2][2]["media_type"] == "STORIES"
        assert posts[2][2]["image_url"].endswith("_story.jpg")
        assert "caption" not in posts[2][2]
        assert posts[3][2]["creation_id"] == "id-3"
        assert any(item[0] == "GET" and item[2]["fields"] == "status_code,status" for item in calls)


def test_several_photos_become_a_carousel() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photos = []
        for index in range(2):
            path = Path(tmp) / f"look-{index}.jpg"
            Image.new("RGB", (700, 1400), (240, 240, 240)).save(path)
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
        assert len(posts) == 6
        assert posts[0][2]["is_carousel_item"] == "true"
        assert posts[1][2]["is_carousel_item"] == "true"
        assert "caption" not in posts[0][2]
        assert posts[2][2]["media_type"] == "CAROUSEL"
        assert posts[2][2]["children"] == "id-1,id-2"
        assert posts[2][2]["caption"].startswith("Silk Slip Dress-101$")
        assert posts[3][2]["creation_id"] == "id-3"
        assert posts[4][2]["media_type"] == "STORIES"
        assert posts[5][2]["creation_id"] == "id-5"


def test_story_is_filed_into_the_dress_highlight() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        Image.new("RGB", (900, 1200), (40, 40, 80)).save(photo)
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


def test_litterbox_host_returns_public_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        photo = Path(tmp) / "look.jpg"
        photo.write_bytes(b"jpeg-bytes")
        host = LitterboxImageHost()

        with patch("httpx.Client") as mock_client_cls:
            client = MagicMock()
            response = MagicMock()
            response.text = "https://litter.catbox.moe/abc.jpg"
            response.raise_for_status = MagicMock()
            client.__enter__.return_value = client
            client.post.return_value = response
            mock_client_cls.return_value = client

            assert host.ensure_public_url(str(photo)) == "https://litter.catbox.moe/abc.jpg"
            assert client.post.call_args.kwargs["data"]["time"] == "24h"


def test_dry_run_logs_instagram_caption() -> None:
    publisher = DryRunPublisher(InstagramPublisher("token", "1789", image_host=_Host()))
    result = publisher.publish(_sample_post())
    assert result.success is True
    assert result.platform_post_id.startswith("DRYRUN_INSTAGRAM_")


def test_config_points_instagram_at_test_account() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["instagram"]["enabled"] is False
    assert data["instagram"]["username"] == "mukhsinius"
    assert "t.me/fashionalleyb" in data["instagram"]["caption_footer"]
    assert "instagram.com" not in data["instagram"]["caption_footer"]


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
