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
