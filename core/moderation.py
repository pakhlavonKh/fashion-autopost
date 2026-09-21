"""Moderation gate for pre-publication review.

Per SDD §7 and SRS §10.2 (Open Question 2: Moderation requirement).
Provides an optional pipeline stage to hold posts in 'pending_review' status
if manual moderation is enabled by configuration.
"""

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ModerationGate(Protocol):
    """Interface controlling whether a selected product can proceed to immediate publishing."""

    def should_publish(self, external_id: str) -> bool:
        """Return True if item is approved for immediate publishing; False to hold in review."""
        ...


class ConfigurableModerationGate:
    """Config-driven moderation gate.
    
    If moderation is disabled, or auto_approve is True, items publish immediately.
    If moderation is enabled and auto_approve is False, items are queued for human review.
    """

    def __init__(self, enabled: bool = False, auto_approve: bool = True) -> None:
        self.enabled = enabled
        self.auto_approve = auto_approve

    def should_publish(self, external_id: str) -> bool:
        if not self.enabled:
            return True

        if self.auto_approve:
            logger.info("ModerationGate: auto-approving %s (auto_approve=True)", external_id)
            return True

        logger.info(
            "ModerationGate: manual review required for %s, holding in pending_review",
            external_id,
        )
        return False
