"""Publish a story with a real link sticker and file it into Highlights.

The Graph API can post a plain story image, but it cannot attach a tappable
link or create a Highlight. Both of those use the account's instagram.com
session: the sticker opens the Telegram channel, and its title is
«посмотреть подробнее фото». If that Highlight circle does not exist yet, it
is created and the story is added to it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from publishers.instagram_highlights import InstagramHighlightClient, InstagramSessionExpired
from publishers.instagram_story import LINK_LABEL

logger = logging.getLogger(__name__)

# Center of the sticker, as a fraction of the 9:16 frame. Sits above the reply bar.
LINK_STICKER_X = 0.50
LINK_STICKER_Y = 0.84
LINK_STICKER_WIDTH = 0.68
LINK_STICKER_HEIGHT = 0.055


class InstagramPrivateStory:
    """Uploads the collage as a story and archives it under a category Highlight."""

    def __init__(self, session_id: str, client: Any | None = None) -> None:
        self.session_id = session_id.strip()
        self._client = client

    def publish_story(
        self,
        image: Path,
        *,
        link_url: str,
        link_title: str = LINK_LABEL,
        highlight_title: str,
    ) -> str:
        client = self._ensure_client()
        story_pk = client.publish_linked_story(
            Path(image),
            link_url=link_url,
            link_title=link_title,
            x=LINK_STICKER_X,
            y=LINK_STICKER_Y,
            width=LINK_STICKER_WIDTH,
            height=LINK_STICKER_HEIGHT,
        )
        try:
            highlight_id = client.add_story(highlight_title, story_pk)
            logger.info(
                "Story %s added to highlight «%s» (%s)",
                story_pk,
                highlight_title,
                highlight_id,
            )
        except InstagramSessionExpired:
            raise
        except Exception as exc:
            logger.error(
                "Story %s is live with link %s, but it was not added to Highlights «%s»: %s",
                story_pk,
                link_url,
                highlight_title,
                exc,
            )
        return story_pk

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        user_id = self.session_id.split("%3A")[0].split(":")[0]
        self._client = InstagramHighlightClient(self.session_id, user_id)
        return self._client
