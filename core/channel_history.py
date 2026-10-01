"""Read what the Telegram channel actually publishes.

A bot cannot fetch past channel messages through the Bot API, but a public
channel shows its posts on t.me/s/<name>. Those captions are the first-hand
answer to "what does this channel sell", including posts made before the bot
existed, so the style profile is built from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import html as html_lib
import logging
import re

import httpx

logger = logging.getLogger(__name__)

PREVIEW_URL = "https://t.me/s/{channel}"
MAX_PAGES = 6
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.9",
}

# The contact block repeats on every post and says nothing about the garment.
_FOOTER_START = re.compile(
    r"(европейское\s+качество|обращаться\s*:|отзывы\s*:|товары\s+в\s+наличии|"
    r"наш\s+(instagram|telegram|основной)|t\.me/|instagram\.com|тел\s*:)",
    re.IGNORECASE,
)
# Captions open with "Прямой жакет с поясом-85$".
_TITLE_PRICE = re.compile(
    r"^(?P<title>.+?)\s*[-–—]\s*(?P<price>\d{1,6}(?:[.,]\d{1,2})?)\s*(?:\$|usd|у\.?\s?е\.?)\s*$",
    re.IGNORECASE,
)
_POST_ANCHOR = re.compile(r'data-post="[^"]+/(\d+)"')
_MESSAGE_TEXT = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)


@dataclass(frozen=True)
class ChannelPost:
    """One published post, shaped like the product rows the profile expects."""

    title: str
    description: str
    price_original: Decimal | None = None
    category: str = ""
    source: str = ""


def channel_username(raw: str | None) -> str | None:
    """Public handle of the channel to learn from. None for a private chat id."""
    for part in str(raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        match = re.search(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})", text)
        if match:
            return match.group(1)
    return None


def fetch_channel_posts(
    channel: str | None,
    *,
    limit: int = 50,
    markup: Decimal = Decimal("0"),
    timeout_seconds: float = 15.0,
) -> list[ChannelPost]:
    """Recent posts of a public channel, newest first. Empty when it cannot be read."""
    handle = channel_username(channel)
    if not handle:
        return []

    posts: list[ChannelPost] = []
    seen: set[str] = set()
    before: str | None = None
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            for _ in range(MAX_PAGES):
                url = PREVIEW_URL.format(channel=handle)
                response = client.get(url, params={"before": before} if before else None)
                if response.status_code != 200:
                    logger.info("Channel preview for %s answered HTTP %s", handle, response.status_code)
                    break
                page_captions, oldest_id = _page_captions(response.text)
                if not page_captions:
                    break
                for caption in page_captions:
                    post = parse_post_caption(caption, markup=markup)
                    if post is None or post.title.casefold() in seen:
                        continue
                    seen.add(post.title.casefold())
                    posts.append(post)
                if len(posts) >= limit or not oldest_id:
                    break
                before = oldest_id
    except Exception as exc:
        logger.info("Could not read the posts of channel %s: %s", handle, exc)
        return posts[:limit]

    logger.info("Read %d posts from the Telegram channel @%s", len(posts[:limit]), handle)
    return posts[:limit]


def parse_post_caption(caption: str, *, markup: Decimal = Decimal("0")) -> ChannelPost | None:
    """Split a post caption into the garment name, its description and the store price.

    The caption carries the selling price, so the markup is taken back off to
    keep it comparable with the prices the stores show.
    """
    lines = [line.strip() for line in (caption or "").splitlines() if line.strip()]
    if not lines:
        return None

    head = _TITLE_PRICE.match(lines[0])
    title = (head.group("title") if head else lines[0]).strip(" .")
    # A caption that opens with a bare price or an emoji says nothing about style.
    if not re.search(r"[A-Za-zА-Яа-яЁё]{3,}", title) or _FOOTER_START.search(title):
        return None

    body: list[str] = []
    for line in lines[1:]:
        if _FOOTER_START.search(line):
            break
        body.append(line)

    return ChannelPost(
        title=title,
        description=" ".join(body),
        price_original=_store_price(head.group("price") if head else None, markup),
    )


def _store_price(raw: str | None, markup: Decimal) -> Decimal | None:
    if not raw:
        return None
    try:
        selling = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    store = selling - markup
    return store if store > Decimal("0") else None


def _page_captions(html_text: str) -> tuple[list[str], str | None]:
    """One caption per message block, plus the oldest post id for paging back."""
    anchors = list(_POST_ANCHOR.finditer(html_text))
    if not anchors:
        return [], None

    captions: list[str] = []
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(html_text)
        block = html_text[anchor.start():end]
        found = _MESSAGE_TEXT.search(block)
        if found:
            captions.append(_plain_text(found.group(1)))

    ids = [int(anchor.group(1)) for anchor in anchors]
    return captions, str(min(ids)) if ids else None


def _plain_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text)
