"""Dynamic discovery service for Telegram channels, groups, and admin chats.

Inspects Telegram Bot API getUpdates to autonomously discover and register:
1. Channels/groups where the bot is added as administrator (publish_target).
2. Direct user messages (/start, /admin) to register for admin alerts (admin_alert).
3. Bot removal / kick events to automatically deactivate targets.

Eliminates the need for manual, hardcoded numeric chat IDs.
"""

import logging
from typing import Any
import httpx

from storage.models import TelegramChatRecord
from storage.repository import ProductRepository

logger = logging.getLogger(__name__)


class TelegramChatDiscoveryService:
    """Discovers and synchronizes Telegram channels and chats via Telegram getUpdates."""

    def __init__(
        self,
        bot_token: str,
        repo: ProductRepository,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.bot_token = bot_token
        self.repo = repo
        self.timeout_seconds = timeout_seconds

    def sync_updates(self) -> list[TelegramChatRecord]:
        """Poll Telegram getUpdates and register any newly joined channels or admin chats."""
        if not self.bot_token or "mock" in self.bot_token.lower():
            logger.debug("Skipping Telegram update sync: mock token configured")
            return self.repo.get_active_telegram_targets()

        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.get(url, params={"allowed_updates": ["message", "channel_post", "my_chat_member"]})
                if resp.status_code != 200:
                    logger.warning("Telegram getUpdates returned status %d: %s", resp.status_code, resp.text)
                    return self.repo.get_active_telegram_targets()

                data = resp.json()
                if not data.get("ok"):
                    logger.warning("Telegram getUpdates returned error: %s", data)
                    return self.repo.get_active_telegram_targets()

                updates = data.get("result", [])
                logger.info("TelegramChatDiscoveryService: processing %d updates", len(updates))

                for upd in updates:
                    self._process_single_update(upd)

        except Exception as exc:
            logger.error("Failed to sync Telegram updates: %s", exc)

        return self.repo.get_active_telegram_targets()

    def _process_single_update(self, upd: dict[str, Any]) -> None:
        """Parse individual Telegram update object and update chat database."""
        # 1. Bot member status changes (added/removed as admin from channel/group)
        if "my_chat_member" in upd:
            event = upd["my_chat_member"]
            chat = event.get("chat", {})
            new_status = event.get("new_chat_member", {}).get("status", "")
            chat_id = str(chat.get("id"))
            chat_type = str(chat.get("type", "channel"))
            title = str(chat.get("title") or chat.get("first_name") or chat_id)
            username = chat.get("username")

            if new_status in ("administrator", "member", "creator"):
                role = "publish_target" if chat_type in ("channel", "supergroup", "group") else "admin_alert"
                self.repo.upsert_telegram_chat(
                    chat_id=chat_id,
                    title=title,
                    chat_type=chat_type,
                    role=role,
                    username=username,
                    is_active=True,
                )
                logger.info("Discovered new active %s target: '%s' (ID: %s)", chat_type, title, chat_id)
            elif new_status in ("kicked", "left"):
                self.repo.deactivate_telegram_chat(chat_id)
                logger.info("Bot removed from %s '%s' (ID: %s), deactivated", chat_type, title, chat_id)

        # 2. Channel posts in connected channels
        elif "channel_post" in upd:
            chat = upd["channel_post"].get("chat", {})
            chat_id = str(chat.get("id"))
            title = str(chat.get("title") or chat_id)
            username = chat.get("username")
            chat_type = str(chat.get("type", "channel"))

            self.repo.upsert_telegram_chat(
                chat_id=chat_id,
                title=title,
                chat_type=chat_type,
                role="publish_target",
                username=username,
                is_active=True,
            )
            logger.info("Discovered channel from post: '%s' (ID: %s)", title, chat_id)

        # 3. Direct user messages (e.g. /start or /admin in private chat)
        elif "message" in upd:
            msg = upd["message"]
            chat = msg.get("chat", {})
            chat_id = str(chat.get("id"))
            chat_type = str(chat.get("type", "private"))
            title = str(chat.get("title") or f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip() or chat_id)
            username = chat.get("username")

            if chat_type == "private":
                self.repo.upsert_telegram_chat(
                    chat_id=chat_id,
                    title=title,
                    chat_type=chat_type,
                    role="admin_alert",
                    username=username,
                    is_active=True,
                )
                logger.info("Registered private admin chat for alerts: '%s' (ID: %s)", title, chat_id)
            elif chat_type in ("group", "supergroup"):
                self.repo.upsert_telegram_chat(
                    chat_id=chat_id,
                    title=title,
                    chat_type=chat_type,
                    role="publish_target",
                    username=username,
                    is_active=True,
                )
                logger.info("Discovered group target: '%s' (ID: %s)", title, chat_id)
