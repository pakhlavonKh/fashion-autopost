"""Publish a story with a link sticker and file it into an Instagram Highlight.

The Graph API can publish a plain story, but it cannot attach a tappable link
or edit Highlights. Both steps go through the Instagram app API, signed in with
the instagram.com sessionid cookie sent as the app's bearer token. The web API
cannot do either: it drops link stickers and redirects Highlight calls.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote

logger = logging.getLogger(__name__)

MAX_HIGHLIGHT_TITLE = 16
HIGHLIGHT_COVER_CROP = [0.0, 0.21830457, 1.0, 0.78094524]
SESSION_REJECTED = (
    "Instagram rejected INSTAGRAM_SESSIONID. Open instagram.com while logged in, "
    "copy the full sessionid cookie again, put it in .env, and restart the program."
)


class InstagramSessionExpired(RuntimeError):
    """The browser session cookie is missing, expired, or was rejected."""


class InstagramHighlightClient:
    """Uploads a linked story and files it into Highlights with the app API."""

    def __init__(self, session_id: str, user_id: str = "", client: Any | None = None) -> None:
        self.session_id = unquote(session_id.strip())
        self.user_id = str(user_id).strip() or self.session_id.split(":")[0]
        self._client = client

    def publish_linked_story(
        self,
        image: Path,
        *,
        link_url: str,
        link_title: str,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> str:
        """Upload the collage with a tappable link over the painted pill. Returns the story pk."""
        from instagrapi.types import StorySticker

        client = self._session()
        try:
            self._call(
                client.private_request,
                "media/validate_reel_url/",
                {"url": link_url, "_uid": self.user_id, "_uuid": client.uuid},
            )
        except InstagramSessionExpired:
            raise
        except Exception as exc:
            logger.warning("Instagram did not preview the story link, continuing: %s", exc)

        upload_id, image_width, image_height = self._upload(client, Path(image))
        sticker = StorySticker(
            id="link_sticker_default",
            type="story_link",
            x=x,
            y=y,
            z=0,
            width=width,
            height=height,
            rotation=0.0,
            extra={
                "link_type": "web",
                "url": link_url,
                "link_title": link_title,
                "tap_state_str_id": "link_sticker_default",
            },
        )
        last_error: Exception | None = None
        for attempt in range(3):
            # Instagram needs a moment to register the upload before it can be configured.
            time.sleep(3)
            try:
                result = self._call(
                    client.photo_configure_to_story,
                    upload_id,
                    image_width,
                    image_height,
                    "",
                    stickers=[sticker],
                )
            except InstagramSessionExpired:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("Story configure attempt %s failed: %s", attempt + 1, exc)
                continue
            media = result.get("media") if isinstance(result, dict) else None
            story_pk = str((media or {}).get("pk") or "").split("_")[0]
            if not story_pk:
                last_error = RuntimeError(f"Instagram did not return the story id: {str(result)[:200]}")
                continue
            if not (media or {}).get("story_link_stickers"):
                logger.warning("Story %s was published, but Instagram did not echo the link sticker", story_pk)
            return story_pk
        assert last_error is not None
        raise last_error

    def add_story(self, title: str, media_pk: str) -> str:
        """Add the story to the Highlight named `title`, creating it when missing."""
        client = self._session()
        highlight_title = _clip_title(title)
        media_id = self._media_id(media_pk)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                existing = self._find(client, highlight_title)
                if existing:
                    self._edit(client, existing, media_id)
                    return existing
                return self._create(client, highlight_title, media_id)
            except InstagramSessionExpired:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("Highlight «%s» attempt %s failed: %s", highlight_title, attempt + 1, exc)
                if attempt < 2:
                    time.sleep(2)
        assert last_error is not None
        raise last_error

    def _find(self, client: Any, title: str) -> str | None:
        data = self._call(client.private_request, f"highlights/{self.user_id}/highlights_tray/")
        wanted = _normalize_title(title)
        for reel in data.get("tray") or []:
            if not isinstance(reel, dict):
                continue
            if _normalize_title(str(reel.get("title") or "")) != wanted:
                continue
            pk = str(reel.get("id") or reel.get("pk") or "").split(":")[-1]
            if pk:
                return pk
        return None

    def _create(self, client: Any, title: str, media_id: str) -> str:
        from instagrapi import config

        cover = {"media_id": media_id, "crop_rect": json.dumps(HIGHLIGHT_COVER_CROP)}
        data = self._call(
            client.private_request,
            "highlights/create_reel/",
            {
                "supported_capabilities_new": json.dumps(config.SUPPORTED_CAPABILITIES),
                "source": "self_profile",
                "creation_id": str(int(time.time())),
                "_uid": self.user_id,
                "_uuid": client.uuid,
                "cover": json.dumps(cover),
                "title": title,
                "media_ids": json.dumps([media_id]),
            },
        )
        reel = data.get("reel") if isinstance(data.get("reel"), dict) else {}
        pk = str(reel.get("id") or reel.get("pk") or "").split(":")[-1]
        if not pk:
            raise RuntimeError(f"Instagram did not return a highlight id for «{title}»")
        return pk

    def _edit(self, client: Any, highlight_pk: str, media_id: str) -> None:
        from instagrapi import config

        self._call(
            client.private_request,
            f"highlights/highlight:{highlight_pk}/edit_reel/",
            {
                "supported_capabilities_new": json.dumps(config.SUPPORTED_CAPABILITIES),
                "source": "self_profile",
                "_uid": self.user_id,
                "_uuid": client.uuid,
                "added_media_ids": json.dumps([media_id]),
                "removed_media_ids": "[]",
            },
        )

    def _media_id(self, media_pk: str) -> str:
        media_pk = str(media_pk).strip()
        if "_" in media_pk:
            return media_pk
        return f"{media_pk}_{self.user_id}"

    def _session(self) -> Any:
        if self._client is not None:
            return self._client
        from instagrapi import Client

        client = Client()
        client.delay_range = [0, 0]
        self._call(client.login_by_sessionid, self.session_id)
        self._client = client
        return client

    def _upload(self, client: Any, image: Path) -> tuple[str, int, int]:
        """Send the JPEG bytes as they are. instagrapi's uploader re-saves at quality 75."""
        from PIL import Image
        from instagrapi import config

        payload = image.read_bytes()
        with Image.open(image) as opened:
            image_width, image_height = opened.size
        upload_id = str(int(time.time() * 1000))
        name = f"{upload_id}_0_{random.randint(1000000000, 9999999999)}"
        params = {
            "retry_context": '{"num_step_auto_retry":0,"num_reupload":0,"num_step_manual_retry":0}',
            "media_type": "1",
            "xsharing_user_ids": "[]",
            "upload_id": upload_id,
            "image_compression": json.dumps({"lib_name": "moz", "lib_version": "3.1.m", "quality": "95"}),
        }
        headers = client.private_headers({
            "Accept-Encoding": "gzip",
            "X-Instagram-Rupload-Params": json.dumps(params),
            "X_FB_PHOTO_WATERFALL_ID": str(uuid.uuid4()),
            "X-Entity-Type": "image/jpeg",
            "Offset": "0",
            "X-Entity-Name": name,
            "X-Entity-Length": str(len(payload)),
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
        })
        response = client.private.post(
            f"https://{config.API_DOMAIN}/rupload_igphoto/{name}",
            data=payload,
            headers=headers,
        )
        if response.status_code in (401, 403) or "login_required" in response.text:
            raise InstagramSessionExpired(SESSION_REJECTED)
        if response.status_code != 200:
            raise RuntimeError(f"Instagram story upload failed: HTTP {response.status_code} {response.text[:200]}")
        return upload_id, image_width, image_height

    def _call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        from instagrapi.exceptions import ChallengeRequired, LoginRequired

        try:
            return method(*args, **kwargs)
        except (LoginRequired, ChallengeRequired) as exc:
            raise InstagramSessionExpired(SESSION_REJECTED) from exc


def _clip_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if len(cleaned) <= MAX_HIGHLIGHT_TITLE:
        return cleaned
    return cleaned[:MAX_HIGHLIGHT_TITLE].rstrip()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).casefold()
