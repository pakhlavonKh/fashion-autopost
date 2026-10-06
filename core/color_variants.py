"""Colourways of one product, read from its store page.

A product page lists every colour the model comes in. Each colour has its own
link, its own photos and often its own size grid, so the bot offers them to the
admin one by one: post the colour from the link, another one, or all of them.

Every link built here names its colour explicitly (Zara ?v1=, Inditex
?colorId=, Mango /99 or ?c=, H&M article number). A link without a colour opens
the store's default colour, and that is how a beige link used to post black.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import html as html_lib
import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from core.dedup import _COLOR_QUERY_KEYS as _DEDUP_COLOR_KEYS, normalize_url, variant_parts
from core.gallery import mango_page_identity
from core.page_data import searchable_texts

logger = logging.getLogger(__name__)

_INDITEX_BRANDS = ("stradivarius", "massimodutti", "bershka", "pullandbear", "oysho")
_COLORS_ARRAY = re.compile(r'"colou?rs"\s*:\s*\[')
_COLORS_OBJECT = re.compile(r'"colou?rs"\s*:\s*\{')
_MANGO_PHOTO = re.compile(r"/punto/(\d{6,10})-([0-9A-Za-z]{2,3})-", re.IGNORECASE)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_MANGO_PATH_COLOR = re.compile(r"(/\d{7,10}/)([0-9a-z]{2,3})(?=/|$)", re.IGNORECASE)
_HM_ARTICLE = re.compile(r"productpage\.(\d{6,})", re.IGNORECASE)
COLOR_QUERY_KEYS = frozenset(_DEDUP_COLOR_KEYS)
_TRACKING = re.compile(r"^(utm_|gclid$|fbclid$|igshid$|_gl$|mc_)", re.IGNORECASE)


@dataclass(frozen=True)
class ColorVariant:
    """One colourway: the store's code, the store's name and the link that opens it."""

    code: str
    name: str
    url: str
    selected: bool = False

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "name": self.name, "url": self.url, "selected": self.selected}


def extract_color_variants(html_text: str, page_url: str, current_color: str | None = None) -> list[ColorVariant]:
    """All colourways the page offers, the one the link opens marked as selected.

    No colour is marked when the page does not say which one it shows; a guess
    here would post a colour the admin never saw.

    Store pages change shape, so several readings are tried in turn: the store's
    own JSON (plain, or escaped inside Next.js chunks), schema.org JSON-LD, the
    colour switcher links, and for Mango the colour codes in its photo names.

    Returns an empty list when the page names fewer than two colours.
    """
    if not html_text or not page_url:
        return []
    host = urlsplit(page_url).netloc.lower()
    texts = searchable_texts(html_text)
    readers: list = []
    if host.endswith("hm.com"):
        readers.append(_hm_variants)
    if "zara." in host or any(brand in host for brand in _INDITEX_BRANDS):
        readers.append(_inditex_variants)
    if "mango." in host:
        readers.append(_mango_variants)
    readers.append(_json_ld_variants)

    attempts = [(reader, text) for reader in readers for text in texts]
    attempts.append((partial(_switcher_link_variants, current_color=current_color), html_text))
    if "mango." in host:
        attempts.append((partial(_mango_photo_variants, current_color=current_color), html_text))

    for reader, text in attempts:
        try:
            found = reader(text, page_url)
        except Exception as exc:  # A page shape we do not know must not stop the post.
            logger.debug("%s could not read colours from %s: %s", _reader_name(reader), page_url, exc)
            continue
        found = _unique(found)
        if len(found) >= 2:
            logger.info(
                "Colours on %s via %s: %s",
                page_url,
                _reader_name(reader),
                ", ".join(f"{item.name} ({item.code})" for item in found),
            )
            return _mark_selected(found, page_url, current_color)
    logger.info("No colour choice found on %s", page_url)
    return []


def _reader_name(reader) -> str:
    return getattr(getattr(reader, "func", reader), "__name__", "reader")


def link_names_color(url: str) -> bool:
    """True when the link itself picks a colour, so it never opens the default one."""
    parts = urlsplit(url or "")
    if any(key.lower() in COLOR_QUERY_KEYS and value for key, value in parse_qsl(parts.query)):
        return True
    if _MANGO_PATH_COLOR.search(parts.path or "") and "mango." in parts.netloc.lower():
        return True
    return bool(_HM_ARTICLE.search(parts.path or ""))


def same_variant_url(left: str, right: str) -> bool:
    """Two links open the same colourway of the same product."""
    return bool(left and right) and normalize_url(left) == normalize_url(right)


def set_query_param(url: str, key: str, value: str) -> str:
    """The link with one query parameter set, tracking tags and other colour keys dropped."""
    parts = urlsplit(url)
    kept = [
        (name, val)
        for name, val in parse_qsl(parts.query, keep_blank_values=False)
        if name.lower() != key.lower()
        and name.lower() not in COLOR_QUERY_KEYS
        and not _TRACKING.match(name)
    ]
    kept.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _query_value(url: str, *keys: str) -> str:
    wanted = {key.lower() for key in keys}
    for name, value in parse_qsl(urlsplit(url or "").query):
        if name.lower() in wanted and value.strip():
            return value.strip()
    return ""


def _unique(found: list[ColorVariant]) -> list[ColorVariant]:
    seen_codes: set[str] = set()
    seen_urls: set[str] = set()
    result: list[ColorVariant] = []
    for item in found:
        if not item.url or not item.name:
            continue
        key_url = normalize_url(item.url)
        key_code = item.code.lower()
        if key_url in seen_urls or (key_code and key_code in seen_codes):
            continue
        seen_urls.add(key_url)
        if key_code:
            seen_codes.add(key_code)
        result.append(item)
    return result


def _mark_selected(found: list[ColorVariant], page_url: str, current_color: str | None) -> list[ColorVariant]:
    """The link's own colour: by the link, the page's flag, then the name. None when unsure."""
    index = next((i for i, item in enumerate(found) if same_variant_url(item.url, page_url)), None)
    if index is None:
        index = next((i for i, item in enumerate(found) if item.selected), None)
    if index is None and current_color:
        wanted = current_color.strip().casefold()
        index = next((i for i, item in enumerate(found) if item.name.strip().casefold() == wanted), None)
    return [
        ColorVariant(item.code, item.name, item.url, selected=(i == index))
        for i, item in enumerate(found)
    ]


def _decode_array_at(html_text: str, start: int) -> list | None:
    try:
        value, _end = json.JSONDecoder().raw_decode(html_text[start:])
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def _color_arrays(html_text: str):
    """Every JSON array under a "colors" key, as decoded lists of dicts."""
    for match in _COLORS_ARRAY.finditer(html_text):
        items = _decode_array_at(html_text, match.end() - 1)
        if items and all(isinstance(item, dict) for item in items):
            yield items
    # Some pages key colours by code: "colors":{"75":{...},"99":{...}}.
    for match in _COLORS_OBJECT.finditer(html_text):
        try:
            value, _end = json.JSONDecoder().raw_decode(html_text[match.end() - 1:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict) and value and all(isinstance(item, dict) for item in value.values()):
            yield [{"id": key, **item} if "id" not in item else item for key, item in value.items()]


def _entry_name(entry: dict) -> str:
    for key in ("name", "colorName", "colourName", "label", "description"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _entry_code(entry: dict) -> str:
    for key in ("id", "code", "colorId", "colorCode", "colourCode"):
        value = entry.get(key)
        if value is not None and not isinstance(value, (dict, list)) and str(value).strip():
            return str(value).strip()
    return ""


def _inditex_variants(html_text: str, page_url: str) -> list[ColorVariant]:
    """Zara opens a colour with ?v1=<productId>; Bershka and its siblings with ?colorId=<code>."""
    host = urlsplit(page_url).netloc.lower()
    is_zara = "zara." in host
    v1 = _query_value(page_url, "v1")
    best: list[ColorVariant] = []
    for items in _color_arrays(html_text):
        variants: list[ColorVariant] = []
        has_v1 = False
        for entry in items:
            name = _entry_name(entry)
            code = _entry_code(entry)
            if not name or not code:
                continue
            product_id = str(entry.get("productId") or "").strip()
            if is_zara:
                if not product_id:
                    continue
                url = set_query_param(page_url, "v1", product_id)
                has_v1 = has_v1 or product_id == v1
                code = product_id
            else:
                url = set_query_param(page_url, "colorId", code)
            selected = bool(entry.get("selected") or entry.get("isSelected"))
            variants.append(ColorVariant(code, name, url, selected))
        # The page's own product comes first; recommendations list colours later on.
        if has_v1:
            return variants
        if len(variants) >= 2 and not best:
            best = variants
    return best


def _hm_variants(html_text: str, page_url: str) -> list[ColorVariant]:
    """H&M gives each colour its own article number, and its own productpage.<article>.html."""
    from adapters.product_page import _extract_json_object_after

    details = _extract_json_object_after(html_text, '"productArticleDetails":')
    if not isinstance(details, dict):
        return []
    variations = details.get("variations")
    if not isinstance(variations, dict):
        return []
    current = (_HM_ARTICLE.search(page_url) or [None, ""])[1]
    variants: list[ColorVariant] = []
    for article, data in variations.items():
        if not isinstance(data, dict) or not re.fullmatch(r"\d{6,}", str(article)):
            continue
        name = _entry_name(data)
        if not name:
            continue
        if current:
            url = _HM_ARTICLE.sub(f"productpage.{article}", page_url.split("?")[0].split("#")[0], count=1)
        else:
            url = str(data.get("url") or "")
            if url.startswith("/"):
                parts = urlsplit(page_url)
                url = f"{parts.scheme}://{parts.netloc}{url}"
        variants.append(ColorVariant(str(article), name, url, str(article) == current))
    return variants


def _json_ld_variants(html_text: str, page_url: str) -> list[ColorVariant]:
    """schema.org ProductGroup: one variant per colour and size, each with its own link."""
    from adapters.product_page import _iter_json_ld

    variants: list[ColorVariant] = []
    for node in _iter_json_ld(html_text):
        children = node.get("hasVariant")
        if not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict):
                continue
            color = child.get("color")
            if isinstance(color, dict):
                color = color.get("name")
            if not isinstance(color, str) or not color.strip():
                continue
            offers = child.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                offers = {}
            url = str(child.get("url") or offers.get("url") or "")
            if not url.startswith("http"):
                continue
            code = color_token(url)
            variants.append(ColorVariant(code, " ".join(color.split()), url))
    # A group whose colours all share one link cannot open them apart.
    if len({normalize_url(item.url) for item in variants}) < 2:
        return []
    return variants


def _mango_variants(html_text: str, page_url: str) -> list[ColorVariant]:
    """Mango names a colour by a two-digit code, in the path (/37016751/99) or as ?c=99."""
    page = mango_page_identity(page_url)
    own_code = page[1].lower() if page else ""
    best: list[ColorVariant] = []
    for items in _color_arrays(html_text):
        variants: list[ColorVariant] = []
        for entry in items:
            name = _entry_name(entry)
            code = _entry_code(entry)
            if not name or not re.fullmatch(r"[0-9A-Za-z]{2,3}", code):
                continue
            selected = bool(entry.get("selected") or entry.get("isSelected") or entry.get("default") is True)
            variants.append(ColorVariant(code, name, mango_color_url(page_url, code), selected))
        if len(variants) < 2:
            continue
        # Recommendations carry colour lists too; the product's own one holds the link's colour.
        if own_code and any(item.code.lower() == own_code for item in variants):
            return variants
        if not best:
            best = variants
    return best


def _mango_photo_variants(html_text: str, page_url: str, current_color: str | None = None) -> list[ColorVariant]:
    """Last resort for Mango: colour codes in the product's own photo names (37066365-75-01).

    The names of the other colours are not on the photos, so they read «Цвет 99»
    until the admin opens that colour.
    """
    page = mango_page_identity(page_url)
    if not page:
        return []
    product_id, own_code = page
    codes: list[str] = []
    for found_id, code in _MANGO_PHOTO.findall(html_text):
        if found_id == product_id and code.lower() not in [item.lower() for item in codes]:
            codes.append(code)
    return [
        ColorVariant(
            code,
            (current_color or "").strip() if own_code and code.lower() == own_code.lower() and current_color else f"Цвет {code}",
            mango_color_url(page_url, code),
            bool(own_code) and code.lower() == own_code.lower(),
        )
        for code in codes
    ]


def _switcher_link_variants(html_text: str, page_url: str, current_color: str | None = None) -> list[ColorVariant]:
    """Colour switcher links: same product, another colour in the link, the colour named on the link."""
    page_product, page_color = variant_parts(page_url)
    page_article = (_HM_ARTICLE.search(page_url) or [None, ""])[1]
    if not page_product and not page_article:
        return []
    variants: list[ColorVariant] = []
    for match in _ANCHOR.finditer(html_text):
        attrs, inner = match.group(1), match.group(2)
        href = _attr(attrs, "href")
        if not href:
            continue
        url = urljoin(page_url, html_lib.unescape(href))
        if urlsplit(url).netloc.lower() != urlsplit(page_url).netloc.lower():
            continue
        if page_article:
            article = (_HM_ARTICLE.search(url) or [None, ""])[1]
            # H&M colours share the first seven digits of the article number.
            if not article or article[:7] != page_article[:7]:
                continue
            code = article
        else:
            product, color = variant_parts(url)
            if product != page_product or not color:
                continue
            code = color
        name = ""
        for key in ("aria-label", "title", "data-color-name", "data-colour-name", "data-color"):
            name = _clean_name(_attr(attrs, key))
            if name:
                break
        if not name:
            name = _clean_name(re.sub(r"<[^>]+>", " ", inner))
        if name:
            variants.append(ColorVariant(code, name, url))
    # The switcher often shows the open colour as plain text, not as a link.
    own_code = page_article or page_color
    if variants and own_code and current_color and all(item.code.lower() != own_code.lower() for item in variants):
        variants.insert(0, ColorVariant(own_code, current_color.strip(), page_url, True))
    return variants


def _attr(attrs: str, name: str) -> str:
    match = re.search(rf"(?<![\w-]){re.escape(name)}\s*=\s*([\"'])(.*?)\1", attrs, re.IGNORECASE | re.DOTALL)
    return html_lib.unescape(match.group(2)).strip() if match else ""


def _clean_name(text: str) -> str:
    """A colour name from a label such as «Color: Negro» or «Seleccionar color Burdeos»."""
    words = " ".join((text or "").split())
    words = re.sub(r"^(?:seleccionar colou?r|select colou?r|colou?r seleccionado|colou?r|farbe|couleur|colore|цвет)\s*[:\-]?\s*", "", words, flags=re.IGNORECASE)
    if not words or len(words) > 40 or not re.search(r"[A-Za-zА-Яа-яЁё]", words):
        return ""
    return words


def mango_color_url(page_url: str, code: str) -> str:
    parts = urlsplit(page_url)
    if _MANGO_PATH_COLOR.search(parts.path):
        path = _MANGO_PATH_COLOR.sub(lambda m: f"{m.group(1)}{code}", parts.path, count=1)
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return set_query_param(page_url, "c", code)


def color_token(url: str) -> str:
    _product, color = variant_parts(url)
    if color:
        return color
    article = _HM_ARTICLE.search(url or "")
    return article.group(1) if article else ""
