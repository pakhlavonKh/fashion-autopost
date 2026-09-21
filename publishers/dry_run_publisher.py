"""DryRunPublisher wrapper implementing the Publisher interface.

Per SDD §3.6 and SRS FR-9.
Wraps any real publisher, intercepts publish calls, logs formatted payload,
and returns synthetic success without performing network calls.
"""

import logging
import uuid
from datetime import datetime, timezone

from core.composer import ComposedPost
from publishers.base import Publisher, PublishResult

logger = logging.getLogger(__name__)


class DryRunPublisher:
    """Wrapper that intercepts publish() calls to simulate output without network traffic."""

    def __init__(self, inner_publisher: Publisher) -> None:
        self.inner_publisher = inner_publisher

    @property
    def platform_name(self) -> str:
        return self.inner_publisher.platform_name

    def publish(self, post: ComposedPost) -> PublishResult:
        """Log the simulated post details and return a mock success result."""
        fake_id = f"DRYRUN_{self.platform_name.upper()}_{uuid.uuid4().hex[:8]}"
        timestamp = datetime.now(timezone.utc).isoformat()

        logger.info(
            "\n"
            "===================== [DRY-RUN SIMULATION: %s] =====================\n"
            "Time:       %s\n"
            "Platform:   %s\n"
            "Post ID:    %s\n"
            "Photo URL:  %s\n"
            "Price:      %s %.2f\n"
            "Link:       %s\n"
            "--- Content ---\n"
            "%s\n"
            "=======================================================================",
            self.platform_name.upper(),
            timestamp,
            self.platform_name,
            fake_id,
            post.photo_url,
            post.currency,
            post.price,
            post.product_url or "None",
            post.text,
        )

        return PublishResult(success=True, platform_post_id=fake_id)
