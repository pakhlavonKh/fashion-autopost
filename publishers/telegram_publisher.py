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
from core.pricing import whole_price
from core.image_downloader import ImageDownloader, prepare_original_jpeg, unique_images
from core.resilience import retry_with_backoff
from publishers.base import PublishResult

logger = logging.getLogger(__name__)

# Telegram captions allow maximum 1024 characters
MAX_TELEGRAM_CAPTION_LEN = 1024
# sendMediaGroup accepts at most 10 photos. Further shots go out as the next album.
TELEGRAM_ALBUM_SIZE = 10

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
        self._public_usernames: dict[str, str] = {}
        self._ids_by_username: dict[str, str] = {}
        self._canonical_ids: dict[str, str] = {}

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

        # Include all configured channel_ids (supports comma-separated list, e.g. group ID + channel username)
        if self.channel_id:
            configured_ids = [
                cid.strip() for cid in str(self.channel_id).split(",") if cid.strip()
            ]
            for cid in configured_ids:
                if cid not in target_ids:
                    target_ids.append(cid)

        target_ids = self._unique_chat_ids(target_ids)

        if not target_ids:
            logger.warning("Telegram publish skipped: no active channels configured or discovered.")
            return PublishResult(
                success=False,
                error="No active Telegram channels found. Add the bot to a channel or configure a channel ID.",
            )

        caption = self._format_caption(post)
        successful_ids: list[str] = []
        public_links: list[str] = []
        errors: list[str] = []

        # Determine all images to send (download all gallery images locally)
        candidate_urls = list(post.photo_urls) if getattr(post, "photo_urls", None) else []
        if not candidate_urls and post.photo_url:
            candidate_urls = [post.photo_url]

        downloaded_paths: list[str] = []
        for i, u in enumerate(candidate_urls):
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
        downloaded_paths = self._uniform_slides(downloaded_paths)

        for chat_id in target_ids:
            try:
                msg_id = self._send_all_photos(downloaded_paths, caption, chat_id)
                successful_ids.append(f"{chat_id}:{msg_id}" if len(target_ids) > 1 else str(msg_id))
                username = self._public_usernames.get(chat_id) or (
                    chat_id[1:] if chat_id.startswith("@") else None
                )
                if username and msg_id and msg_id != "None":
                    link = f"https://t.me/{username}/{msg_id}"
                    if link not in public_links:
                        public_links.append(link)
            except Exception as exc:
                logger.error("Telegram publish failed for channel %s (%s): %s", chat_id, post.title, exc)
                errors.append(f"{chat_id}: {exc}")

        if successful_ids:
            if not public_links:
                fallback_username = None
                if self.bio_footer:
                    import re
                    match = re.search(r"https://t\.me/([A-Za-z0-9_]+)", self.bio_footer)
                    if match:
                        fallback_username = match.group(1)
                if not fallback_username and self.channel_id and str(self.channel_id).startswith("@"):
                    fallback_username = str(self.channel_id)[1:]

                for sid in successful_ids:
                    if ":" in sid:
                        cid, mid = sid.split(":", 1)
                    else:
                        cid, mid = target_ids[0], sid
                    if fallback_username and mid and mid != "None":
                        public_links.append(f"https://t.me/{fallback_username}/{mid}")
                        break
                    elif mid and mid != "None":
                        cid_str = str(cid).strip()
                        if cid_str.startswith("-100"):
                            public_links.append(f"https://t.me/c/{cid_str[4:]}/{mid}")
                        else:
                            public_links.append(f"https://t.me/fashionalleyb/{mid}")
                        break

            return PublishResult(
                success=True,
                platform_post_id=",".join(successful_ids),
                links=tuple(public_links),
            )

        return PublishResult(success=False, error="; ".join(errors))

    def _unique_chat_ids(self, chat_ids: list[str]) -> list[str]:
        """One send per chat. @username and the numeric id of a channel are the same chat."""
        unique: list[str] = []
        seen: set[str] = set()
        for chat_id in chat_ids:
            canonical = self._canonical_chat_id(chat_id)
            alias = self._alias_chat_id(canonical)
            if canonical in seen or (alias is not None and alias in seen):
                logger.info(
                    "Skipping Telegram destination %s: the post already goes to this chat as %s",
                    chat_id,
                    canonical,
                )
                continue
            seen.add(canonical)
            if alias is not None:
                seen.add(alias)
            unique.append(canonical)
        return unique

    def _canonical_chat_id(self, chat_id: str) -> str:
        """Numeric chat id for a destination. Resolved once, then reused."""
        key = chat_id.strip()
        cached = self._canonical_ids.get(key)
        if cached is not None:
            return cached
        resolved = self._resolve_chat_id(key)
        self._canonical_ids[key] = resolved
        return resolved

    def _resolve_chat_id(self, chat_id: str) -> str:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    f"https://api.telegram.org/bot{self.bot_token}/getChat",
                    params={"chat_id": chat_id},
                )
                data = response.json()
            result = data.get("result") if isinstance(data, dict) else None
            if data.get("ok") and isinstance(result, dict) and result.get("id") is not None:
                canonical = str(result["id"])
                username = str(result.get("username") or "")
                if username:
                    self._public_usernames[canonical] = username
                    self._ids_by_username[username.casefold()] = canonical
                return canonical
        except Exception as exc:
            logger.warning("Could not resolve Telegram chat %s: %s", chat_id, exc)
        # Telegram did not answer. A handle already seen on a resolved chat is that same chat.
        if chat_id.startswith("@"):
            return self._ids_by_username.get(chat_id[1:].casefold()) or chat_id.casefold()
        return chat_id

    def _alias_chat_id(self, canonical: str) -> str | None:
        """The other way this chat can be written: @handle for an id, or the id for a @handle."""
        username = self._public_usernames.get(canonical)
        if username:
            return f"@{username.casefold()}"
        if canonical.startswith("@"):
            return self._ids_by_username.get(canonical[1:].casefold())
        return None

    def _uniform_slides(self, photos: list[str]) -> list[str]:
        """High-resolution JPEGs in the store's own frame. Duplicate pictures are removed."""
        local_paths = [Path(photo) for photo in photos if Path(photo).is_file()]
        kept = {str(path.resolve()) for path in unique_images(local_paths)} if local_paths else set()
        slides: list[str] = []
        seen: set[str] = set()
        for photo in photos:
            path = Path(photo)
            if path.is_file():
                resolved = str(path.resolve())
                if resolved not in kept or resolved in seen:
                    continue
                seen.add(resolved)
                dest = path.with_name(f"{path.stem}_tg.jpg")
                try:
                    slides.append(str(prepare_original_jpeg(path, dest)))
                except Exception as exc:
                    logger.debug("Sending the original file for Telegram, prepare failed: %s", exc)
                    slides.append(photo)
            elif photo not in seen:
                seen.add(photo)
                slides.append(photo)
        return slides

    def _send_all_photos(self, photos: list[str], caption: str, chat_id: str) -> str:
        """Send every photo. Telegram albums hold 10, so the rest follow as further albums.

        The sales caption is on the first album only.
        """
        if not photos:
            raise RuntimeError("No photos to send")
        first_id = ""
        for index in range(0, len(photos), TELEGRAM_ALBUM_SIZE):
            batch = photos[index:index + TELEGRAM_ALBUM_SIZE]
            batch_caption = caption if index == 0 else ""
            if len(batch) > 1:
                msg_id = self._send_media_group_with_retry(batch, batch_caption, chat_id=chat_id)
            else:
                msg_id = self._send_photo_with_retry(batch[0], batch_caption, chat_id=chat_id)
            if not first_id:
                first_id = msg_id
        return first_id

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=10.0, exceptions=(httpx.HTTPError,))
    def _send_media_group_with_retry(
        self,
        photos: list[str],
        caption: str,
        chat_id: str | None = None,
    ) -> str:
        """Call Telegram Bot API sendMediaGroup with exponential backoff for multiple photos."""
        target_chat = chat_id or (str(self.channel_id).split(",")[0].strip() if self.channel_id else None)
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

            if idx == 0 and caption:
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
        target_chat = chat_id or (str(self.channel_id).split(",")[0].strip() if self.channel_id else None)
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
                data = {"chat_id": str(target_chat)}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = "HTML"
                resp = client.post(url, data=data, files=files)
            else:
                payload = {
                    "chat_id": str(target_chat),
                    "photo": photo_url,
                }
                if caption:
                    payload["caption"] = caption
                    payload["parse_mode"] = "HTML"
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
        price_val = int(whole_price(post.price))
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

        # If this post was created with an explicit custom description (admin manual post),
        # do NOT auto-prepend a header (e.g. Плетёная сумка с короткой ручкой-500$).
        # The admin provides the exact text for Part 1, followed by footer (Part 2).
        if getattr(post, "is_custom_description", False):
            caption = f"{desc_clean}\n\n{footer}" if desc_clean else footer
            if len(caption) > MAX_TELEGRAM_CAPTION_LEN:
                caption = caption[: MAX_TELEGRAM_CAPTION_LEN - 3] + "..."
            return caption

        first_lines = [line.strip() for line in desc_clean.split("\n") if line.strip()][:4]
        has_header_already = (
            desc_clean.lower().startswith(header.lower())
            or (title_clean and desc_clean.lower().startswith(title_clean.lower() + "-"))
            or any("-" in line and any(cur in line for cur in ("$", "€", "сум", "USD", "EUR")) for line in first_lines)
        )

        if desc_clean:
            if has_header_already:
                caption = f"{desc_clean}\n\n{footer}"
            else:
                caption = f"{header}\n{desc_clean}\n\n{footer}"
        else:
            caption = f"{header}\n\n{footer}"
        if len(caption) > MAX_TELEGRAM_CAPTION_LEN:
            caption = caption[: MAX_TELEGRAM_CAPTION_LEN - 3] + "..."

        return caption
