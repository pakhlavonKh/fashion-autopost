"""Admin notification system for critical errors.

Per SDD §3.10 and SRS FR-7.3 & §10.5 (Open Question 5: Critical error alert destination).
Configurable to send alerts via Telegram, print to console, or broadcast to multiple sinks.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Protocol, runtime_checkable
import httpx

logger = logging.getLogger(__name__)


@runtime_checkable
class AdminNotifier(Protocol):
    """Protocol for dispatching critical error alerts and approval requests to system administrators."""

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        """Send a critical alert notification."""
        ...

    def notify_duplicate_warning(
        self,
        approval_id: int,
        title: str,
        source: str,
        price: str,
        previous_date: str,
        previous_url: str | None,
        proposed_date: str,
    ) -> None:
        """Send duplicate publication warning and approval buttons."""
        ...

    def notify_duplicate_skipped(self, title: str, product_url: str | None = None) -> None:
        """Tell the admin a product was skipped because the channel already carried it."""
        ...


class ConsoleAdminNotifier:
    """Logs critical alerts directly to console/logger."""

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        item_info = f" [Item: {external_id}]" if external_id else ""
        logger.critical(
            "🚨 [ADMIN ALERT] Critical failure in stage '%s'%s: %s",
            stage,
            item_info,
            message,
        )

    def notify_duplicate_warning(
        self,
        approval_id: int,
        title: str,
        source: str,
        price: str,
        previous_date: str,
        previous_url: str | None,
        proposed_date: str,
    ) -> None:
        logger.warning(
            "⚠️ [DUPLICATE WARNING] Product '%s' (Approval #%s) already published on %s. Awaiting Admin Approval.",
            title,
            approval_id,
            previous_date,
        )

    def notify_duplicate_skipped(self, title: str, product_url: str | None = None) -> None:
        logger.info(
            "↷ [DUPLICATE SKIPPED] Product '%s' (%s) was published before, taking the next in the queue.",
            title,
            product_url or "no product URL",
        )


class TelegramAdminNotifier:
    """Sends critical alert messages to designated or dynamically discovered Telegram admin chat IDs."""

    def __init__(
        self,
        bot_token: str,
        admin_chat_id: str | None = None,
        repo: Any | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.bot_token = bot_token
        self.admin_chat_id = admin_chat_id
        self.repo = repo
        self.timeout_seconds = timeout_seconds

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        if not self.bot_token or "mock" in self.bot_token.lower():
            logger.info("TelegramAdminNotifier: skipping send in mock mode.")
            return

        target_chats: list[str] = []
        if self.repo is not None:
            try:
                active_admins = self.repo.get_active_admin_chats()
                target_chats = [c.chat_id for c in active_admins]
            except Exception as exc:
                logger.warning("Failed to fetch admin chats from repository: %s", exc)

        if not target_chats and self.admin_chat_id and "mock" not in self.admin_chat_id.lower():
            target_chats = [self.admin_chat_id]

        if not target_chats:
            logger.debug("TelegramAdminNotifier: no admin chats registered, skipping.")
            return

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        item_text = f"\n<b>Affected Item:</b> <code>{external_id}</code>" if external_id else ""
        text = (
            f"🚨 <b>CRITICAL SYSTEM ALERT</b>\n"
            f"<b>Time:</b> {now_utc}\n"
            f"<b>Stage:</b> {stage}"
            f"{item_text}\n"
            f"<b>Error:</b>\n<pre>{message[:1500]}</pre>"
        )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                for chat_id in target_chats:
                    payload = {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    }
                    try:
                        client.post(url, json=payload)
                    except Exception as chat_exc:
                        logger.error("Failed to send alert to admin chat %s: %s", chat_id, chat_exc)
        except Exception as exc:
            logger.error("Failed to send Telegram admin notification: %s", exc)

    def notify_duplicate_warning(
        self,
        approval_id: int,
        title: str,
        source: str,
        price: str,
        previous_date: str,
        previous_url: str | None,
        proposed_date: str,
    ) -> None:
        if not self.bot_token or "mock" in self.bot_token.lower():
            logger.info("TelegramAdminNotifier: skipping duplicate warning in mock mode.")
            return

        target_chats: list[str] = []
        if self.repo is not None:
            try:
                active_admins = self.repo.get_active_admin_chats()
                target_chats = [c.chat_id for c in active_admins]
            except Exception as exc:
                logger.warning("Failed to fetch admin chats from repository: %s", exc)

        if not target_chats and self.admin_chat_id and "mock" not in self.admin_chat_id.lower():
            target_chats = [self.admin_chat_id]

        if not target_chats:
            logger.debug("TelegramAdminNotifier: no admin chats registered for duplicate warning, skipping.")
            return

        prev_link_text = f'\n<b>Предыдущий пост:</b> <a href="{previous_url}">{previous_url}</a>' if previous_url else ""
        text = (
            f"⚠️ <b>ВНИМАНИЕ: ПОВТОРНАЯ ПУБЛИКАЦИЯ</b>\n\n"
            f"Товар уже публиковался в Telegram-канале!\n\n"
            f"🛍 <b>Товар:</b> {title}\n"
            f"🏷 <b>Бренд:</b> {source.upper()}\n"
            f"💰 <b>Цена:</b> {price}\n"
            f"📅 <b>Предыдущая публикация:</b> {previous_date}{prev_link_text}\n"
            f"🕒 <b>Предлагаемое время:</b> {proposed_date}\n\n"
            f"Опубликовать товар повторно или выбрать следующий?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Опубликовать повторно", "callback_data": f"dup_approve:{approval_id}"},
                    {"text": "❌ Отклонить", "callback_data": f"dup_reject:{approval_id}"},
                ]
            ]
        }

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                for chat_id in target_chats:
                    payload = {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup,
                        "disable_web_page_preview": True,
                    }
                    try:
                        client.post(url, json=payload)
                    except Exception as chat_exc:
                        logger.error("Failed to send duplicate warning to admin chat %s: %s", chat_id, chat_exc)
        except Exception as exc:
            logger.error("Failed to send Telegram duplicate warning: %s", exc)

    def notify_duplicate_skipped(self, title: str, product_url: str | None = None) -> None:
        target_chats = self._admin_chats()
        if not target_chats:
            return

        link = f'\n🔗 <a href="{product_url}">{product_url}</a>' if product_url else ""
        text = (
            f"↷ <b>ПРОПУЩЕН ПОВТОР</b>\n\n"
            f"Товар <b>{title}</b> уже публиковался в канале ранее, "
            f"поэтому публикую следующий товар из очереди.{link}"
        )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                for chat_id in target_chats:
                    try:
                        client.post(
                            url,
                            json={
                                "chat_id": chat_id,
                                "text": text,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True,
                            },
                        )
                    except Exception as chat_exc:
                        logger.error("Failed to send the skipped-duplicate notice to %s: %s", chat_id, chat_exc)
        except Exception as exc:
            logger.error("Failed to send the Telegram skipped-duplicate notice: %s", exc)

    def _admin_chats(self) -> list[str]:
        """Chats that receive admin notices, empty when there is nowhere to send."""
        if not self.bot_token or "mock" in self.bot_token.lower():
            logger.info("TelegramAdminNotifier: skipping send in mock mode.")
            return []

        chats: list[str] = []
        if self.repo is not None:
            try:
                chats = [chat.chat_id for chat in self.repo.get_active_admin_chats()]
            except Exception as exc:
                logger.warning("Failed to fetch admin chats from repository: %s", exc)

        if not chats and self.admin_chat_id and "mock" not in self.admin_chat_id.lower():
            chats = [self.admin_chat_id]
        return chats


class CompositeAdminNotifier:
    """Dispatches notifications to multiple notifiers in sequence."""

    def __init__(self, notifiers: list[AdminNotifier]) -> None:
        self.notifiers = notifiers

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        for notifier in self.notifiers:
            try:
                notifier.notify_critical(stage, message, external_id)
            except Exception as exc:
                logger.warning("Notifier %s failed: %s", type(notifier).__name__, exc)

    def notify_duplicate_warning(
        self,
        approval_id: int,
        title: str,
        source: str,
        price: str,
        previous_date: str,
        previous_url: str | None,
        proposed_date: str,
    ) -> None:
        for notifier in self.notifiers:
            if hasattr(notifier, "notify_duplicate_warning"):
                try:
                    notifier.notify_duplicate_warning(
                        approval_id, title, source, price, previous_date, previous_url, proposed_date
                    )
                except Exception as exc:
                    logger.warning("Notifier %s failed duplicate warning: %s", type(notifier).__name__, exc)

    def notify_duplicate_skipped(self, title: str, product_url: str | None = None) -> None:
        for notifier in self.notifiers:
            if hasattr(notifier, "notify_duplicate_skipped"):
                try:
                    notifier.notify_duplicate_skipped(title, product_url)
                except Exception as exc:
                    logger.warning("Notifier %s failed skipped notice: %s", type(notifier).__name__, exc)

