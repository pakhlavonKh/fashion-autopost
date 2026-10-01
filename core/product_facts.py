"""Color and size grid taken from the store page.

Captions must repeat the color name and the full size range printed on the
product page. Nothing here invents a shade or a default XS–XL span.
"""

from __future__ import annotations

from dataclasses import replace
import json
import logging
import re
from collections.abc import Sequence
from urllib.parse import parse_qs, urlparse

from adapters.base import RawProduct

logger = logging.getLogger(__name__)

_SIZE_KEYS = ("sizes", "sizeOptions", "availableSizes", "sizeList")
_COLOR_KEYS = ("color", "colour", "colorName", "colorLabel", "colourName")
_QUERY_COLOR_KEYS = ("c", "color", "colorId", "colour", "v1", "colorid")
_COLOR_PARENTS = {"colors", "colours", "colorways", "variants", "variations", "color"}
_NOT_COLOR = {
    "in_stock", "out_of_stock", "available", "unavailable", "sold_out", "soldout",
    "true", "false", "null", "none", "default", "regular", "petite", "tall",
    "long", "short", "women", "men", "mujer", "hombre", "selected", "size",
    "sizes", "color", "colour", "new", "sale", "product",
}
_GARMENT = re.compile(
    r"\b(dress|cardigan|jeans|coat|shirt|blouse|skirt|trousers|pants|jacket|knit|"
    r"платье|кардиган|брюки|рубашк|юбк|пальто|пиджак)\b",
    re.IGNORECASE,
)
_LETTER_SIZE = re.compile(
    r"^(?:[2-9]XS|XXXS|XXS|XSS|XS|S|M|L|XL|XXL|XXXL|XXXXL|[2-9]XL)$",
    re.IGNORECASE,
)
_SIZE_TOKEN = re.compile(
    r"^(?:"
    r"[2-9]XS|XXXS|XXS|XSS|XS|S|M|L|XL|XXL|XXXL|XXXXL|[2-9]XL|"
    r"\d{1,2}(?:[./]\d{1,2})?|"
    r"ONE\s*SIZE|OS|TU|UNICA|ÚNICA|TALLA\s*ÚNICA|"
    r"(?:[2-9]XS|XXXS|XXS|XSS|XS|S|M|L|XL|XXL|[2-9]XL)\s*/\s*"
    r"(?:[2-9]XS|XXXS|XXS|XSS|XS|S|M|L|XL|XXL|[2-9]XL)"
    r")$",
    re.IGNORECASE,
)
_LETTER_RANK = {
    "XXXS": 0, "3XS": 0,
    "XXS": 1, "XSS": 1, "2XS": 1,
    "XS": 2,
    "S": 3,
    "M": 4,
    "L": 5,
    "XL": 6,
    "XXL": 7, "2XL": 7,
    "XXXL": 8, "3XL": 8,
    "XXXXL": 9, "4XL": 9,
    "5XL": 10,
    "6XL": 11,
    "7XL": 12,
    "8XL": 13,
    "9XL": 14,
}
_DOM_SIZE = re.compile(
    r"<(?:button|option|li|span|a)\b([^>]*)>([^<]{1,16})</(?:button|option|li|span|a)>",
    re.IGNORECASE,
)
_DOM_COLOR = (
    re.compile(r"itemprop=[\"']color[\"'][^>]*>([^<]{1,40})<", re.IGNORECASE),
    re.compile(
        r"class=[\"'][^\"']*(?:color-name|colorName|product-color|colour-name)[^\"']*[\"'][^>]*>([^<]{1,40})<",
        re.IGNORECASE,
    ),
    re.compile(r"data-color-name=[\"']([^\"']{1,40})[\"']", re.IGNORECASE),
)


class _Variant:
    def __init__(self, color: str | None, sizes: tuple[str, ...], ids: tuple[str, ...], selected: bool) -> None:
        self.color = color
        self.sizes = sizes
        self.ids = ids
        self.selected = selected


def extract_site_facts(html_text: str, url: str = "") -> tuple[str | None, tuple[str, ...]]:
    """Return the current color name and the full size grid from product HTML."""
    if not html_text:
        return None, ()
    found: list[_Variant] = []
    for document in _iter_json_documents(html_text):
        _consume(document, "", found)
    found.extend(_variants_from_size_arrays(html_text))

    color, sizes = _choose(found, url)
    sizes = _prefer_sizes(sizes, _sizes_from_dom(html_text))
    if not color:
        color = _color_from_dom(html_text)
    return color, tuple(sizes)


def site_description(color: str | None, sizes: Sequence[str]) -> str:
    """Caption lines for the size grid and color. Empty when the page did not say."""
    lines: list[str] = []
    ordered = order_sizes(sizes)
    if len(ordered) == 1:
        lines.append(f"Размер: {ordered[0]}.")
    elif len(ordered) >= 2:
        lines.append(f"Размеры от {ordered[0]} до {ordered[-1]}.")
    cleaned = " ".join((color or "").split()).strip(" .")
    if cleaned:
        lines.append(f"Цвет: {cleaned}.")
    return "\n".join(lines)


def _prefer_sizes(primary: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    """Keep the fuller grid. Do not replace letter sizes with a measurement chart."""
    if not extra:
        return primary
    if not primary:
        return extra

    def letter_count(items: tuple[str, ...]) -> int:
        return sum(1 for item in items if re.search(r"[A-Za-zА-Яа-яЁё]", item))

    if letter_count(primary) and not letter_count(extra):
        return primary
    if letter_count(extra) and not letter_count(primary):
        return extra
    if len(extra) > len(primary):
        return extra
    return primary


def order_sizes(sizes: Sequence[str]) -> list[str]:
    """Smallest to largest, using the labels printed on the site."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in sizes:
        label = clean_size(str(raw))
        if not label:
            continue
        key = re.sub(r"\s+", "", label).upper()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(label)
    return sorted(cleaned, key=_size_sort_key)


def clean_size(raw: str) -> str | None:
    """Keep a size label and drop prices, stock words, and measurement sentences."""
    text = " ".join((raw or "").split())
    if not text or len(text) > 20:
        return None
    if _SIZE_TOKEN.match(text):
        return _display_size(text)
    match = re.search(
        r"\b([2-9]XS|XXXS|XXS|XSS|XS|S|M|L|XL|XXL|XXXL|[2-9]XL|\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if match and not _GARMENT.search(text):
        return _display_size(match.group(1))
    return None


def attach_site_facts(product: RawProduct, *, timeout_seconds: float = 12.0) -> RawProduct:
    """Fill color and sizes from the product page when the listing did not include them."""
    if product.color and product.sizes:
        return product
    if not _looks_like_product_page(product.product_url):
        return product
    try:
        from adapters.product_page import _fetch_html_fast

        final_url, html_text = _fetch_html_fast(product.product_url, timeout_seconds)
    except Exception as exc:
        logger.info("Could not open product page for size and color %s: %s", product.product_url, exc)
        return product
    if not html_text:
        return product
    color, sizes = extract_site_facts(html_text, final_url or product.product_url)
    color = product.color or color
    sizes = product.sizes or sizes
    if color == product.color and sizes == product.sizes:
        return product
    logger.info(
        "Product page facts for %s: color=%s sizes=%s",
        product.external_id,
        color or "—",
        " ".join(sizes) if sizes else "—",
    )
    return replace(product, color=color, sizes=sizes)


def _looks_like_product_page(url: str) -> bool:
    """Listing cards and test placeholders are not product pages."""
    if not url or not url.startswith("http"):
        return False
    path = urlparse(url).path.lower()
    if "/p/" in path or "productpage" in path or "/product/" in path:
        return True
    return re.search(r"\d{5,}", path) is not None


def _display_size(raw: str) -> str:
    compact = re.sub(r"\s+", "", raw).upper()
    if _LETTER_SIZE.match(compact) or re.fullmatch(r"\d{1,2}", compact):
        return compact
    return " ".join(raw.split())


def _size_sort_key(label: str) -> tuple:
    compact = re.sub(r"\s+", "", label).upper()
    head = compact.split("/", 1)[0]
    if head in _LETTER_RANK:
        return (0, _LETTER_RANK[head], compact)
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)", compact)
    if match:
        return (1, float(match.group(1).replace(",", ".")), compact)
    return (2, 0, compact)


def _choose(found: list[_Variant], url: str) -> tuple[str | None, tuple[str, ...]]:
    if not found:
        return None, ()
    sized = [item for item in found if item.sizes]
    pool = sized or found
    best = max(pool, key=lambda item: _score(item, url))
    color = best.color
    if not color:
        related = [
            item for item in found
            if item.color and set(item.ids) & set(best.ids)
        ]
        if related:
            color = max(related, key=lambda item: _score(item, url)).color
        else:
            colored = [item for item in found if item.color]
            if len(colored) == 1:
                color = colored[0].color
            elif colored:
                color = max(colored, key=lambda item: _score(item, url)).color
    return color, order_sizes(best.sizes)


def _score(item: _Variant, url: str) -> int:
    score = len(item.sizes)
    if item.color:
        score += 3
    if item.selected:
        score += 40
    query_ids = _query_ids(url)
    for ident in item.ids:
        if not ident:
            continue
        if ident in query_ids:
            score += 200
        elif len(ident) >= 6 and ident in (url or ""):
            score += 100
    return score


def _query_ids(url: str) -> set[str]:
    if not url:
        return set()
    found: set[str] = set()
    parsed = urlparse(url)
    for key, values in parse_qs(parsed.query).items():
        if key.lower() not in {item.lower() for item in _QUERY_COLOR_KEYS}:
            continue
        for value in values:
            text = value.strip()
            if text:
                found.add(text)
    tail = re.search(r"[_-](\d{2,4})$", parsed.path)
    if tail:
        found.add(tail.group(1))
    return found


def _consume(node: object, parent_key: str, found: list[_Variant]) -> None:
    if isinstance(node, list):
        for item in node:
            _consume(item, parent_key, found)
        return
    if not isinstance(node, dict):
        return

    variants = node.get("hasVariant")
    if isinstance(variants, list):
        _add_grouped_variants(variants, found)

    labels = _sizes_on(node)
    color = _color_from(node, parent_key)
    if labels or color:
        found.append(_Variant(color, tuple(labels), _ids_on(node, parent_key), _is_selected(node)))

    for key, value in node.items():
        if key == "hasVariant":
            continue
        _consume(value, str(key), found)


def _add_grouped_variants(variants: list, found: list[_Variant]) -> None:
    grouped: dict[str, list[str]] = {}
    ids: dict[str, list[str]] = {}
    selected: set[str] = set()
    for item in variants:
        if not isinstance(item, dict):
            continue
        color = _color_from(item, "hasVariant") or ""
        labels = _labels_from_size_value(item.get("size"))
        if not labels:
            labels = _sizes_on(item)
        grouped.setdefault(color, []).extend(labels)
        ids.setdefault(color, []).extend(_ids_on(item, "hasVariant"))
        if _is_selected(item):
            selected.add(color)
    for color, labels in grouped.items():
        if not labels and not color:
            continue
        found.append(_Variant(color or None, tuple(labels), tuple(ids.get(color, ())), color in selected))


def _sizes_on(node: dict) -> list[str]:
    lowered = {str(key).lower() for key in node}
    if any(token in key for key in lowered for token in ("chest", "waist", "hip", "bust", "inseam", "measure")):
        return []
    for key in _SIZE_KEYS:
        if key not in node:
            continue
        labels = _labels_from_size_value(node.get(key))
        if labels and not _is_measurement_list(labels):
            return labels
        return []
    size_value = node.get("size")
    if isinstance(size_value, list):
        labels = _labels_from_size_value(size_value)
        if labels and not _is_measurement_list(labels):
            return labels
    return []


def _labels_from_size_value(value: object) -> list[str]:
    if isinstance(value, str):
        label = clean_size(value)
        return [label] if label else []
    if isinstance(value, list):
        labels: list[str] = []
        for item in value:
            label = _size_label(item)
            if label and label not in labels:
                labels.append(label)
        return labels
    if isinstance(value, dict):
        label = _size_label(value)
        if label:
            return [label]
        labels = []
        for key, item in value.items():
            if clean_size(str(key)):
                shown = clean_size(str(key))
                if shown and shown not in labels:
                    labels.append(shown)
                continue
            nested = _size_label(item)
            if nested and nested not in labels:
                labels.append(nested)
        return labels
    return []


def _size_label(item: object) -> str | None:
    if isinstance(item, str):
        return clean_size(item)
    if not isinstance(item, dict):
        return None
    for key in ("label", "name", "size", "sizeName", "value", "displayName", "shortName"):
        value = item.get(key)
        if isinstance(value, str):
            label = clean_size(value)
            if label:
                return label
        elif isinstance(value, dict):
            label = _size_label(value)
            if label:
                return label
    return None


def _is_measurement_list(labels: list[str]) -> bool:
    numbers: list[int] = []
    for label in labels:
        if not re.fullmatch(r"\d{1,3}", label):
            return False
        numbers.append(int(label))
    return bool(numbers) and min(numbers) >= 70


def _color_from(node: dict, parent_key: str) -> str | None:
    for key in _COLOR_KEYS:
        text = _color_text(node.get(key))
        if text:
            return text
    if _color_parent(parent_key):
        for key in ("label", "name", "description"):
            text = _color_text(node.get(key))
            if text:
                return text
    return None


def _color_parent(parent_key: str) -> bool:
    if parent_key.lower() in _COLOR_PARENTS or parent_key == "hasVariant":
        return True
    return parent_key.isdigit() and len(parent_key) >= 5


def _color_text(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "label", "value"):
            found = _color_text(value.get(key))
            if found:
                return found
        return None
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not _plausible_color(text):
        return None
    return text


def _plausible_color(text: str) -> bool:
    if not text or len(text) > 40:
        return False
    if not re.search(r"[A-Za-zА-Яа-яЁё]", text):
        return False
    if len(text.split()) > 4:
        return False
    if text.casefold() in _NOT_COLOR or clean_size(text):
        return False
    if text.startswith(("http://", "https://")) or _GARMENT.search(text):
        return False
    return True


def _ids_on(node: dict, parent_key: str) -> tuple[str, ...]:
    found: list[str] = []
    for key in ("id", "sku", "code", "articleCode", "productId", "reference", "colorId"):
        value = node.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text and text not in found:
            found.append(text)
    if parent_key.isdigit() and len(parent_key) >= 5 and parent_key not in found:
        found.append(parent_key)
    return tuple(found)


def _is_selected(node: dict) -> bool:
    for key in ("selected", "isSelected", "is_selected", "current", "isCurrent"):
        if node.get(key) is True:
            return True
    return False


def _iter_json_documents(html_text: str):
    for raw in re.findall(r"<script\b[^>]*>(.*?)</script>", html_text, flags=re.IGNORECASE | re.DOTALL):
        text = raw.strip()
        if not text or ("{" not in text and "[" not in text):
            continue
        start_obj = text.find("{")
        start_list = text.find("[")
        start = start_obj
        if start < 0 or (start_list >= 0 and start_list < start):
            start = start_list
        if start < 0:
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        yield value


def _variants_from_size_arrays(html_text: str) -> list[_Variant]:
    found: list[_Variant] = []
    for match in re.finditer(r'"(?:sizes|sizeOptions|availableSizes)"\s*:\s*\[', html_text):
        try:
            payload, _ = json.JSONDecoder().raw_decode(html_text[match.end() - 1 :])
        except json.JSONDecodeError:
            continue
        labels = _labels_from_size_value(payload)
        if not labels or _is_measurement_list(labels):
            continue
        window = html_text[max(0, match.start() - 500) : match.start()]
        lowered = window.lower().replace(" ", "")
        if "sizeguide" in lowered or "measurement" in lowered:
            continue
        found.append(_Variant(_color_before(window), tuple(labels), _ids_before(window), False))
    return found


def _color_before(window: str) -> str | None:
    color: str | None = None
    for match in re.finditer(
        r'"(?:label|name|color|colour|colorName|colorLabel)"\s*:\s*"([^"\\]{1,40})"',
        window,
    ):
        text = _color_text(match.group(1))
        if text:
            color = text
    return color


def _ids_before(window: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in re.finditer(r'"(?:id|sku|productId|colorId|reference)"\s*:\s*"?([A-Za-z0-9_-]{1,24})"?', window):
        text = match.group(1)
        if text not in found:
            found.append(text)
    return tuple(found[-4:])


def _sizes_from_dom(html_text: str) -> tuple[str, ...]:
    labels: list[str] = []
    for match in _DOM_SIZE.finditer(html_text):
        attrs = match.group(1).lower()
        if not any(token in attrs for token in ("size", "talla", "taille", "taglia", "größe", "grosse", "rozmiar")):
            continue
        label = clean_size(match.group(2))
        if label and label not in labels:
            labels.append(label)
    ordered = tuple(order_sizes(labels))
    if _is_measurement_list(list(ordered)):
        return ()
    return ordered


def _color_from_dom(html_text: str) -> str | None:
    for pattern in _DOM_COLOR:
        match = pattern.search(html_text)
        if not match:
            continue
        text = _color_text(match.group(1))
        if text:
            return text
    return None
