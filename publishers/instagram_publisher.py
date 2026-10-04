"""Instagram Graph API publisher implementation.

Per SDD §3.6 and SRS FR-5.2, FR-5.3.
Publishes a single feed photo or a carousel. A carousel opens on a cover
collage, then the original store photos follow in the Telegram order, ending
with the product front, back and close-up. A story collage of the same photos
follows, linked to the Telegram post and filed into the Highlight for the
garment category.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, TypeVar

import httpx

from config.app_config import DEFAULT_INSTAGRAM_CAPTION_FOOTER
from core.composer import ComposedPost
from core.pricing import whole_price
from core.image_downloader import ImageDownloader, unique_images
from core.product_facts import is_footwear
from core.resilience import retry_with_backoff
from publishers.base import PublishResult
from publishers.image_hosting import ImageHostingService, LitterboxImageHost
from publishers.instagram_media import prepare_feed_jpeg, render_feed_collage
from publishers.instagram_private_story import InstagramPrivateStory
from publishers.instagram_story import (
    LINK_LABEL,
    detect_highlight,
    format_story_price,
    product_description,
    quality_line_from_footer,
    render_story_collage,
    story_link_url,
)

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v23.0"
_FETCH_URL = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
# Instagram API with Facebook Login  (requires Page + Business account linkage)
GRAPH_FACEBOOK_BASE = "https://graph.facebook.com"
# Instagram API with Instagram Login (works with any Professional account directly)
GRAPH_INSTAGRAM_BASE = "https://graph.instagram.com"

MAX_INSTAGRAM_CAPTION_LEN = 2200
MAX_CAROUSEL_ITEMS = 10
PRODUCT_ANGLE_SLIDES = 3
CONTAINER_READY_TIMEOUT_SECONDS = 90.0

_T = TypeVar("_T")


class InstagramMediaFetchError(RuntimeError):
    """Instagram error 9004: its crawler could not download the image URL."""

    def __init__(self, message: str, image_url: str | None = None) -> None:
        super().__init__(message)
        self.image_url = image_url


class InstagramPublisher:
    """Publishes posts to an Instagram Professional account via the Graph API."""

    def __init__(
        self,
        access_token: str,
        account_id: str,
        image_host: ImageHostingService | None = None,
        timeout_seconds: float = 120.0,
        caption_footer: str | None = None,
        username: str | None = None,
        use_instagram_login: bool | None = None,
        session_id: str | None = None,
        private_story: InstagramPrivateStory | None = None,
        login: str | None = None,
        password: str | None = None,
        repo: Any | None = None,
        llm: Any | None = None,
        story_worker: Any | None = None,
    ) -> None:
        self.access_token = access_token
        self.account_id = account_id
        self.image_host = image_host or LitterboxImageHost()
        self.timeout_seconds = timeout_seconds
        self.caption_footer = caption_footer if caption_footer is not None else DEFAULT_INSTAGRAM_CAPTION_FOOTER
        self.username = (username or "").strip().lstrip("@") or None
        self.repo = repo
        self.llm = llm
        if private_story is not None:
            self.private_story: InstagramPrivateStory | None = private_story
        elif (login and password) or (session_id and session_id.strip()):
            self.private_story = InstagramPrivateStory(
                session_id or "",
                login=login or "",
                password=password or "",
            )
        else:
            self.private_story = None
        if story_worker is not None:
            self.story_worker = story_worker
        elif self.repo is not None and self.llm is not None:
            from publishers.playwright_story_worker import PlaywrightStoryWorker
            self.story_worker = PlaywrightStoryWorker(
                repo=self.repo,
                llm=self.llm,
                account_id=self.account_id,
                private_story=self.private_story,
            )
        else:
            self.story_worker = None
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

    def format_caption(self, post: ComposedPost, *, carousel: bool = False) -> str:
        """Build the feed caption: name and price, description, contacts."""
        footer = self.caption_footer
        if carousel:
            footer = footer_for_carousel(footer)
        return format_instagram_caption(post, footer)

    def publish(self, post: ComposedPost) -> PublishResult:
        """Publish the feed post, then the story collage of the same photos."""
        try:
            target = f"@{self.username}" if self.username else self.account_id
            originals, prepared = self._prepare_local_images(post)
            slides = self._with_cover_collage(originals, prepared)
            public_urls = [self.image_host.ensure_public_url(str(path)) for path in slides]
            post_id = self._publish_feed(
                public_urls,
                self.format_caption(post, carousel=len(public_urls) > 1),
                slides,
            )
            logger.info("Instagram post %s published to %s", post_id, target)
        except Exception as exc:
            logger.error("Instagram publish failed for %s: %s", post.title, exc)
            return PublishResult(success=False, error=str(exc))

        self._publish_story_and_highlight(post, originals)
        return PublishResult(success=True, platform_post_id=str(post_id))

    def _publish_feed(self, public_urls: list[str], caption: str, sources: list[Path] | None = None) -> str:
        """Create the feed container, re-uploading any photo Instagram cannot download."""
        urls = list(public_urls)
        paths = list(sources or [])
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self._publish_ready(urls, caption, paths)
            except InstagramMediaFetchError as exc:
                last_error = exc
                if attempt == 2:
                    break
                logger.warning("Instagram could not fetch a photo (attempt %s): %s", attempt + 1, exc)
                urls = self._rehost_failed_set(urls, paths, exc.image_url)
        raise last_error or RuntimeError("Instagram could not fetch the photos")

    def _publish_ready(self, urls: list[str], caption: str, paths: list[Path]) -> str:
        if len(urls) == 1:
            container_id = self._create_image_container(urls[0], caption)
        else:
            child_ids: list[str] = []
            for index, url in enumerate(urls):
                try:
                    child_ids.append(self._create_carousel_child(url))
                except InstagramMediaFetchError as exc:
                    urls[index] = self._rehost(paths[index] if index < len(paths) else None, exc.image_url or url)
                    child_ids.append(self._create_carousel_child(urls[index]))
            container_id = self._create_carousel_container(child_ids, caption)
        self._wait_until_ready(container_id)
        return self._publish_container(container_id)

    def _rehost_failed_set(self, urls: list[str], paths: list[Path], failed_url: str | None) -> list[str]:
        if failed_url and failed_url in urls:
            index = urls.index(failed_url)
            path = paths[index] if index < len(paths) else None
            urls[index] = self._rehost(path, failed_url)
            return urls
        refreshed: list[str] = []
        for index, url in enumerate(urls):
            path = paths[index] if index < len(paths) else None
            refreshed.append(self._rehost(path, url))
        return refreshed

    def _rehost(self, path: Path | None, failed_url: str) -> str:
        replace = getattr(self.image_host, "replace_unfetchable", None)
        if callable(replace):
            try:
                return str(replace(failed_url))
            except Exception as exc:
                logger.warning("Could not re-upload %s: %s", failed_url, exc)
        if path is not None:
            return self.image_host.ensure_public_url(str(path))
        raise InstagramMediaFetchError(
            f"Instagram could not fetch {failed_url}",
            image_url=failed_url,
        )

    def _with_cover_collage(self, originals: list[Path], prepared: list[Path]) -> list[Path]:
        """Carousel opens on a collage; the photos follow in the Telegram order."""
        if len(originals) < 2:
            return prepared
        cover = prepared[0].with_name(f"{prepared[0].stem}_cover.jpg")
        try:
            render_feed_collage(originals, cover)
        except Exception as exc:
            logger.warning("Instagram cover collage skipped: %s", exc)
            return prepared
        return [cover, *_keep_product_angles(prepared, MAX_CAROUSEL_ITEMS - 1)]

    def _publish_story_and_highlight(self, post: ComposedPost, prepared: list[Path]) -> None:
        """Story uses the same photos as the post, with a Telegram link sticker and a Highlight."""
        link_url = (
            post.telegram_links[0]
            if (post.telegram_links and len(post.telegram_links) > 0)
            else story_link_url(post.telegram_links, self.caption_footer)
        )

        if self.story_worker and self.repo:
            try:
                external_id = getattr(post, "product_id", "") or ""
                product = self.repo.get_by_external_id(external_id) if external_id else None
                if product:
                    ok, msg, _story_id = self.story_worker.process_story_job(
                        product.id,
                        prepared,
                        price_label=format_story_price(post.price, post.currency),
                        quality_line=quality_line_from_footer(self.caption_footer),
                    )
                    if ok:
                        return
                    logger.warning(
                        "Story with the Telegram link did not finish for %s (%s). "
                        "Publishing a plain story instead.",
                        post.title,
                        msg,
                    )
            except Exception as exc:
                logger.warning("PlaywrightStoryWorker failed: %s, falling back to direct story publish", exc)

        highlight = detect_highlight(post.title, post.text, post.product_url or "")
        link_url = story_link_url(post.telegram_links, self.caption_footer)
        use_real_sticker = self.private_story is not None
        try:
            dest = prepared[0].with_name(f"{prepared[0].stem}_story.jpg")
            collage = render_story_collage(
                prepared,
                dest,
                title=post.title,
                price_label=format_story_price(post.price, post.currency),
                description=product_description(post.text),
                quality_line=quality_line_from_footer(self.caption_footer),
            )
        except Exception as exc:
            logger.error("Instagram story collage failed for %s: %s", post.title, exc)
            return

        if use_real_sticker:
            try:
                story_id = self.private_story.publish_story(  # type: ignore[union-attr]
                    collage,
                    link_url=link_url,
                    link_title=LINK_LABEL,
                    highlight_title=highlight,
                )
                logger.info(
                    "Instagram story %s published for %s with link %s",
                    story_id,
                    post.title,
                    link_url,
                )
                return
            except Exception as exc:
                logger.error(
                    "Story link sticker failed for %s: %s. Publishing a plain story instead.",
                    post.title,
                    exc,
                )

        try:
            story_url = self.image_host.ensure_public_url(str(collage))
            try:
                container_id = self._create_story_container(story_url)
            except InstagramMediaFetchError as exc:
                story_url = self._rehost(collage, exc.image_url or story_url)
                container_id = self._create_story_container(story_url)
            self._wait_until_ready(container_id)
            story_id = self._publish_container(container_id)
            logger.info("Instagram story %s published for %s", story_id, post.title)
            reason = (
                "the Instagram session failed"
                if use_real_sticker
                else "INSTAGRAM_SESSIONID is not set"
            )
            logger.warning(
                "Story %s has no tappable link and was not added to Highlights «%s»: %s.",
                story_id,
                highlight,
                reason,
            )
        except Exception as exc:
            logger.error("Instagram story failed for %s: %s", post.title, exc)

    def _public_image_urls(self, post: ComposedPost) -> list[str]:
        _originals, prepared = self._prepare_local_images(post)
        return [self.image_host.ensure_public_url(str(path)) for path in prepared]

    def _prepare_local_images(self, post: ComposedPost) -> tuple[list[Path], list[Path]]:
        sources = list(post.photo_urls) if post.photo_urls else []
        if not sources and post.photo_url:
            sources = [post.photo_url]

        locals_found: list[Path] = []
        prepare_errors: list[str] = []
        for index, source in enumerate(_keep_product_angles(sources, MAX_CAROUSEL_ITEMS)):
            local = self._resolve_local_image(source, post.title, index)
            if local is None:
                prepare_errors.append(f"unreadable image: {source}")
                continue
            locals_found.append(local)

        originals: list[Path] = []
        prepared: list[Path] = []
        anchor = "bottom" if is_footwear(post.title, post.product_url or "") else "top"
        for local in unique_images(locals_found):
            dest = local.with_name(f"{local.stem}_ig.jpg")
            try:
                prepared.append(prepare_feed_jpeg(local, dest, anchor=anchor))
                originals.append(local)
            except Exception as exc:
                prepare_errors.append(str(exc))
                logger.warning("Skipping Instagram image %s: %s", local, exc)

        if not prepared:
            detail = prepare_errors[0] if prepare_errors else "no image sources"
            raise RuntimeError(f"No usable photos for Instagram post '{post.title}': {detail}")

        if len(prepared) > MAX_CAROUSEL_ITEMS:
            prepared = _keep_product_angles(prepared, MAX_CAROUSEL_ITEMS)
            originals = originals[:MAX_CAROUSEL_ITEMS]
        return originals, prepared

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

    def _create_story_container(self, image_url: str) -> str:
        return self._create_container({
            "image_url": image_url,
            "media_type": "STORIES",
        })

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
        self._wait_until_ready(container_id, image_url=image_url)
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

    def _wait_until_ready(self, container_id: str, image_url: str | None = None) -> None:
        deadline = time.monotonic() + CONTAINER_READY_TIMEOUT_SECONDS
        status = "IN_PROGRESS"
        detail = ""
        while time.monotonic() <= deadline:
            status, detail = self._container_status(container_id)
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                message = f"Instagram container {container_id} is {status}: {detail or 'no detail'}"
                fetch_failed, found_url = _media_fetch_failure(
                    {"message": detail, "error_user_msg": detail}
                )
                if image_url or fetch_failed:
                    raise InstagramMediaFetchError(message, image_url=found_url or image_url)
                raise RuntimeError(message)
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
            user_message = str(error.get("error_user_msg") or "").strip()
            detail = f"Instagram API error {code}: {message}"
            if user_message and user_message not in str(message):
                detail = f"{detail}. {user_message}"
            fetch_failed, image_url = _media_fetch_failure(error)
            if fetch_failed:
                raise InstagramMediaFetchError(detail, image_url=image_url)
            raise httpx.HTTPStatusError(
                detail,
                request=resp.request,
                response=resp,
            )
        return data


def _media_fetch_failure(error: dict) -> tuple[bool, str | None]:
    """Error 9004 means Meta could not download image_url. The URI is in the user message."""
    code = _as_int(error.get("code"))
    subcode = _as_int(error.get("error_subcode"))
    message = str(error.get("message") or "")
    user = str(error.get("error_user_msg") or "")
    combined = f"{message} {user}".lower()
    fetch_failed = (
        code == 9004
        or subcode == 2207052
        or "could not be fetched" in combined
        or "media download" in combined
    )
    if not fetch_failed:
        return False, None
    match = _FETCH_URL.search(user) or _FETCH_URL.search(message)
    image_url = match.group(0).rstrip(".,)") if match else None
    return True, image_url


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _keep_product_angles(photos: list[_T], limit: int) -> list[_T]:
    """Trim model shots, never the closing front, back and close-up."""
    if len(photos) <= limit:
        return photos
    return photos[: limit - PRODUCT_ANGLE_SLIDES] + photos[-PRODUCT_ANGLE_SLIDES:]


_CAROUSEL_DROPPED_LINE = re.compile(
    r"обращаться|отзыв|в наличии|telegram|t\.me/",
    re.IGNORECASE,
)


def footer_for_carousel(footer: str | None) -> str:
    """Drop contact handles, reviews, stock account and the Telegram link from a carousel caption.

    Phone and the quality line stay. Stories still read the full footer for the link sticker.
    """
    if not footer:
        return ""
    kept = [line for line in footer.splitlines() if not _CAROUSEL_DROPPED_LINE.search(line)]
    text = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_instagram_caption(post: ComposedPost, footer: str | None = None) -> str:
    """Format a boutique caption that matches the Telegram card, within Instagram limits."""
    price_val = int(whole_price(post.price))
    price_display = f"{price_val}$" if post.currency == "USD" else f"{price_val} {post.currency}"
    header = f"{post.title.strip()}-{price_display}"
    description = _extract_description(post)
    contact = (footer if footer is not None else DEFAULT_INSTAGRAM_CAPTION_FOOTER).strip()

    caption = _assemble_caption(header, description, contact)
    if len(caption) <= MAX_INSTAGRAM_CAPTION_LEN:
        return caption

    overflow = len(caption) - (MAX_INSTAGRAM_CAPTION_LEN - 3)
    trimmed = description[: max(0, len(description) - overflow)].rstrip()
    if trimmed:
        trimmed += "..."
    return _assemble_caption(header, trimmed, contact)[:MAX_INSTAGRAM_CAPTION_LEN]


def _assemble_caption(header: str, description: str, contact: str) -> str:
    first_line = description.split("\n")[0].strip() if description else ""
    has_header_already = (
        description.lower().startswith(header.lower())
        or description.lower().startswith(header.split("-")[0].lower() + "-")
        or ("-" in first_line and any(cur in first_line for cur in ("$", "€", "сум", "USD", "EUR")))
    )
    if description and has_header_already:
        body = description
    else:
        body = f"{header}\n{description}" if description else header
    chunks = [body]
    if contact:
        chunks.append(contact)
    return "\n\n".join(chunks)


def _extract_description(post: ComposedPost) -> str:
    chunks = [part.strip() for part in post.text.split("\n\n") if part.strip()]
    body: list[str] = []
    for chunk in chunks:
        if chunk.startswith("✨") or chunk.startswith("🏷") or chunk.startswith("🔗"):
            continue
        body.append(chunk)
    return "\n\n".join(body).strip()

