"""Instagram Graph API publisher implementation.

Per SDD §3.6 and SRS FR-5.2, FR-5.3.
Publishes a single feed photo or a carousel and waits until Meta finishes processing.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from config.app_config import DEFAULT_INSTAGRAM_CAPTION_FOOTER
from core.composer import ComposedPost
from core.image_downloader import ImageDownloader
from core.resilience import retry_with_backoff
from publishers.base import PublishResult
from publishers.image_hosting import ImageHostingService, LitterboxImageHost
from publishers.instagram_media import prepare_feed_jpeg

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v23.0"
# Instagram API with Facebook Login  (requires Page + Business account linkage)
GRAPH_FACEBOOK_BASE = "https://graph.facebook.com"
# Instagram API with Instagram Login (works with any Professional account directly)
GRAPH_INSTAGRAM_BASE = "https://graph.instagram.com"

MAX_INSTAGRAM_CAPTION_LEN = 2200
MAX_CAROUSEL_ITEMS = 10
MAX_HASHTAGS = 30
CONTAINER_READY_TIMEOUT_SECONDS = 45.0

BASE_HASHTAGS = ("fashion", "style", "outfit", "одежда", "стиль", "lookoftheday")


class InstagramPublisher:
    """Publishes posts to an Instagram Professional account via the Graph API."""

    def __init__(
        self,
        access_token: str,
        account_id: str,
        image_host: ImageHostingService | None = None,
        timeout_seconds: float = 45.0,
        caption_footer: str | None = None,
        username: str | None = None,
        use_instagram_login: bool | None = None,
    ) -> None:
        self.access_token = access_token
        self.account_id = account_id
        self.image_host = image_host or LitterboxImageHost()
        self.timeout_seconds = timeout_seconds
        self.caption_footer = caption_footer if caption_footer is not None else DEFAULT_INSTAGRAM_CAPTION_FOOTER
        self.username = (username or "").strip().lstrip("@") or None
        self.downloader = ImageDownloader(timeout_seconds=self.timeout_seconds)
        # Auto-detect: IGAA tokens come from graph.instagram.com (Instagram Login)
        if use_instagram_login is None:
            use_instagram_login = access_token.startswith("IGAA")
        self._base_url = GRAPH_INSTAGRAM_BASE if use_instagram_login else GRAPH_FACEBOOK_BASE
        logger.info(
            "InstagramPublisher using %s (use_instagram_login=%s)",
            self._base_url, use_instagram_login,
        )

    @property
    def platform_name(self) -> str:
        return "instagram"

    def format_caption(self, post: ComposedPost) -> str:
        """Build the feed caption: name and price, description, contacts, hashtags."""
        return format_instagram_caption(post, self.caption_footer)

    def publish(self, post: ComposedPost) -> PublishResult:
        """Create a feed photo or carousel and publish it."""
        try:
            target = f"@{self.username}" if self.username else self.account_id
            caption = self.format_caption(post)
            public_urls = self._public_image_urls(post)
            if len(public_urls) == 1:
                container_id = self._create_image_container(public_urls[0], caption)
            else:
                child_ids = [
                    self._create_carousel_child(url)
                    for url in public_urls
                ]
                container_id = self._create_carousel_container(child_ids, caption)

            self._wait_until_ready(container_id)
            post_id = self._publish_container(container_id)
            logger.info("Instagram post %s published to %s", post_id, target)
            return PublishResult(success=True, platform_post_id=str(post_id))
        except Exception as exc:
            logger.error("Instagram publish failed for %s: %s", post.title, exc)
            return PublishResult(success=False, error=str(exc))

    def _public_image_urls(self, post: ComposedPost) -> list[str]:
        sources = list(post.photo_urls) if post.photo_urls else []
        if not sources and post.photo_url:
            sources = [post.photo_url]

        prepared: list[Path] = []
        for index, source in enumerate(sources[:MAX_CAROUSEL_ITEMS]):
            local = self._resolve_local_image(source, post.title, index)
            if local is None:
                continue
            dest = local.with_name(f"{local.stem}_ig.jpg")
            try:
                prepared.append(prepare_feed_jpeg(local, dest))
            except Exception as exc:
                logger.warning("Skipping Instagram image %s: %s", source, exc)

        if not prepared:
            raise RuntimeError(f"No usable photos for Instagram post '{post.title}'")

        urls = [self.image_host.ensure_public_url(str(path)) for path in prepared]
        if len(urls) > MAX_CAROUSEL_ITEMS:
            urls = urls[:MAX_CAROUSEL_ITEMS]
        return urls

    def _resolve_local_image(self, source: str, title: str, index: int) -> Path | None:
        local = Path(source)
        if local.is_file():
            return local
        if source.startswith("http://") or source.startswith("https://"):
            downloaded = self.downloader.download(source, external_id=f"{title[:15]}_{index}")
            if downloaded and downloaded.is_file():
                return downloaded
        logger.warning("Instagram could not read image: %s", source)
        return None

    def _create_image_container(self, image_url: str, caption: str) -> str:
        return self._create_container({
            "image_url": image_url,
            "caption": caption,
        })

    def _create_carousel_child(self, image_url: str) -> str:
        container_id = self._create_container({
            "image_url": image_url,
            "is_carousel_item": "true",
        })
        self._wait_until_ready(container_id)
        return container_id

    def _create_carousel_container(self, child_ids: list[str], caption: str) -> str:
        return self._create_container({
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
        })

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=15.0, exceptions=(httpx.HTTPError,))
    def _create_container(self, fields: dict[str, Any]) -> str:
        url = f"{self._base_url}/{GRAPH_API_VERSION}/{self.account_id}/media"
        data = self._graph_response("POST", url, fields)
        return str(data["id"])

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=15.0, exceptions=(httpx.HTTPError,))
    def _publish_container(self, container_id: str) -> str:
        url = f"{self._base_url}/{GRAPH_API_VERSION}/{self.account_id}/media_publish"
        data = self._graph_response("POST", url, {"creation_id": container_id})
        return str(data["id"])

    def _wait_until_ready(self, container_id: str) -> None:
        deadline = time.monotonic() + CONTAINER_READY_TIMEOUT_SECONDS
        status = "IN_PROGRESS"
        detail = ""
        while time.monotonic() <= deadline:
            status, detail = self._container_status(container_id)
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise RuntimeError(
                    f"Instagram container {container_id} is {status}: {detail or 'no detail'}"
                )
            time.sleep(2)
        raise TimeoutError(
            f"Instagram container {container_id} was not ready (last status {status})"
        )

    def _container_status(self, container_id: str) -> tuple[str, str]:
        url = f"{self._base_url}/{GRAPH_API_VERSION}/{container_id}"
        data = self._graph_response("GET", url, {"fields": "status_code,status"})
        status_code = str(data.get("status_code") or "")
        detail = ""
        raw_status = data.get("status")
        if isinstance(raw_status, str):
            detail = raw_status
        return status_code, detail

    def _graph_response(self, method: str, url: str, fields: dict[str, Any]) -> dict[str, Any]:
        payload = dict(fields)
        payload["access_token"] = self.access_token
        with httpx.Client(timeout=self.timeout_seconds) as client:
            if method == "GET":
                resp = client.get(url, params=payload)
            else:
                resp = client.post(url, data=payload)
            try:
                data = resp.json()
            except Exception:
                data = {}

        if not isinstance(data, dict):
            data = {}
        if not resp.is_success or ("id" not in data and "status_code" not in data):
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            message = error.get("message") or resp.text
            code = error.get("code")
            raise httpx.HTTPStatusError(
                f"Instagram API error {code}: {message}",
                request=resp.request,
                response=resp,
            )
        return data


def format_instagram_caption(post: ComposedPost, footer: str | None = None) -> str:
    """Format a boutique caption that matches the Telegram card, within Instagram limits."""
    price_val = int(post.price) if post.price % 1 == 0 else f"{post.price:.2f}"
    price_display = f"{price_val}$" if post.currency == "USD" else f"{price_val} {post.currency}"
    header = f"{post.title.strip()}-{price_display}"
    description = _extract_description(post)
    contact = (footer if footer is not None else DEFAULT_INSTAGRAM_CAPTION_FOOTER).strip()
    tags = _hashtags(post.source)

    caption = _assemble_caption(header, description, contact, tags)
    if len(caption) <= MAX_INSTAGRAM_CAPTION_LEN:
        return caption

    overflow = len(caption) - (MAX_INSTAGRAM_CAPTION_LEN - 3)
    trimmed = description[: max(0, len(description) - overflow)].rstrip()
    if trimmed:
        trimmed += "..."
    return _assemble_caption(header, trimmed, contact, tags)[:MAX_INSTAGRAM_CAPTION_LEN]


def _assemble_caption(header: str, description: str, contact: str, tags: str) -> str:
    body = f"{header}\n{description}" if description else header
    chunks = [body]
    if contact:
        chunks.append(contact)
    if tags:
        chunks.append(tags)
    return "\n\n".join(chunks)


def _extract_description(post: ComposedPost) -> str:
    chunks = [part.strip() for part in post.text.split("\n\n") if part.strip()]
    body: list[str] = []
    for chunk in chunks:
        if chunk.startswith("✨") or chunk.startswith("🏷") or chunk.startswith("🔗"):
            continue
        body.append(chunk)
    return "\n\n".join(body).strip()


def _hashtags(source: str) -> str:
    tags: list[str] = []
    brand = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_]", "", source.lower())
    if brand:
        tags.append(f"#{brand}")
    tags.extend(f"#{tag}" for tag in BASE_HASHTAGS)
    unique = list(dict.fromkeys(tags))[:MAX_HASHTAGS]
    return " ".join(unique)
