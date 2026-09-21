"""Instagram Graph API publisher implementation.

Per SDD §3.6 and SRS FR-5.2, FR-5.3.
Executes Meta Graph API's 2-step media container publishing flow and enforces HTTPS image URLs.
"""

import logging
from typing import Any
import httpx

from core.composer import ComposedPost
from core.resilience import retry_with_backoff
from publishers.base import PublishResult
from publishers.image_hosting import ImageHostingService, PassthroughImageHost

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
MAX_INSTAGRAM_CAPTION_LEN = 2200


class InstagramPublisher:
    """Publishes posts to an Instagram Business/Creator account via Graph API."""

    def __init__(
        self,
        access_token: str,
        account_id: str,
        image_host: ImageHostingService | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.access_token = access_token
        self.account_id = account_id
        self.image_host = image_host or PassthroughImageHost()
        self.timeout_seconds = timeout_seconds

    @property
    def platform_name(self) -> str:
        return "instagram"

    def publish(self, post: ComposedPost) -> PublishResult:
        """Execute the two-step Instagram container creation and publish flow."""
        try:
            # 1. Ensure public HTTPS image URL (SRS FR-5.3)
            public_image_url = self.image_host.ensure_public_url(post.photo_url)
            caption = self._format_caption(post)

            # 2. Step 1: Create media container
            container_id = self._create_media_container_with_retry(public_image_url, caption)

            # 3. Step 2: Publish media container
            post_id = self._publish_container_with_retry(container_id)

            return PublishResult(success=True, platform_post_id=str(post_id))
        except Exception as exc:
            logger.error("Instagram publish failed for %s: %s", post.title, exc)
            return PublishResult(success=False, error=str(exc))

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=15.0, exceptions=(httpx.HTTPError,))
    def _create_media_container_with_retry(self, image_url: str, caption: str) -> str:
        """POST /{ig-user-id}/media to create an item container."""
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.account_id}/media"
        params: dict[str, Any] = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(url, params=params)
            data = resp.json()

            if not resp.is_success or "id" not in data:
                error_msg = data.get("error", {}).get("message") or resp.text
                raise httpx.HTTPStatusError(
                    f"Instagram container creation error: {error_msg}",
                    request=resp.request,
                    response=resp,
                )

            return str(data["id"])

    @retry_with_backoff(max_attempts=3, base_delay=2.0, max_delay=15.0, exceptions=(httpx.HTTPError,))
    def _publish_container_with_retry(self, container_id: str) -> str:
        """POST /{ig-user-id}/media_publish to publish the container."""
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.account_id}/media_publish"
        params: dict[str, Any] = {
            "creation_id": container_id,
            "access_token": self.access_token,
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(url, params=params)
            data = resp.json()

            if not resp.is_success or "id" not in data:
                error_msg = data.get("error", {}).get("message") or resp.text
                raise httpx.HTTPStatusError(
                    f"Instagram container publish error: {error_msg}",
                    request=resp.request,
                    response=resp,
                )

            return str(data["id"])

    def _format_caption(self, post: ComposedPost) -> str:
        """Format clean Instagram caption with styling and hashtags."""
        brand_tag = f"#{post.source.lower()}fashion"
        tags = f"{brand_tag} #fashion #outfitoftheday #styleinspo #fashionboutique"

        caption = f"{post.text}\n\n{tags}"
        if len(caption) > MAX_INSTAGRAM_CAPTION_LEN:
            caption = caption[: MAX_INSTAGRAM_CAPTION_LEN - 3] + "..."

        return caption
