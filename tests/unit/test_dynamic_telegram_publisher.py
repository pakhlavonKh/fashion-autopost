"""Unit tests for dynamic multi-channel Telegram publisher, admin notifier, and dashboard endpoints."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import yaml

from config.app_config import AppConfig
from core.composer import ComposedPost
from dashboard.server import create_dashboard_app
from logging_setup.admin_notifier import TelegramAdminNotifier
from publishers.telegram_publisher import TelegramPublisher
from storage.repository import SqlAlchemyProductRepository


def test_telegram_posts_one_album_of_at_most_ten(tmp_path: Path) -> None:
    """Twelve store photos make one album of ten with the caption, never a second message.

    As in an Instagram carousel, model shots go from the middle and the closing
    front, back and close-up of the product stay.
    """
    from PIL import Image

    photos = []
    for index in range(12):
        path = tmp_path / f"shot-{index}.jpg"
        Image.new("RGB", (40, 80), (index, index, index)).save(path)
        photos.append(str(path))

    pub = TelegramPublisher(bot_token="test_token", channel_id="-100111")
    groups: list[tuple[list[str], str]] = []
    singles: list[str] = []

    def mock_group(batch, caption, chat_id=None):
        groups.append((list(batch), caption))
        return "10"

    def mock_photo(photo, caption, chat_id=None):
        singles.append(caption)
        return "11"

    with patch.object(pub, "_canonical_chat_id", side_effect=lambda chat_id: chat_id):
        with patch.object(pub, "_send_media_group_with_retry", side_effect=mock_group):
            with patch.object(pub, "_send_photo_with_retry", side_effect=mock_photo):
                msg_id = pub._send_all_photos(photos, "Слингбэки-98$", "-100111")

    assert msg_id == "10"
    assert len(groups) == 1
    album, caption = groups[0]
    assert caption == "Слингбэки-98$"
    assert album == photos[:7] + photos[-3:]
    assert singles == []


def test_telegram_album_of_ten_or_fewer_is_sent_as_it_is(tmp_path: Path) -> None:
    pub = TelegramPublisher(bot_token="test_token", channel_id="-100111")
    groups: list[list[str]] = []
    photos = [str(tmp_path / f"shot-{index}.jpg") for index in range(10)]
    with patch.object(pub, "_send_media_group_with_retry", side_effect=lambda batch, caption, chat_id=None: groups.append(list(batch)) or "7"):
        assert pub._send_all_photos(photos, "Платье-55$", "-100111") == "7"
    assert groups == [photos]


def test_telegram_publisher_multi_channel_broadcast(tmp_path: Path) -> None:
    """TelegramPublisher dynamically broadcasts to all active channels in the database."""
    db_url = f"sqlite:///{tmp_path / 'tg_pub.db'}"
    repo = SqlAlchemyProductRepository(db_url)

    # Register 2 active channels
    repo.upsert_telegram_chat(chat_id="-100111", title="Channel One", chat_type="channel", role="publish_target")
    repo.upsert_telegram_chat(chat_id="-100222", title="Channel Two", chat_type="channel", role="publish_target")

    pub = TelegramPublisher(bot_token="test_token", repo=repo)

    post = ComposedPost(
        title="Silk Dress",
        text="Elegant silk dress.",
        price=Decimal("99.00"),
        currency="USD",
        photo_url="https://example.com/dress.jpg",
        product_url="https://example.com/p/1",
        source="zara",
    )

    sent_to = []

    def mock_send(photo_url, caption, chat_id=None):
        sent_to.append(chat_id)
        return f"msg_{chat_id}"

    with patch.object(pub, "_canonical_chat_id", side_effect=lambda chat_id: chat_id):
        with patch.object(pub, "_send_photo_with_retry", side_effect=mock_send):
            res = pub.publish(post)

    assert res.success is True
    assert "-100111" in sent_to
    assert "-100222" in sent_to
    assert "-100111:msg_-100111" in res.platform_post_id
    assert "-100222:msg_-100222" in res.platform_post_id


def test_telegram_publisher_channel_error_isolation(tmp_path: Path) -> None:
    """If one channel fails (e.g. bot removed), other channels still receive the post."""
    db_url = f"sqlite:///{tmp_path / 'tg_pub.db'}"
    repo = SqlAlchemyProductRepository(db_url)

    repo.upsert_telegram_chat(chat_id="-100111", title="Working Channel", chat_type="channel", role="publish_target")
    repo.upsert_telegram_chat(chat_id="-100999", title="Failing Channel", chat_type="channel", role="publish_target")

    pub = TelegramPublisher(bot_token="test_token", repo=repo)

    post = ComposedPost(
        title="Wool Blazer",
        text="Tailored blazer.",
        price=Decimal("150.00"),
        currency="USD",
        photo_url="https://example.com/blazer.jpg",
        product_url="https://example.com/p/2",
        source="mango",
    )

    def mock_send(photo_url, caption, chat_id=None):
        if chat_id == "-100999":
            raise RuntimeError("Bot was kicked from this channel")
        return f"msg_{chat_id}"

    with patch.object(pub, "_canonical_chat_id", side_effect=lambda chat_id: chat_id):
        with patch.object(pub, "_send_photo_with_retry", side_effect=mock_send):
            res = pub.publish(post)

    # Returns success because working channel succeeded
    assert res.success is True
    assert "-100111:msg_-100111" in res.platform_post_id


def test_telegram_admin_notifier_dynamic_broadcast(tmp_path: Path) -> None:
    """Admin alert notifications are dynamically dispatched to all registered admin chats."""
    db_url = f"sqlite:///{tmp_path / 'tg_notifier.db'}"
    repo = SqlAlchemyProductRepository(db_url)

    repo.upsert_telegram_chat(chat_id="11111", title="Admin Alice", chat_type="private", role="admin_alert")
    repo.upsert_telegram_chat(chat_id="22222", title="Admin Bob", chat_type="private", role="admin_alert")

    notifier = TelegramAdminNotifier(bot_token="test_token", repo=repo)

    dispatched_chats = []

    with patch("httpx.Client.post") as mock_post:
        def capture_post(url, json=None):
            dispatched_chats.append(json["chat_id"])
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        mock_post.side_effect = capture_post
        notifier.notify_critical(stage="pricing", message="Rate conversion error", external_id="item-55")

    assert "11111" in dispatched_chats
    assert "22222" in dispatched_chats


def test_dashboard_telegram_chat_endpoints(tmp_path: Path) -> None:
    """Dashboard API endpoints for listing, updating, and deactivating Telegram chats."""
    db_url = f"sqlite:///{tmp_path / 'tg_api.db'}"
    repo = SqlAlchemyProductRepository(db_url)
    repo.upsert_telegram_chat(chat_id="-100777", title="Summer Collection", chat_type="channel", role="publish_target")

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"schedule": {"times": ["10:00"]}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    mock_runner = MagicMock()

    app = create_dashboard_app(config=config, runner=mock_runner, repo=repo)
    client = TestClient(app)
    client.headers["X-Admin-Key"] = config.dashboard.admin_key

    # 1. GET /api/telegram/chats
    res = client.get("/api/telegram/chats")
    assert res.status_code == 200
    chats = res.json()["chats"]
    assert len(chats) == 1
    assert chats[0]["chat_id"] == "-100777"
    assert chats[0]["is_active"] is True

    # 2. PATCH /api/telegram/chats/-100777 (toggle off)
    res_patch = client.patch("/api/telegram/chats/-100777", json={"is_active": False})
    assert res_patch.status_code == 200

    targets = repo.get_active_telegram_targets()
    assert len(targets) == 0

    # 3. DELETE /api/telegram/chats/-100777 (deactivate)
    res_del = client.delete("/api/telegram/chats/-100777")
    assert res_del.status_code == 200


def test_telegram_publisher_comma_separated_destinations() -> None:
    """TelegramPublisher supports comma-separated list of group ID and channel username."""
    pub = TelegramPublisher(bot_token="test_token", channel_id="-1004363309099, @fashionalleyb")

    post = ComposedPost(
        title="Floral Blouse",
        text="Stylish floral blouse.",
        price=Decimal("45.00"),
        currency="USD",
        photo_url="https://example.com/blouse.jpg",
        product_url="https://example.com/p/2",
        source="mango",
    )

    sent_to = []

    def mock_send(photo_url, caption, chat_id=None):
        sent_to.append(chat_id)
        return f"msg_{chat_id}"

    with patch.object(pub, "_canonical_chat_id", side_effect=lambda chat_id: chat_id):
        with patch.object(pub, "_send_photo_with_retry", side_effect=mock_send):
            res = pub.publish(post)

    assert res.success is True
    assert "-1004363309099" in sent_to
    assert "@fashionalleyb" in sent_to
    assert len(sent_to) == 2


def test_telegram_publisher_sends_once_when_username_matches_numeric_id(tmp_path: Path) -> None:
    """@channel and its numeric id are the same chat and must not both receive the post."""
    db_url = f"sqlite:///{tmp_path / 'tg_pub.db'}"
    repo = SqlAlchemyProductRepository(db_url)
    repo.upsert_telegram_chat(
        chat_id="-1001246015920",
        title="Fashion Nest Boutique",
        chat_type="channel",
        role="publish_target",
    )
    pub = TelegramPublisher(
        bot_token="test_token",
        channel_id="-1004363309099, @fashionalleyb",
        repo=repo,
    )
    post = ComposedPost(
        title="Floral Blouse",
        text="Stylish floral blouse.",
        price=Decimal("45.00"),
        currency="USD",
        photo_url="https://example.com/blouse.jpg",
        product_url="https://example.com/p/2",
        source="mango",
    )
    sent_to: list[str] = []

    def mock_send(photo_url, caption, chat_id=None):
        sent_to.append(chat_id)
        return f"msg_{chat_id}"

    def resolve(chat_id: str) -> str:
        if chat_id in {"@fashionalleyb", "-1001246015920"}:
            return "-1001246015920"
        return chat_id

    with patch.object(pub, "_canonical_chat_id", side_effect=resolve):
        with patch.object(pub, "_send_photo_with_retry", side_effect=mock_send):
            res = pub.publish(post)

    assert res.success is True
    assert sent_to == ["-1001246015920", "-1004363309099"]


def _chat_lookup_client(chats: dict[str, dict | None]):
    """httpx.Client stub for getChat. A None entry means Telegram did not answer."""

    def fake_client(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client

        def get(_url, params=None, **_kwargs):
            response = MagicMock()
            if params is None:
                response.content = b""
                return response
            chat = chats.get(params["chat_id"], {})
            if chat is None:
                raise RuntimeError("Telegram did not answer getChat")
            response.json.return_value = {"ok": True, "result": chat}
            return response

        client.get.side_effect = get
        return client

    return fake_client


def test_channel_listed_by_id_and_by_handle_receives_one_post(tmp_path: Path) -> None:
    """The database holds the channel id and the config repeats it as @handle: one post, not two."""
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'tg_pub.db'}")
    repo.upsert_telegram_chat(
        chat_id="-1001246015920",
        title="Fashion Nest Boutique",
        chat_type="channel",
        role="publish_target",
    )
    pub = TelegramPublisher(
        bot_token="test_token",
        channel_id="-1004363309099, @fashionalleyb",
        repo=repo,
    )
    post = ComposedPost(
        title="Floral Blouse",
        text="Stylish floral blouse.",
        price=Decimal("45.00"),
        currency="USD",
        photo_url="https://example.com/blouse.jpg",
        product_url="https://example.com/p/2",
        source="mango",
    )
    chats = {
        "-1001246015920": {"id": -1001246015920, "type": "channel", "username": "fashionalleyb"},
        "-1004363309099": {"id": -1004363309099, "type": "supergroup", "title": "dev"},
        "@fashionalleyb": {"id": -1001246015920, "type": "channel", "username": "fashionalleyb"},
    }
    sent_to: list[str] = []

    with patch("publishers.telegram_publisher.httpx.Client", side_effect=_chat_lookup_client(chats)):
        with patch.object(
            pub,
            "_send_photo_with_retry",
            side_effect=lambda photo, caption, chat_id=None: sent_to.append(chat_id) or "7",
        ):
            res = pub.publish(post)

    assert res.success is True
    assert sent_to == ["-1001246015920", "-1004363309099"]


def test_handle_telegram_cannot_resolve_is_still_recognised_as_the_same_channel() -> None:
    """A getChat failure must not turn one channel into two posts, whichever form comes first."""
    post = ComposedPost(
        title="Floral Blouse",
        text="Stylish floral blouse.",
        price=Decimal("45.00"),
        currency="USD",
        photo_url="https://example.com/blouse.jpg",
        product_url="https://example.com/p/2",
        source="mango",
    )
    chats: dict[str, dict | None] = {
        "-1001246015920": {"id": -1001246015920, "type": "channel", "username": "fashionalleyb"},
        "@fashionalleyb": None,
    }

    for channel_id in ("-1001246015920, @fashionalleyb", "@fashionalleyb, -1001246015920"):
        pub = TelegramPublisher(bot_token="test_token", channel_id=channel_id)
        sent_to: list[str] = []
        with patch(
            "publishers.telegram_publisher.httpx.Client",
            side_effect=_chat_lookup_client(chats),
        ):
            with patch.object(
                pub,
                "_send_photo_with_retry",
                side_effect=lambda photo, caption, chat_id=None: sent_to.append(chat_id) or "7",
            ):
                res = pub.publish(post)

        assert res.success is True
        assert len(sent_to) == 1, f"{channel_id} sent to {sent_to}"


def test_telegram_publisher_returns_public_post_link() -> None:
    """The public channel post link is returned so the Instagram story can open it."""
    pub = TelegramPublisher(bot_token="test_token", channel_id="-1004363309099, @fashionalleyb")
    post = ComposedPost(
        title="Floral Blouse",
        text="Stylish floral blouse.",
        price=Decimal("45.00"),
        currency="USD",
        photo_url="https://example.com/blouse.jpg",
        product_url="https://example.com/p/2",
        source="mango",
    )
    chats = {
        "-1004363309099": {"id": -1004363309099, "type": "supergroup", "title": "dev"},
        "@fashionalleyb": {"id": -1001246015920, "type": "channel", "username": "fashionalleyb"},
    }

    def fake_client(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client

        def get(_url, params=None, **_kwargs):
            response = MagicMock()
            if params is None:
                response.content = b""
                return response
            response.json.return_value = {"ok": True, "result": chats[params["chat_id"]]}
            return response

        client.get.side_effect = get
        return client

    with patch("publishers.telegram_publisher.httpx.Client", side_effect=fake_client):
        with patch.object(pub, "_send_photo_with_retry", return_value="205728"):
            res = pub.publish(post)

    assert res.success is True
    assert res.links == ("https://t.me/fashionalleyb/205728",)

