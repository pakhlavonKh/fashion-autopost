"""Product data that a store page hides inside JavaScript strings.

Next.js storefronts (Mango among them) stream the page data as React Server
Component chunks: self.__next_f.push([1,"5:{\\"product\\":{...}}"]). The JSON is
there, but escaped inside a string literal, so a search for "colors":[ or
"sizes":[ over the raw HTML finds nothing. These helpers give that JSON back as
plain text, in the order the page sent it.
"""

from __future__ import annotations

import json
import re

_FLIGHT_CHUNK = re.compile(r"self\.__next_f\.push\(\[\s*\d+\s*,\s*\"((?:[^\"\\]|\\.)*)\"\s*\]\)", re.DOTALL)
_ESCAPED_KEY = re.compile(r'\\"[A-Za-z_]+\\"\s*:')


def flight_text(html_text: str) -> str:
    """The decoded React Server Component payload of a Next.js page, or ""."""
    if not html_text or "__next_f" not in html_text:
        return ""
    parts: list[str] = []
    for match in _FLIGHT_CHUNK.finditer(html_text):
        try:
            parts.append(json.loads(f'"{match.group(1)}"'))
        except (json.JSONDecodeError, ValueError):
            continue
    return "".join(parts)


def unescaped_text(html_text: str) -> str:
    """The page with \\" turned back into ", for JSON escaped in some other script string.

    Only a fallback: it is not a real string decoder, but raw_decode on the
    result still finds arrays such as "colors":[...] that were escaped once.
    """
    if not html_text or not _ESCAPED_KEY.search(html_text):
        return ""
    return html_text.replace('\\\\"', '"').replace('\\"', '"').replace("\\/", "/")


def searchable_texts(html_text: str) -> list[str]:
    """The page itself, then every decoded form of the data hidden in it."""
    texts = [html_text or ""]
    for extra in (flight_text(html_text), unescaped_text(html_text)):
        if extra:
            texts.append(extra)
    return texts


def iter_json_values(text: str, limit: int = 400):
    """Every JSON object or array that parses at some position of the text, in order."""
    decoder = json.JSONDecoder()
    idx = 0
    size = len(text or "")
    count = 0
    while idx < size and count < limit:
        start_obj = text.find("{", idx)
        start_list = text.find("[", idx)
        start = start_obj
        if start < 0 or (start_list >= 0 and start_list < start):
            start = start_list
        if start < 0:
            break
        try:
            value, end_offset = decoder.raw_decode(text[start:])
        except (json.JSONDecodeError, ValueError):
            idx = start + 1
            continue
        idx = start + max(1, end_offset)
        count += 1
        if value:
            yield value


_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def page_json_values(html_text: str, per_script: int = 8):
    """JSON found in the page's scripts, then in its Next.js payload."""
    for raw in _SCRIPT.findall(html_text or ""):
        text = raw.strip()
        if text and ("{" in text or "[" in text):
            yield from iter_json_values(text, limit=per_script)
    hidden = flight_text(html_text)
    if hidden:
        yield from iter_json_values(hidden, limit=400)


def walk_dicts(value):
    """Every dict nested anywhere in a decoded JSON value."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
