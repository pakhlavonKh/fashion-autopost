"""Publisher protocol and result DTOs.

Per SDD §3.6 and SRS FR-5.
Decouples core business logic from specific social media network APIs.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.composer import ComposedPost


@dataclass(frozen=True)
class PublishResult:
    """Outcome of attempting to publish a post to a social network."""
    success: bool
    platform_post_id: str | None = None
    error: str | None = None


@runtime_checkable
class Publisher(Protocol):
    """Abstract interface for publishing a composed post to a target channel."""

    @property
    def platform_name(self) -> str:
        """Name of the platform (e.g., 'telegram', 'instagram')."""
        ...

    def publish(self, post: ComposedPost) -> PublishResult:
        """Publish the post and return success status or error."""
        ...
