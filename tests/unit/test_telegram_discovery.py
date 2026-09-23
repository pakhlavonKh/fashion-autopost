"""Unit tests for TelegramChatDiscoveryService and TelegramChatRecord storage.

Tests parsing of Telegram events (my_chat_member, channel_post, private message)
and persistence in SQLite repository without hardcoded chat IDs.
"""

from pathlib import Path
from storage.repository import SqlAlchemyProductRepository
from publishers.telegram_discovery import TelegramChatDiscoveryService


def test_telegram_discovery_my_chat_member_added(tmp_path: Path) -> None:
    """When bot is added as admin to a channel, it is discovered as a publish target."""
    db_url = f"sqlite:///{tmp_path / 'tg_disc.db'}"
    repo = SqlAlchemyProductRepository(db_url)
    service = TelegramChatDiscoveryService(bot_token="test_token", repo=repo)

    update = {
        "update_id": 1001,
        "my_chat_member": {
            "chat": {
                "id": -1001999888777,
                "title": "Autumn Fashion Deals",
                "type": "channel",
                "username": "autumn_fashion",
            },
            "new_chat_member": {
                "status": "administrator",
            },
        },
    }

    service._process_single_update(update)

    targets = repo.get_active_telegram_targets()
    assert len(targets) == 1
    t = targets[0]
    assert t.chat_id == "-1001999888777"
    assert t.title == "Autumn Fashion Deals"
    assert t.chat_type == "channel"
    assert t.role == "publish_target"
    assert t.username == "autumn_fashion"
    assert t.is_active is True


def test_telegram_discovery_bot_removed(tmp_path: Path) -> None:
    """When bot is removed from a channel, the channel is deactivated."""
    db_url = f"sqlite:///{tmp_path / 'tg_disc.db'}"
    repo = SqlAlchemyProductRepository(db_url)
    service = TelegramChatDiscoveryService(bot_token="test_token", repo=repo)

    # Initial join
    repo.upsert_telegram_chat(
        chat_id="-100123",
        title="Old Channel",
        chat_type="channel",
        role="publish_target",
        is_active=True,
    )
    assert len(repo.get_active_telegram_targets()) == 1

    # Bot kicked update
    kick_update = {
        "update_id": 1002,
        "my_chat_member": {
            "chat": {
                "id": -100123,
                "title": "Old Channel",
                "type": "channel",
            },
            "new_chat_member": {
                "status": "kicked",
            },
        },
    }
    service._process_single_update(kick_update)

    assert len(repo.get_active_telegram_targets()) == 0


def test_telegram_discovery_admin_private_message(tmp_path: Path) -> None:
    """When an admin messages /start to the bot, they are registered for admin alerts."""
    db_url = f"sqlite:///{tmp_path / 'tg_disc.db'}"
    repo = SqlAlchemyProductRepository(db_url)
    service = TelegramChatDiscoveryService(bot_token="test_token", repo=repo)

    msg_update = {
        "update_id": 1003,
        "message": {
            "message_id": 55,
            "chat": {
                "id": 98765432,
                "first_name": "Alice",
                "last_name": "Admin",
                "type": "private",
                "username": "alice_admin",
            },
            "text": "/start",
        },
    }
    service._process_single_update(msg_update)

    admins = repo.get_active_admin_chats()
    assert len(admins) == 1
    admin = admins[0]
    assert admin.chat_id == "98765432"
    assert "Alice" in admin.title
    assert admin.role == "admin_alert"
    assert admin.is_active is True
