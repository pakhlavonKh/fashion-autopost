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
    """Publishes clothing product posts with photo to a Telegram channel or group."""

    def __init__(self, bot_token: str, channel_id: str, timeout_seconds: float = 30.0) -> None:
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.timeout_seconds = timeout_seconds

    @property
    def platform_name(self) -> str:
        return "telegram"

    def publish(self, post: ComposedPost) -> PublishResult:
        """Publish photo and caption to Telegram channel."""
        caption = self._format_caption(post)

        try:
            message_id = self._send_photo_with_retry(post.photo_url, caption)
            return PublishResult(success=True, platform_post_id=str(message_id))
        except Exception as exc:
            logger.error("Telegram publish failed for %s: %s", post.title, exc)
            return PublishResult(success=False, error=str(exc))

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=10.0, exceptions=(httpx.HTTPError,))
    def _send_photo_with_retry(self, photo_url: str, caption: str) -> str:
        """Call Telegram Bot API sendPhoto with exponential backoff."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        payload: dict[str, Any] = {
            "chat_id": self.channel_id,
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
