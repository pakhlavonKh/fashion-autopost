"""Telegram channel publisher implementation.

Per SDD §3.6 and SRS FR-5.1.
Uses Telegram Bot API sendPhoto endpoint, enforces caption limits, and retries on failure.
"""

import html
import json
import logging
from pathlib import Path
from typing import Any
import httpx

from core.composer import ComposedPost
from core.image_downloader import ImageDownloader
from core.resilience import retry_with_backoff
from publishers.base import PublishResult

logger = logging.getLogger(__name__)

# Telegram captions allow maximum 1024 characters
MAX_TELEGRAM_CAPTION_LEN = 1024

DEFAULT_BIO_FOOTER = (
    "Европейское качество\n"
    "Обращаться: @nigora_7\n"
    "Тел:+998998484044\n"
    "✨Отзывы: @otzivi_fashbou\n"
    "Товары в наличии: @vnalichiifash\n\n"
    "Наш Instagram:\n"
    "https://www.instagram.com/fashionnestboutique?igsh=Z29pN2tscmJhd3Mx\n\n"
    "Наш основной Telegram-канал:\n"
    "https://t.me/fashionalleyb"
)


class TelegramPublisher:
    """Publishes clothing product posts with photo or photo albums to Telegram channels/groups."""

    def __init__(
        self,
        bot_token: str,
        channel_id: str | None = None,
        repo: Any | None = None,
        timeout_seconds: float = 30.0,
        bio_footer: str | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.repo = repo
        self.timeout_seconds = timeout_seconds
        self.downloader = ImageDownloader(timeout_seconds=self.timeout_seconds)
        self.bio_footer = bio_footer or DEFAULT_BIO_FOOTER

    @property
    def platform_name(self) -> str:
        return "telegram"

    def publish(self, post: ComposedPost) -> PublishResult:
        """Publish photo or album and caption to all active Telegram channels."""
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

        # Determine all images to send (download all gallery images locally)
        candidate_urls = list(post.photo_urls) if getattr(post, "photo_urls", None) else []
        if not candidate_urls and post.photo_url:
            candidate_urls = [post.photo_url]

        downloaded_paths: list[str] = []
        for i, u in enumerate(candidate_urls[:8]):
            local_cand = Path(u)
            if local_cand.is_file():
                downloaded_paths.append(str(local_cand))
                continue
            try:
                downloaded = self.downloader.download(u, external_id=f"{post.title[:15]}_{i}")
                if downloaded and downloaded.is_file():
                    downloaded_paths.append(str(downloaded))
            except Exception as exc:
                logger.debug("Could not pre-download photo '%s' for Telegram: %s", u, exc)

        if not downloaded_paths and post.photo_url:
            downloaded_paths = [post.photo_url]

        for chat_id in target_ids:
            try:
                if len(downloaded_paths) > 1:
                    msg_id = self._send_media_group_with_retry(
                        downloaded_paths[:8],
                        caption,
                        chat_id=chat_id,
                    )
                else:
                    msg_id = self._send_photo_with_retry(
                        downloaded_paths[0],
                        caption,
                        chat_id=chat_id,
                    )
                successful_ids.append(f"{chat_id}:{msg_id}" if len(target_ids) > 1 else str(msg_id))
            except Exception as exc:
                logger.error("Telegram publish failed for channel %s (%s): %s", chat_id, post.title, exc)
                errors.append(f"{chat_id}: {exc}")

        if successful_ids:
            return PublishResult(success=True, platform_post_id=",".join(successful_ids))

        return PublishResult(success=False, error="; ".join(errors))

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=10.0, exceptions=(httpx.HTTPError,))
    def _send_media_group_with_retry(
        self,
        photos: list[str],
        caption: str,
        chat_id: str | None = None,
    ) -> str:
        """Call Telegram Bot API sendMediaGroup with exponential backoff for multiple photos."""
        target_chat = chat_id or self.channel_id
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMediaGroup"

        media_list = []
        files = {}
        for idx, photo_path_or_url in enumerate(photos):
            attach_name = f"photo{idx}"
            local_cand = Path(photo_path_or_url)
            if local_cand.is_file():
                photo_bytes = local_cand.read_bytes()
                filename = local_cand.name or f"photo{idx}.jpg"
                mime_type = "image/jpeg"
                if filename.lower().endswith(".webp") or (len(photo_bytes) >= 12 and photo_bytes[:4] == b"RIFF" and photo_bytes[8:12] == b"WEBP"):
                    mime_type = "image/webp"
                    if not filename.lower().endswith(".webp"):
                        filename = f"{local_cand.stem}.webp"
                elif filename.lower().endswith(".png") or (len(photo_bytes) >= 8 and photo_bytes[:8] == b"\x89PNG\r\n\x1a\n"):
                    mime_type = "image/png"
                    if not filename.lower().endswith(".png"):
                        filename = f"{local_cand.stem}.png"
                files[attach_name] = (filename, photo_bytes, mime_type)
                media_item: dict[str, Any] = {
                    "type": "photo",
                    "media": f"attach://{attach_name}",
                }
            else:
                media_item = {
                    "type": "photo",
                    "media": photo_path_or_url,
                }

            if idx == 0:
                media_item["caption"] = caption
                media_item["parse_mode"] = "HTML"

            media_list.append(media_item)

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(
                url,
                data={"chat_id": str(target_chat), "media": json.dumps(media_list)},
                files=files if files else None,
            )
            data = resp.json()
            if not resp.is_success or not data.get("ok"):
                error_desc = data.get("description") or resp.text
                raise httpx.HTTPStatusError(
                    f"Telegram API error {resp.status_code}: {error_desc}",
                    request=resp.request,
                    response=resp,
                )
            result = data.get("result", [])
            first_msg_id = result[0].get("message_id") if result and isinstance(result, list) else data.get("result", {}).get("message_id")
            return str(first_msg_id)

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=10.0, exceptions=(httpx.HTTPError,))
    def _send_photo_with_retry(
        self,
        photo_url: str,
        caption: str,
        chat_id: str | None = None,
    ) -> str:
        """Call Telegram Bot API sendPhoto with exponential backoff."""
        target_chat = chat_id or self.channel_id
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"

        local_candidate = Path(photo_url)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            if local_candidate.is_file():
                photo_bytes = local_candidate.read_bytes()
                filename = local_candidate.name or "photo.jpg"
                mime_type = "image/jpeg"
                if filename.lower().endswith(".webp") or (len(photo_bytes) >= 12 and photo_bytes[:4] == b"RIFF" and photo_bytes[8:12] == b"WEBP"):
                    mime_type = "image/webp"
                    if not filename.lower().endswith(".webp"):
                        filename = f"{local_candidate.stem}.webp"
                elif filename.lower().endswith(".png") or (len(photo_bytes) >= 8 and photo_bytes[:8] == b"\x89PNG\r\n\x1a\n"):
                    mime_type = "image/png"
                    if not filename.lower().endswith(".png"):
                        filename = f"{local_candidate.stem}.png"

                files = {"photo": (filename, photo_bytes, mime_type)}
                data = {
                    "chat_id": str(target_chat),
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                resp = client.post(url, data=data, files=files)
            else:
                payload = {
                    "chat_id": str(target_chat),
                    "photo": photo_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
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
        """Format post text matching client specification (1-to-1 bio)."""
        price_val = int(post.price) if post.price % 1 == 0 else f"{post.price:.2f}"
        price_display = f"{price_val}$" if post.currency == "USD" else f"{price_val} {post.currency}"
        title_clean = html.escape(post.title.strip())

        # Header format: Платье-78$
        header = f"{title_clean}-{price_display}"

        # Clean description without old headers
        desc = post.text
        if "\n\n" in desc:
            parts = desc.split("\n\n")
            if len(parts) > 1 and parts[0].startswith("✨"):
                desc = parts[1]
        desc_clean = html.escape(desc.strip())

        footer = (self.bio_footer or DEFAULT_BIO_FOOTER).strip()

        caption = f"{header}\n{desc_clean}\n\n{footer}"
        if len(caption) > MAX_TELEGRAM_CAPTION_LEN:
            caption = caption[: MAX_TELEGRAM_CAPTION_LEN - 3] + "..."

        return caption
