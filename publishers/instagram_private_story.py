"""Publish a story with a real link sticker and file it into Highlights.

The Graph API can post a plain story image, but it cannot attach a tappable
link or create a Highlight. Both of those use the account session: the sticker
opens the Telegram channel, and its title is «посмотреть подробнее фото».
If that Highlight circle does not exist yet, it is created and the story is
added to it.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from publishers.instagram_highlights import _normalize_title
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
        self._validate_link(client, link_url)
        story = client.photo_upload_to_story(
            Path(image),
            stickers=[self._link_sticker(link_url, link_title)],
            resize_mode="fill",
        )
        story_pk = str(story.pk)
        self._archive(client, highlight_title, story_pk)
        return story_pk

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from instagrapi import Client

        client = Client()
        client.login_by_sessionid(self.session_id)
        self._client = client
        return client

    def _validate_link(self, client: Any, link_url: str) -> None:
        client.private_request(
            "media/validate_reel_url/",
            {
                "url": link_url,
                "_uid": str(client.user_id),
                "_uuid": client.uuid,
            },
        )

    def _link_sticker(self, link_url: str, link_title: str) -> Any:
        from instagrapi.types import StorySticker

        return StorySticker(
            id="link_sticker_default",
            type="story_link",
            x=LINK_STICKER_X,
            y=LINK_STICKER_Y,
            z=0,
            width=LINK_STICKER_WIDTH,
            height=LINK_STICKER_HEIGHT,
            rotation=0.0,
            extra={
                "link_type": "web",
                "url": link_url,
                "link_title": link_title,
                "tap_state_str_id": "link_sticker_default",
            },
        )

    def _archive(self, client: Any, title: str, story_pk: str) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self._archive_once(client, title, story_pk)
            except Exception as exc:
                last_error = exc
                logger.warning("Highlight «%s» attempt %s failed: %s", title, attempt + 1, exc)
                if attempt < 2:
                    time.sleep(2)
        assert last_error is not None
        raise last_error

    def _archive_once(self, client: Any, title: str, story_pk: str) -> str:
        existing = self._find_highlight(client, title)
        if existing:
            client.highlight_add_stories(existing, [client.media_id(story_pk)])
            logger.info("Story %s added to existing highlight «%s» (%s)", story_pk, title, existing)
            return existing
        created = client.highlight_create(title, [story_pk])
        highlight_pk = str(created.pk)
        logger.info("Created highlight «%s» (%s) with story %s", title, highlight_pk, story_pk)
        return highlight_pk

    def _find_highlight(self, client: Any, title: str) -> str | None:
        wanted = _normalize_title(title)
        for reel in client.user_highlights(str(client.user_id)):
            if _normalize_title(str(getattr(reel, "title", ""))) != wanted:
                continue
            pk = str(getattr(reel, "pk", "") or "")
            if pk:
                return pk.split(":")[-1]
        return None
