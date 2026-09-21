"""Admin notification system for critical errors.

Per SDD §3.10 and SRS FR-7.3 & §10.5 (Open Question 5: Critical error alert destination).
Configurable to send alerts via Telegram, print to console, or broadcast to multiple sinks.
"""

from datetime import datetime, timezone
import logging
from typing import Protocol, runtime_checkable
import httpx

logger = logging.getLogger(__name__)


@runtime_checkable
class AdminNotifier(Protocol):
    """Protocol for dispatching critical error alerts to system administrators."""

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        """Send a critical alert notification."""
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


class TelegramAdminNotifier:
    """Sends critical alert messages to a designated Telegram admin chat ID."""

    def __init__(self, bot_token: str, admin_chat_id: str, timeout_seconds: float = 10.0) -> None:
        self.bot_token = bot_token
        self.admin_chat_id = admin_chat_id
        self.timeout_seconds = timeout_seconds

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        if not self.bot_token or not self.admin_chat_id or "mock" in self.bot_token.lower():
            logger.info("TelegramAdminNotifier: skipping send in mock/unconfigured mode.")
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
        payload = {
            "chat_id": self.admin_chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                client.post(url, json=payload)
        except Exception as exc:
            logger.error("Failed to send Telegram admin notification: %s", exc)


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
