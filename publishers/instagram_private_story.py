"""Publish a story with a real link sticker and file it into Highlights.

The Graph API can post a plain story image, but it cannot attach a tappable
link or create a Highlight. Both of those use the account's Instagram app
sign-in: the sticker sits over the painted «посмотреть подробнее фото» pill
and opens the product's post in the Telegram channel. If the Highlight circle
does not exist yet, it is created and the story is added to it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from publishers.instagram_highlights import InstagramHighlightClient
from publishers.instagram_story import LINK_LABEL, link_sticker_area

logger = logging.getLogger(__name__)


class InstagramPrivateStory:
    """Uploads the collage as a story and archives it under a category Highlight."""

    def __init__(
        self,
        session_id: str = "",
        client: Any | None = None,
        *,
        login: str = "",
        password: str = "",
    ) -> None:
        self.session_id = (session_id or "").strip()
        self.login = login
        self.password = password
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
        x, y, width, height = link_sticker_area()
        story_pk = client.publish_linked_story(
            Path(image),
            link_url=link_url,
            link_title=link_title,
            x=x,
            y=y,
            width=width,
            height=height,
        )
        # The story is live from here on. A Highlight failure must not trigger a second story.
        try:
            highlight_id = client.add_story(highlight_title, story_pk)
            logger.info(
                "Story %s added to highlight «%s» (%s)",
                story_pk,
                highlight_title,
                highlight_id,
            )
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
        self._client = InstagramHighlightClient(
            self.session_id,
            login=self.login,
            password=self.password,
        )
        return self._client
