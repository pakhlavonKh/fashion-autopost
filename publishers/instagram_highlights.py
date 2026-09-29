"""File a published story into an Instagram Highlight (актуальное).

The official Graph API can publish a story, but it cannot create or edit
Highlights. That step uses the account's own web session: the same cookie the
browser sends to instagram.com. The story is added to the circle whose title
matches the garment category, or a new circle is created with that title.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

IG_APP_ID = "936619743392459"
WEB_ROOT = "https://www.instagram.com"
MAX_HIGHLIGHT_TITLE = 16


class InstagramHighlightClient:
    """Adds a story to a named Highlight using an instagram.com session cookie."""

    def __init__(self, session_id: str, user_id: str, timeout_seconds: float = 30.0) -> None:
        self.session_id = session_id.strip()
        self.user_id = str(user_id).strip()
        self.timeout_seconds = timeout_seconds
        self._csrf: str | None = None

    def add_story(self, title: str, media_pk: str) -> str:
        """Add the story to the Highlight named `title`, creating it when missing."""
        highlight_title = _clip_title(title)
        media_id = self._media_id(media_pk)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self._add_once(highlight_title, media_id)
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
            f"{WEB_ROOT}/api/v1/highlights/{highlight_pk}/edit_reel/",
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

    def _request(self, method: str, url: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            client.cookies.set("sessionid", self.session_id, domain=".instagram.com")
            client.cookies.set("ds_user_id", self.user_id, domain=".instagram.com")
            self._ensure_csrf(client)
            headers = self._headers()
            if method == "GET":
                response = client.get(url, headers=headers)
            else:
                response = client.post(url, headers=headers, data=fields or {})
        return self._parse(response)

    def _ensure_csrf(self, client: httpx.Client) -> None:
        if self._csrf:
            client.cookies.set("csrftoken", self._csrf, domain=".instagram.com")
            return
        response = client.get(f"{WEB_ROOT}/", headers=self._headers())
        csrf = client.cookies.get("csrftoken")
        if not csrf:
            match = re.search(r"csrftoken=([^;]+)", response.headers.get("set-cookie", ""))
            csrf = match.group(1) if match else None
        if not csrf:
            raise RuntimeError("Instagram session did not return a csrf token")
        self._csrf = csrf
        client.cookies.set("csrftoken", csrf, domain=".instagram.com")

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "X-IG-App-ID": IG_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{WEB_ROOT}/",
            "Origin": WEB_ROOT,
        }
        if self._csrf:
            headers["X-CSRFToken"] = self._csrf
        return headers

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not response.is_success or data.get("status") not in (None, "ok"):
            message = data.get("message") or data.get("status") or response.text[:300]
            raise RuntimeError(f"Instagram highlights error: {message}")
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
