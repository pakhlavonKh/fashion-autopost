"""Colourways of one product, read from its store page.

A product page lists every colour the model comes in. Each colour has its own
link, its own photos and often its own size grid, so the bot offers them to the
admin one by one: post the colour from the link, another one, or all of them.

Every link built here names its colour explicitly (Zara ?v1=, Pull&Bear ?cS=,
the other Inditex chains ?colorId=, Mango /99 or ?c=, H&M article number,
Salesforce stores _COLOUR.html or ?dwvar_<id>_color=, Shopify ?variant=). A
link without a colour opens the store's default colour, and that is how a beige
link used to post black.

Readers are tried from the most specific to the most general, so a store the
bot has never seen still gets its colours from schema.org data, a colour list
with links, or the colour switcher on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import html as html_lib
import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from core.dedup import is_color_key, normalize_url, variant_parts
from core.gallery import mango_page_identity
from core.page_data import page_json_values, searchable_texts, walk_dicts
from core.store_platforms import INDITEX_COLOR_PARAM, inditex_brand

logger = logging.getLogger(__name__)

_COLORS_ARRAY = re.compile(r'"(?:colou?rs|colou?rways|swatches)"\s*:\s*\[', re.IGNORECASE)
_COLORS_OBJECT = re.compile(r'"colou?rs"\s*:\s*\{', re.IGNORECASE)
_VARIATION_ATTRIBUTES = re.compile(r'"variationAttributes"\s*:\s*\[')
_MANGO_PHOTO = re.compile(r"/punto/(\d{6,10})-([0-9A-Za-z]{2,3})-", re.IGNORECASE)
_SWITCHER_TAG = re.compile(r"<(a|button|li|div|span|input|option)\b([^>]*)>(.*?)(?=<(?:a|button|li|div|span|input|option)\b|$)", re.IGNORECASE | re.DOTALL)
_LINK_ATTRS = ("href", "data-url", "data-href", "data-link", "data-product-url", "data-variant-url", "value")
_NAME_ATTRS = ("aria-label", "title", "data-color-name", "data-colour-name", "data-color", "data-colour", "data-name", "alt")
_MANGO_PATH_COLOR = re.compile(r"(/\d{7,10}/)([0-9a-z]{2,3})(?=/|$)", re.IGNORECASE)
_SFCC_PATH_COLOR = re.compile(r"(_)([A-Za-z0-9][A-Za-z0-9.\-]{0,15})(\.html?)$", re.IGNORECASE)
_SFCC_COLOR_PARAM = re.compile(r"^dwvar_(.+)_colou?r$", re.IGNORECASE)
_HM_ARTICLE = re.compile(r"productpage\.(\d{6,})", re.IGNORECASE)
_COLOR_OPTION = re.compile(r"colou?r|farbe|couleur|colore|cor\b|kleur|kolor|цвет", re.IGNORECASE)
_TRACKING = re.compile(r"^(utm_|gclid$|fbclid$|igshid$|_gl$|mc_|ampv$|ref$)", re.IGNORECASE)


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

    Returns an empty list when the page names fewer than two colours.
    """
    if not html_text or not page_url:
        return []
    host = urlsplit(page_url).netloc.lower()
    texts = searchable_texts(html_text)

    platform: list = []
    if host.endswith("hm.com"):
        platform.append(_hm_variants)
    if inditex_brand(page_url):
        platform.append(_inditex_variants)
    if "mango." in host:
        platform.append(_mango_variants)
    general = [
        _variation_attribute_variants,
        _shopify_variants,
        _json_ld_variants,
        _linked_color_array_variants,
    ]

    attempts = [(reader, text) for reader in platform for text in texts]
    attempts += [(reader, html_text) for reader in general]
    attempts.append((partial(_switcher_variants, current_color=current_color), html_text))
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
    if variant_parts(url)[1]:
        return True
    return bool(_HM_ARTICLE.search(urlsplit(url or "").path or ""))


def color_token(url: str) -> str:
    _product, color = variant_parts(url)
    if color:
        return color
    article = _HM_ARTICLE.search(url or "")
    return article.group(1) if article else ""


def same_variant_url(left: str, right: str) -> bool:
    """Two links open the same colourway of the same product."""
    return bool(left and right) and normalize_url(left) == normalize_url(right)


def set_query_param(url: str, key: str, value: str) -> str:
    """The link with one query parameter set, tracking tags and other colour keys dropped."""
    parts = urlsplit(url)
    kept = [
        (name, val)
        for name, val in parse_qsl(parts.query, keep_blank_values=False)
        if name.lower() != key.lower() and not is_color_key(name) and not _TRACKING.match(name)
    ]
    kept.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def mango_color_url(page_url: str, code: str) -> str:
    parts = urlsplit(page_url)
    if _MANGO_PATH_COLOR.search(parts.path):
        path = _MANGO_PATH_COLOR.sub(lambda m: f"{m.group(1)}{code}", parts.path, count=1)
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return set_query_param(page_url, "c", code)


def sfcc_color_url(page_url: str, code: str, hint: str = "") -> str:
    """A Salesforce store link for one colour, in the shape the page's own link uses."""
    parts = urlsplit(page_url)
    for key, _value in parse_qsl(parts.query):
        if _SFCC_COLOR_PARAM.match(key):
            return set_query_param(page_url, key, code)
    product, color = variant_parts(page_url)
    if color and _SFCC_PATH_COLOR.search(parts.path):
        path = _SFCC_PATH_COLOR.sub(lambda m: f"{m.group(1)}{code}{m.group(3)}", parts.path, count=1)
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    for key, _value in parse_qsl(urlsplit(hint or "").query):
        if _SFCC_COLOR_PARAM.match(key):
            return set_query_param(page_url, key, code)
    return ""


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
        token = color_token(page_url).lower()
        if token:
            index = next((i for i, item in enumerate(found) if item.code.lower() == token), None)
    if index is None:
        index = next((i for i, item in enumerate(found) if item.selected), None)
    if index is None and current_color:
        wanted = current_color.strip().casefold()
        index = next((i for i, item in enumerate(found) if item.name.strip().casefold() == wanted), None)
    return [
        ColorVariant(item.code, item.name, item.url, selected=(i == index))
        for i, item in enumerate(found)
    ]


def _decode_at(text: str, start: int):
    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except (json.JSONDecodeError, ValueError):
        return None
    return value


def _color_arrays(text: str):
    """Every JSON array under a "colors" key, as decoded lists of dicts."""
    for match in _COLORS_ARRAY.finditer(text):
        items = _decode_at(text, match.end() - 1)
        if isinstance(items, list) and items and all(isinstance(item, dict) for item in items):
            yield items
    # Some pages key colours by code: "colors":{"75":{...},"99":{...}}.
    for match in _COLORS_OBJECT.finditer(text):
        value = _decode_at(text, match.end() - 1)
        if isinstance(value, dict) and value and all(isinstance(item, dict) for item in value.values()):
            yield [{"id": key, **item} if "id" not in item else item for key, item in value.items()]


def _entry_name(entry: dict) -> str:
    for key in ("name", "colorName", "colourName", "displayValue", "label", "value", "description"):
        value = entry.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("label")
        if isinstance(value, str) and value.strip() and not value.strip().startswith(("http", "/")):
            return " ".join(value.split())
    return ""


def _entry_code(entry: dict) -> str:
    for key in ("id", "code", "colorId", "colorCode", "colourCode", "value"):
        value = entry.get(key)
        if value is not None and not isinstance(value, (dict, list)) and str(value).strip():
            return str(value).strip()
    return ""


def _entry_url(entry: dict, page_url: str) -> str:
    for key in ("url", "href", "link", "productUrl", "pdpUrl", "seoUrl"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip() and not value.strip().startswith(("data:", "#", "javascript")):
            return urljoin(page_url, value.strip())
    return ""


def _selected(entry: dict) -> bool:
    return any(entry.get(key) is True for key in ("selected", "isSelected", "current", "isCurrent", "active"))


# --- platform readers --------------------------------------------------------


def _inditex_variants(text: str, page_url: str) -> list[ColorVariant]:
    """Zara opens a colour with ?v1=<productId>; the other chains with ?colorId= (Pull&Bear ?cS=)."""
    brand = inditex_brand(page_url)
    is_zara = brand == "zara"
    v1 = _query_value(page_url, "v1")
    # Keep whichever colour key the admin's link already uses.
    param = next(
        (key for key, _value in parse_qsl(urlsplit(page_url).query) if key.lower() in {"colorid", "cs"}),
        INDITEX_COLOR_PARAM.get(brand, "colorId"),
    )
    best: list[ColorVariant] = []
    for items in _color_arrays(text):
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
                url = set_query_param(page_url, param, code)
            variants.append(ColorVariant(code, name, url, _selected(entry)))
        # The page's own product comes first; recommendations list colours later on.
        if has_v1:
            return variants
        if len(variants) >= 2 and not best:
            best = variants
    return best


def _hm_variants(text: str, page_url: str) -> list[ColorVariant]:
    """H&M gives each colour its own article number, and its own productpage.<article>.html."""
    from adapters.product_page import _extract_json_object_after

    details = _extract_json_object_after(text, '"productArticleDetails":')
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
            url = _entry_url(data, page_url)
        variants.append(ColorVariant(str(article), name, url, str(article) == current))
    return variants


def _mango_variants(text: str, page_url: str) -> list[ColorVariant]:
    """Mango names a colour by a two-digit code, in the path (/37016751/99) or as ?c=99."""
    page = mango_page_identity(page_url)
    own_code = page[1].lower() if page else ""
    best: list[ColorVariant] = []
    for items in _color_arrays(text):
        variants: list[ColorVariant] = []
        for entry in items:
            name = _entry_name(entry)
            code = _entry_code(entry)
            if not name or not re.fullmatch(r"[0-9A-Za-z]{2,3}", code):
                continue
            variants.append(ColorVariant(code, name, mango_color_url(page_url, code), _selected(entry)))
        if len(variants) < 2:
            continue
        # Recommendations carry colour lists too; the product's own one holds the link's colour.
        if own_code and any(item.code.lower() == own_code for item in variants):
            return variants
        if not best:
            best = variants
    return best


def _mango_photo_variants(text: str, page_url: str, current_color: str | None = None) -> list[ColorVariant]:
    """Last resort for Mango: colour codes in the product's own photo names (37066365-75-01).

    The names of the other colours are not on the photos, so they read «Цвет 99»
    until the admin opens that colour.
    """
    page = mango_page_identity(page_url)
    if not page:
        return []
    product_id, own_code = page
    codes: list[str] = []
    for found_id, code in _MANGO_PHOTO.findall(text):
        if found_id == product_id and code.lower() not in [item.lower() for item in codes]:
            codes.append(code)
    own = own_code.lower()
    return [
        ColorVariant(
            code,
            current_color.strip() if own and code.lower() == own and current_color else f"Цвет {code}",
            mango_color_url(page_url, code),
            bool(own) and code.lower() == own,
        )
        for code in codes
    ]


# --- readers for any store ---------------------------------------------------


def _variation_attribute_variants(text: str, page_url: str) -> list[ColorVariant]:
    """Salesforce Commerce Cloud and alike: variationAttributes → the colour attribute's values."""
    variants: list[ColorVariant] = []
    for match in _VARIATION_ATTRIBUTES.finditer(text):
        attributes = _decode_at(text, match.end() - 1)
        if not isinstance(attributes, list):
            continue
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            label = " ".join(str(attribute.get(key) or "") for key in ("attributeId", "id", "displayName", "name"))
            values = attribute.get("values")
            if not _COLOR_OPTION.search(label) or not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict) or value.get("selectable") is False:
                    continue
                code = str(value.get("value") or value.get("id") or "").strip()
                name = " ".join(str(value.get("displayValue") or value.get("name") or code).split())
                hint = _entry_url(value, page_url)
                url = sfcc_color_url(page_url, code, hint) or (hint if hint and "Product-Variation" not in hint else "")
                if code and name and url:
                    variants.append(ColorVariant(code, name, url, _selected(value)))
            if len(variants) >= 2:
                return variants
            variants = []
    return variants


def _shopify_variants(text: str, page_url: str) -> list[ColorVariant]:
    """Shopify product JSON: the colour option, one link per colour via ?variant=<first id>."""
    for document in page_json_values(text):
        for node in walk_dicts(document):
            options = node.get("options")
            variants = node.get("variants")
            if not isinstance(options, list) or not isinstance(variants, list) or not variants:
                continue
            names = [opt.get("name") if isinstance(opt, dict) else opt for opt in options]
            index = next((i for i, name in enumerate(names) if isinstance(name, str) and _COLOR_OPTION.search(name)), None)
            if index is None:
                continue
            key = f"option{index + 1}"
            found: list[ColorVariant] = []
            seen: set[str] = set()
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                value = variant.get(key)
                if not isinstance(value, str) or not value.strip() or value.casefold() in seen:
                    continue
                seen.add(value.casefold())
                variant_id = str(variant.get("id") or "").strip()
                if not variant_id:
                    continue
                found.append(ColorVariant(variant_id, " ".join(value.split()), set_query_param(page_url, "variant", variant_id)))
            if len(found) >= 2:
                return found
    return []


def _json_ld_variants(text: str, page_url: str) -> list[ColorVariant]:
    """schema.org ProductGroup: one variant per colour and size, each with its own link."""
    from adapters.product_page import _iter_json_ld

    variants: list[ColorVariant] = []
    for node in _iter_json_ld(text):
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
            variants.append(ColorVariant(color_token(url), " ".join(color.split()), url))
    # A group whose colours all share one link cannot open them apart.
    if len({normalize_url(item.url) for item in variants}) < 2:
        return []
    return variants


def _linked_color_array_variants(text: str, page_url: str) -> list[ColorVariant]:
    """Any store: a "colors" list whose entries carry a name and their own link."""
    for document in page_json_values(text):
        for node in walk_dicts(document):
            for key, items in node.items():
                if not isinstance(items, list) or not re.fullmatch(r"colou?rs|colou?rways|swatches|colou?rVariants", str(key), re.IGNORECASE):
                    continue
                found: list[ColorVariant] = []
                for entry in items:
                    if not isinstance(entry, dict):
                        continue
                    name = _entry_name(entry)
                    url = _entry_url(entry, page_url)
                    if name and url and urlsplit(url).netloc.lower() == urlsplit(page_url).netloc.lower():
                        found.append(ColorVariant(color_token(url) or _entry_code(entry), name, url, _selected(entry)))
                if len(found) >= 2:
                    return found
    return []


def _switcher_variants(text: str, page_url: str, current_color: str | None = None) -> list[ColorVariant]:
    """The colour switcher: elements linking to the same product in another colour, named on the element."""
    page_product, page_color = variant_parts(page_url, keep_case=True)
    page_article = (_HM_ARTICLE.search(page_url) or [None, ""])[1]
    if not page_product and not page_article:
        return []
    host = urlsplit(page_url).netloc.lower()
    variants: list[ColorVariant] = []
    for match in _SWITCHER_TAG.finditer(text):
        attrs, inner = match.group(2), match.group(3)
        for attr in _LINK_ATTRS:
            raw = _attr(attrs, attr)
            if not raw or raw.startswith(("#", "javascript", "data:")) or ("/" not in raw and "?" not in raw):
                continue
            url = urljoin(page_url, raw)
            if urlsplit(url).netloc.lower() != host:
                continue
            if page_article:
                article = (_HM_ARTICLE.search(url) or [None, ""])[1]
                # H&M colours share the first seven digits of the article number.
                if not article or article[:7] != page_article[:7]:
                    continue
                code, link = article, url
            else:
                product, color = variant_parts(url, keep_case=True)
                if product != page_product or not color:
                    continue
                code = color
                # Salesforce swatches point at an AJAX endpoint; post the product page itself.
                link = sfcc_color_url(page_url, color, url) or url if "Product-Variation" in url else url
            name = ""
            for key in _NAME_ATTRS:
                name = _clean_name(_attr(attrs, key))
                if name:
                    break
            if not name:
                name = _clean_name(re.sub(r"<[^>]+>", " ", inner))
            if name:
                variants.append(ColorVariant(code, name, link))
            break
    # The switcher often shows the open colour as plain text, not as a link.
    own_code = page_article or page_color
    if variants and own_code and current_color and all(item.code.lower() != own_code.lower() for item in variants):
        variants.insert(0, ColorVariant(own_code, current_color.strip(), page_url, True))
    return variants


def _attr(attrs: str, name: str) -> str:
    match = re.search(rf"(?<![\w-]){re.escape(name)}\s*=\s*([\"'])(.*?)\1", attrs, re.IGNORECASE | re.DOTALL)
    return html_lib.unescape(match.group(2)).strip() if match else ""


def _clean_name(text: str) -> str:
    """A colour name from a label such as «Color: Negro» or «Select Color Black»."""
    words = " ".join((text or "").split())
    words = re.sub(
        r"^(?:seleccionar colou?r|select colou?r|colou?r seleccionado|colou?r|farbe|couleur|colore|цвет)\s*[:\-]?\s*",
        "",
        words,
        flags=re.IGNORECASE,
    )
    if not words or len(words) > 40 or not re.search(r"[A-Za-zА-Яа-яЁё]", words):
        return ""
    return words
