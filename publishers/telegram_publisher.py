"""Telegram channel publisher implementation.

Per SDD §3.6 and SRS FR-5.1.
Uses Telegram Bot API sendPhoto endpoint, enforces caption limits, and retries on failure.
"""

import html
import logging
from typing import Any
import httpx

from core.composer import ComposedPost
from core.resilience import retry_with_backoff
from publishers.base import PublishResult

logger = logging.getLogger(__name__)

# Telegram captions allow maximum 1024 characters
MAX_TELEGRAM_CAPTION_LEN = 1024


class TelegramPublisher:
    """Publishes clothing product posts with photo to one or more dynamic Telegram channels/groups."""

    def __init__(
        self,
        bot_token: str,
        channel_id: str | None = None,
        repo: Any | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.repo = repo
        self.timeout_seconds = timeout_seconds

    @property
    def platform_name(self) -> str:
        return "telegram"

    def publish(self, post: ComposedPost) -> PublishResult:
        """Publish photo and caption to all active Telegram channels."""
        target_ids: list[str] = []

        if self.repo is not None:
            try:
                active_targets = self.repo.get_active_telegram_targets()
                target_ids = [t.chat_id for t in active_targets]
            except Exception as exc:
                logger.warning("Failed to fetch dynamic Telegram channels from repository: %s", exc)

        # Fallback to configured channel_id if no dynamic targets stored yet
        if not target_ids and self.channel_id:
            target_ids = [self.channel_id]

        if not target_ids:
            logger.warning("Telegram publish skipped: no active channels configured or discovered.")
            return PublishResult(
                success=False,
                error="No active Telegram channels found. Add the bot to a channel or configure a channel ID.",
            )

        caption = self._format_caption(post)
        successful_ids: list[str] = []
        errors: list[str] = []

        for chat_id in target_ids:
            try:
                msg_id = self._send_photo_with_retry(post.photo_url, caption, chat_id=chat_id)
                successful_ids.append(f"{chat_id}:{msg_id}" if len(target_ids) > 1 else str(msg_id))
            except Exception as exc:
                logger.error("Telegram publish failed for channel %s (%s): %s", chat_id, post.title, exc)
                errors.append(f"{chat_id}: {exc}")

        if successful_ids:
            return PublishResult(success=True, platform_post_id=",".join(successful_ids))

        return PublishResult(success=False, error="; ".join(errors))

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=10.0, exceptions=(httpx.HTTPError,))
    def _send_photo_with_retry(self, photo_url: str, caption: str, chat_id: str | None = None) -> str:
        """Call Telegram Bot API sendPhoto with exponential backoff."""
        target_chat = chat_id or self.channel_id
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        payload: dict[str, Any] = {
            "chat_id": target_chat,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(url, json=payload)
            data = resp.json()

            if not resp.is_success or not data.get("ok"):
                error_desc = data.get("description") or resp.text
                raise httpx.HTTPStatusError(
                    f"Telegram API error {resp.status_code}: {error_desc}",
                    request=resp.request,
                    response=resp,
                )

            message_id = data.get("result", {}).get("message_id")
            return str(message_id)

    def _format_caption(self, post: ComposedPost) -> str:
        """Format post text with HTML tags and enforce 1024 char limit."""
        title_escaped = html.escape(post.title)
        brand_escaped = html.escape(post.source.upper())
        symbol = "$" if post.currency == "USD" else f"{post.currency} "
        price_str = f"{symbol}{post.price:,.2f}"

        lines = [
            f"<b>✨ {title_escaped} | {brand_escaped}</b>",
            "",
            html.escape(post.text.split("\n\n")[1] if "\n\n" in post.text else post.text),
            "",
            f"🏷 <b>Price:</b> {price_str}",
        ]

        if post.product_url:
            lines.extend(["", f'🔗 <a href="{html.escape(post.product_url)}">View Product</a>'])

        caption = "\n".join(lines)
        if len(caption) > MAX_TELEGRAM_CAPTION_LEN:
            caption = caption[: MAX_TELEGRAM_CAPTION_LEN - 3] + "..."

        return caption
