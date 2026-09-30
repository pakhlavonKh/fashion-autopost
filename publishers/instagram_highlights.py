"""Publish a story with a link sticker and file it into an Instagram Highlight.

The Graph API can publish a plain story, but it cannot attach a tappable link
or edit Highlights. Both steps use the instagram.com session cookie through the
web API, the same one the browser sends. A mobile private-API client rejects
that cookie, so this client speaks the web API instead.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

IG_APP_ID = "936619743392459"
WEB_ROOT = "https://www.instagram.com"
UPLOAD_ROOT = "https://i.instagram.com"
MAX_HIGHLIGHT_TITLE = 16
SESSION_REJECTED = (
    "Instagram rejected INSTAGRAM_SESSIONID. Open instagram.com while logged in, "
    "copy the full sessionid cookie again, put it in .env, and restart the program."
)


class InstagramSessionExpired(RuntimeError):
    """The browser session cookie is missing, expired, or was rejected."""


class InstagramHighlightClient:
    """Uploads a linked story and files it into Highlights with a web session."""

    def __init__(self, session_id: str, user_id: str, timeout_seconds: float = 30.0) -> None:
        self.session_id = unquote(session_id.strip())
        self.user_id = str(user_id).strip() or self.session_id.split(":")[0]
        self.timeout_seconds = timeout_seconds
        self._csrf: str | None = None

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
        """Upload the collage and attach a tappable link sticker. Returns the story pk."""
        self._ensure_session()
        self._request(
            "POST",
            f"{WEB_ROOT}/api/v1/media/validate_reel_url/",
            {"url": link_url, "_uid": self.user_id, "_uuid": self.user_id},
        )
        upload_id, image_width, image_height = self._rupload(image)
        sticker = {
            "x": x,
            "y": y,
            "z": 0,
            "width": width,
            "height": height,
            "rotation": 0.0,
            "type": "story_link",
            "link_type": "web",
            "url": link_url,
            "link_title": link_title,
            "custom_cta": link_title,
            "tap_state": 0,
            "tap_state_str_id": "link_sticker_default",
            "is_sticker": True,
            "selected_index": 0,
        }
        now = int(time.time())
        data = self._request(
            "POST",
            f"{WEB_ROOT}/api/v1/media/configure_to_story/",
            {
                "upload_id": upload_id,
                "source_type": "4",
                "configure_mode": "1",
                "client_shared_at": str(now - 5),
                "client_timestamp": str(now),
                "story_sticker_ids": "link_sticker_default",
                "tap_models": json.dumps([sticker], separators=(",", ":")),
                "original_media_type": "photo",
                "media_transformation_info": json.dumps(
                    {
                        "width": str(image_width),
                        "height": str(image_height),
                        "x_transform": "0",
                        "y_transform": "0",
                        "zoom": "1.0",
                        "rotation": "0.0",
                        "background_coverage": "0.0",
                    },
                    separators=(",", ":"),
                ),
            },
        )
        media = data.get("media") if isinstance(data.get("media"), dict) else {}
        raw_id = str(media.get("pk") or media.get("id") or "")
        story_pk = raw_id.split("_")[0]
        if not story_pk:
            raise RuntimeError("Instagram published the story without a media id")
        if not _response_has_link(media, link_url):
            logger.warning(
                "Story %s was published, but the configure response did not echo the link sticker",
                story_pk,
            )
        return story_pk

    def add_story(self, title: str, media_pk: str) -> str:
        """Add the story to the Highlight named `title`, creating it when missing."""
        highlight_title = _clip_title(title)
        media_id = self._media_id(media_pk)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self._add_once(highlight_title, media_id)
            except InstagramSessionExpired:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Highlight «%s» attempt %s failed: %s",
                    highlight_title,
                    attempt + 1,
                    exc,
                )
                if attempt < 2:
                    time.sleep(2)
        assert last_error is not None
        raise last_error

    def _add_once(self, title: str, media_id: str) -> str:
        existing = self._find(title)
        if existing:
            self._edit(existing, media_id, title)
            return existing
        return self._create(title, media_id)

    def _find(self, title: str) -> str | None:
        url = f"{WEB_ROOT}/api/v1/highlights/{self.user_id}/highlights_tray/"
        data = self._request("GET", url)
        wanted = _normalize_title(title)
        for reel in data.get("tray") or []:
            if not isinstance(reel, dict):
                continue
            reel_title = str(reel.get("title") or "")
            if _normalize_title(reel_title) != wanted:
                continue
            raw_id = str(reel.get("id") or reel.get("pk") or "")
            pk = raw_id.split(":")[-1]
            if pk:
                return pk
        return None

    def _create(self, title: str, media_id: str) -> str:
        cover = {
            "media_id": media_id,
            "crop_rect": "[0.0,0.21830457,1.0,0.78094524]",
        }
        data = self._request(
            "POST",
            f"{WEB_ROOT}/api/v1/highlights/create_reel/",
            {
                "media_ids": json.dumps([media_id], separators=(",", ":")),
                "cover": json.dumps(cover, separators=(",", ":")),
                "source": "self_profile",
                "title": title,
                "creation_id": str(int(time.time())),
            },
        )
        reel = data.get("reel") if isinstance(data.get("reel"), dict) else data
        raw_id = ""
        if isinstance(reel, dict):
            raw_id = str(reel.get("id") or reel.get("pk") or "")
        pk = raw_id.split(":")[-1]
        if not pk:
            raise RuntimeError(f"Instagram did not return a highlight id for «{title}»")
        return pk

    def _edit(self, highlight_pk: str, media_id: str, title: str) -> None:
        self._request(
            "POST",
            f"{WEB_ROOT}/api/v1/highlights/highlight:{highlight_pk}/edit_reel/",
            {
                "added_media_ids": json.dumps([media_id], separators=(",", ":")),
                "removed_media_ids": "[]",
                "source": "story_viewer",
                "title": title,
            },
        )

    def _media_id(self, media_pk: str) -> str:
        media_pk = str(media_pk).strip()
        if "_" in media_pk:
            return media_pk
        return f"{media_pk}_{self.user_id}"

    def _ensure_session(self) -> None:
        """Fail before upload when the cookie is the logged-out redirect."""
        self._request("GET", f"{WEB_ROOT}/api/v1/accounts/edit/web_form_data/")

    def _rupload(self, image: Path) -> tuple[str, int, int]:
        payload = Path(image).read_bytes()
        from PIL import Image

        with Image.open(Path(image)) as opened:
            image_width, image_height = opened.size
        upload_id = str(int(time.time() * 1000))
        name = f"{upload_id}_0_{random.randint(1000000000, 9999999999)}"
        params = {
            "media_type": "1",
            "upload_id": upload_id,
            "upload_media_height": str(image_height),
            "upload_media_width": str(image_width),
            "retry_context": '{"num_step_auto_retry":0,"num_reupload":0,"num_step_manual_retry":0}',
            "xsharing_user_ids": "[]",
            "image_compression": '{"lib_name":"moz","lib_version":"3.1.m","quality":"80"}',
        }
        self._request(
            "POST",
            f"{UPLOAD_ROOT}/rupload_igphoto/{name}",
            body=payload,
            extra_headers={
                "X-Instagram-Rupload-Params": json.dumps(params, separators=(",", ":")),
                "X-Entity-Type": "image/jpeg",
                "X-Entity-Name": name,
                "X-Entity-Length": str(len(payload)),
                "Offset": "0",
                "Content-Type": "application/octet-stream",
            },
        )
        return upload_id, image_width, image_height

    def _request(
        self,
        method: str,
        url: str,
        fields: dict[str, Any] | None = None,
        *,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            self._apply_cookies(client)
            self._ensure_csrf(client)
            headers = self._headers()
            if extra_headers:
                headers.update(extra_headers)
            if method == "GET":
                response = client.get(url, headers=headers)
            elif body is not None:
                response = client.post(url, headers=headers, content=body)
            else:
                response = client.post(url, headers=headers, data=fields or {})
        return self._parse(response)

    def _apply_cookies(self, client: httpx.Client) -> None:
        client.cookies.set("sessionid", self.session_id, domain=".instagram.com")
        client.cookies.set("ds_user_id", self.user_id, domain=".instagram.com")
        if self._csrf:
            client.cookies.set("csrftoken", self._csrf, domain=".instagram.com")

    def _ensure_csrf(self, client: httpx.Client) -> None:
        if self._csrf:
            client.cookies.set("csrftoken", self._csrf, domain=".instagram.com")
            return
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as home:
            self._apply_cookies(home)
            response = home.get(f"{WEB_ROOT}/", headers=self._headers())
            csrf = home.cookies.get("csrftoken")
        if not csrf:
            match = re.search(r"csrftoken=([^;]+)", response.headers.get("set-cookie", ""))
            csrf = match.group(1) if match else None
        if not csrf:
            raise InstagramSessionExpired(SESSION_REJECTED)
        self._csrf = csrf
        client.cookies.set("csrftoken", csrf, domain=".instagram.com")

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "X-IG-App-ID": IG_APP_ID,
            "X-ASBD-ID": "129477",
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{WEB_ROOT}/",
            "Origin": WEB_ROOT,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if self._csrf:
            headers["X-CSRFToken"] = self._csrf
        return headers

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        location = response.headers.get("location") or ""
        status_code = response.status_code if isinstance(response.status_code, int) else 0
        if status_code in {301, 302, 303, 401, 403} or "accounts/login" in location:
            raise InstagramSessionExpired(SESSION_REJECTED)
        try:
            data = response.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        message = str(data.get("message") or data.get("status") or "")
        if data.get("require_login") or "login_required" in message.casefold():
            raise InstagramSessionExpired(SESSION_REJECTED)
        if not response.is_success or data.get("status") not in (None, "ok"):
            detail = message or response.text[:300]
            raise RuntimeError(f"Instagram highlights error: {detail}")
        if data.get("status") == "fail":
            raise RuntimeError(f"Instagram highlights error: {data.get('message') or 'fail'}")
        return data


def _clip_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if len(cleaned) <= MAX_HIGHLIGHT_TITLE:
        return cleaned
    return cleaned[:MAX_HIGHLIGHT_TITLE].rstrip()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).casefold()


def _response_has_link(media: dict[str, Any], link_url: str) -> bool:
    """True when the configure payload echoes the sticker, or omits sticker fields entirely."""
    blobs = [
        media.get("story_link_stickers"),
        media.get("story_cta"),
        media.get("links"),
    ]
    present = [item for item in blobs if item]
    if not present:
        return True
    return link_url in json.dumps(present, ensure_ascii=False)
