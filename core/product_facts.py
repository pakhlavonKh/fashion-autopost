"""Color, size grid, and heel height taken from the store page.

Captions must repeat the color name, the full size range, and — for heeled
shoes — the heel height printed on the product page. Nothing here invents a
shade, a default XS–XL span, or a heel measurement.
"""

from __future__ import annotations

from dataclasses import replace
import html as html_lib
import json
import logging
import re
from collections.abc import Sequence
from urllib.parse import parse_qs, urlparse

from adapters.base import RawProduct
from core.color_names import russian_color
from core.page_data import flight_text, iter_json_values

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
    # Storefronts that hash their class names still keep the word in them,
    # as in «ColorsSelector-module__F5Cauq__label». The «label» must sit in the
    # same class name as «color»: Bershka's reference line carries an unrelated
    # «bds-typography-label-xs» next to «color-selector__reference».
    re.compile(
        r"class=[\"'][^\"']*colou?rs?[^\"'\s]*(?:label|name|value)[^\"']*[\"'][^>]*>([^<]{1,40})<",
        re.IGNORECASE,
    ),
    re.compile(r"<input\b[^>]*name=[\"'][^\"']*(?:colou?r)[^\"']*[\"'][^>]*value=[\"']([^\"']{1,40})[\"']", re.IGNORECASE),
    re.compile(r"<input\b[^>]*value=[\"']([^\"']{1,40})[\"'][^>]*name=[\"'][^\"']*(?:colou?r)[^\"']*[\"']", re.IGNORECASE),
    re.compile(r"colou?r:\s*</span>\s*<[^>]+>([^<]{1,40})<", re.IGNORECASE),
)
# A colour name element whose text follows a hidden screen-reader label, as in
# <span class="color-selector__name"><span class="sr-only">Farbe</span> Taupe</span>.
_DOM_COLOR_NAMED = re.compile(
    r"<(?:span|div|p|strong|h\d)\b[^>]*class=[\"'][^\"']*colou?rs?[^\"'\s]*name[^\"']*[\"'][^>]*>",
    re.IGNORECASE,
)
_HIDDEN_LABEL = re.compile(
    r"<(\w+)\b[^>]*class=[\"'][^\"']*(?:sr-only|visually-hidden|screen-reader)[^\"']*[\"'][^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Article numbers such as «Ref. 1150/864/131» are not shades.
_REFERENCE_CODE = re.compile(r"^(?:ref|art|sku|cod|code)\b|\d{3,}", re.IGNORECASE)


class _Variant:
    def __init__(self, color: str | None, sizes: tuple[str, ...], ids: tuple[str, ...], selected: bool) -> None:
        self.color = color
        self.sizes = sizes
        self.ids = ids
        self.selected = selected


def extract_site_facts(
    html_text: str,
    url: str = "",
    prefer: Sequence[str] = (),
) -> tuple[str | None, tuple[str, ...]]:
    """Return the current color name and the full size grid from product HTML.

    prefer names the colour the link opens (its code and its name). A page
    lists every colour with its own sizes, and those are the ones to show.
    """
    if not html_text:
        return None, ()
    found: list[_Variant] = []
    for document in _iter_json_documents(html_text):
        _consume(document, "", found)
    found.extend(_variants_from_size_arrays(html_text))
    # Next.js storefronts keep the product JSON escaped inside script strings.
    hidden = flight_text(html_text)
    if hidden:
        for document in _iter_json_in_text(hidden, limit=400):
            _consume(document, "", found)
        found.extend(_variants_from_size_arrays(hidden))

    color, sizes = _choose(found, url, prefer)
    sizes = _prefer_sizes(sizes, _sizes_from_dom(html_text))
    if not color:
        color = _color_from_dom(html_text)
    return color, tuple(sizes)


def site_description(
    color: str | None,
    sizes: Sequence[str],
    heel_height: str | None = None,
) -> str:
    """Caption lines for the size grid, color, and heel height. Empty when the page did not say."""
    lines: list[str] = []
    ordered = order_sizes(sizes)
    if len(ordered) == 1:
        lines.append(f"Размер: {ordered[0]}.")
    elif len(ordered) >= 2:
        lines.append(f"Размеры от {ordered[0]} до {ordered[-1]}.")
    # Stores name the shade in their own language; the caption says it in Russian.
    cleaned = russian_color(color or "")
    if cleaned:
        lines.append(f"Цвет: {cleaned}.")
    heel_line = heel_caption_line(heel_height)
    if heel_line:
        lines.append(heel_line)
    return "\n".join(lines)


# A measurement counts only when the page itself labels it as heel height.
_HEEL_LABEL = re.compile(
    r"(?:"
    r"heel\s*height"
    r"|altura\s+del\s+tac[oó]n"
    r"|hauteur\s+du\s+talon"
    r"|absatzh[oö]he"
    r"|altezza\s+(?:del\s+)?tacco"
    r"|altura\s+do\s+salto"
    r"|wysoko(?:ść|sc)\s+obcasa"
    r"|topuk\s*(?:boyu|y[uü]ksekli[gğ]i)"
    r"|высота\s+каблука"
    r"|tac[oó]n\s+de"
    r"|de\s+tac[oó]n"
    r"|talon\s+de"
    r"|heel\s+of"
    r"|tac[oó]n\s*:"
    r")"
    r"(?:\s|&nbsp;|[:\-]){0,20}"
    r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(cm|см|mm|мм)\b",
    re.IGNORECASE,
)
_HEEL_AFTER = re.compile(
    r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(cm|см|mm|мм)\s*(?:high\s*)?(?:heels?|каблук\w*|tac[oó]n|topuk)\b",
    re.IGNORECASE,
)
_HEEL_NAME = re.compile(
    r"^(?:heel[\s_-]*height(?:cm|mm)?|altura\s+del\s+tac[oó]n|hauteur\s+du\s+talon|"
    r"absatzh[oö]he|altezza\s+(?:del\s+)?tacco|altura\s+do\s+salto|"
    r"wysoko(?:ść|sc)\s+obcasa|topuk\s*(?:boyu|y[uü]ksekli[gğ]i)|высота\s+каблука)$",
    re.IGNORECASE,
)
_MEASUREMENT = re.compile(
    r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(cm|см|mm|мм)\b",
    re.IGNORECASE,
)
_FOOTWEAR = re.compile(
    r"(?:"
    r"\b(?:shoes?|boots?|sandals?|heels?|pumps?|mules?|loafers?|sneakers?|"
    r"slingbacks?|stilettos?|zapatos?|botas?|sandalias?|tacones?|"
    r"ayakkab\w*|çizme|topuklu|sandalet)\b|"
    # German, French, Italian storefronts.
    r"\b(?:stiefel\w*|schuh\w*|sandale\w*|bottines?|escarpins?|chaussures?|"
    r"stival\w*|scarpe|sandali)\b|"
    r"туфл|ботил|сапог|босонож|ботин|обув|каблук|мюл|лодоч|сандал|кроссов|кед|сабо|шпильк"
    r")",
    re.IGNORECASE,
)
_HEEL_WORD = re.compile(
    r"(?:"
    r"\b(?:heels?|stilettos?|pumps?|topuklu|tacones?|tac[oó]n|talon|absatz|obcas)\b|"
    r"каблук|шпильк|ботильон"
    r")",
    re.IGNORECASE,
)
_NOT_HEEL = re.compile(
    r"(?:"
    r"\b(?:sneakers?|trainers?|running|flats?|ballet|loafers?|espadrilles?|"
    r"slippers?|slides?|flip[-\s]?flops?)\b|"
    r"кроссов|кед|балетк|лофер|эспадриль|слипон|шлепан|шлёпан|тапоч|мокасин"
    r")",
    re.IGNORECASE,
)
_DETAIL_LISTS = ("extraInfo", "attributes", "features", "details", "properties", "specifications")
_LABEL_FIELDS = ("name", "label", "type", "id", "key")
_VALUE_FIELDS = ("value", "description", "text", "content")


def is_footwear(*parts: str) -> bool:
    """True when the title, URL or text matches footwear (shoes, boots, sandals, sneakers, etc.)."""
    text = " ".join(str(part) for part in parts if part)
    return bool(_FOOTWEAR.search(text))


def is_heeled_footwear(*parts: str) -> bool:
    """True when the title or URL is footwear that is not a flat or a sneaker."""
    text = " ".join(part for part in parts if part)
    if not text or not _FOOTWEAR.search(text):
        return False
    if _HEEL_WORD.search(text):
        return True
    if _NOT_HEEL.search(text):
        return False
    return True


def format_heel_height(raw: str | None, adjust_cm: float = 0.0) -> str | None:
    """Keep a measurement the page printed, optionally adjusting by adjust_cm."""
    if not raw:
        return None
    match = _MEASUREMENT.search(str(raw))
    if not match:
        return None
    number, unit = match.group(1), match.group(2)
    try:
        value = float(number.replace(",", "."))
    except ValueError:
        return None
    if unit.lower() in {"mm", "мм"}:
        if not 10 <= value <= 200:
            return None
        value = round(value + adjust_cm * 10.0, 4)
        if value <= 0:
            return None
        shown_unit = "мм"
    else:
        if not 1 <= value <= 20:
            return None
        value = round(value + adjust_cm, 4)
        if value <= 0:
            return None
        shown_unit = "см"
    if abs(value - round(value)) < 1e-9:
        shown_number = str(int(round(value)))
    else:
        formatted_val = f"{value:.2f}".rstrip("0").rstrip(".")
        if "," in number:
            shown_number = formatted_val.replace(".", ",")
        else:
            shown_number = formatted_val
    return f"{shown_number} {shown_unit}"


def heel_caption_line(raw: str | None) -> str | None:
    """Russian caption line, or None when the page did not print a height."""
    shown = format_heel_height(raw, adjust_cm=-1.0)
    if not shown:
        return None
    return f"Высота каблука: {shown}."


def extract_heel_height(html_text: str, title: str = "") -> str | None:
    """Return the one heel height printed for this product, or None.

    Two different heights on the same page are left blank: the caption must
    not choose between them.
    """
    if not html_text:
        return None
    named: list[tuple[str, str]] = []
    for document in _iter_json_documents(html_text):
        _named_heights(document, named)
    if title:
        matched = _unique_heights([height for name, height in named if _same_product(name, title)])
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            return None
    visible = _unique_heights(_heights_in_text(_plain_text(html_text)))
    if len(visible) == 1:
        return visible[0]
    if len(visible) > 1:
        return None
    scripted = _unique_heights([*_heights_in_text(html_text), *(height for _, height in named)])
    if len(scripted) == 1:
        return scripted[0]
    return None


def _plain_text(html_text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html_text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text))


def _heights_in_text(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (_HEEL_LABEL, _HEEL_AFTER):
        for match in pattern.finditer(text):
            shown = format_heel_height(f"{match.group(1)} {match.group(2)}")
            if shown:
                found.append(shown)
    return found


def _named_heights(node: object, found: list[tuple[str, str]], ancestor_name: str = "") -> None:
    if isinstance(node, list):
        for item in node:
            _named_heights(item, found, ancestor_name)
        return
    if not isinstance(node, dict):
        return
    name = _product_name(node) or ancestor_name
    for height in _direct_heel_values(node):
        found.append((name, height))
    for value in node.values():
        _named_heights(value, found, name)


def _product_name(node: dict) -> str:
    for key in ("name", "title", "productName"):
        value = node.get(key)
        if not isinstance(value, str):
            continue
        text = " ".join(value.split())
        if len(text) < 3 or _HEEL_NAME.search(text) or format_heel_height(text):
            continue
        return text
    return ""


def _direct_heel_values(node: dict) -> list[str]:
    found: list[str] = []
    for key, value in node.items():
        if isinstance(key, str) and isinstance(value, str) and _HEEL_NAME.search(key.strip()):
            shown = format_heel_height(value)
            if shown:
                found.append(shown)
    for key in _DETAIL_LISTS:
        items = node.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            label = ""
            for label_key in _LABEL_FIELDS:
                raw = item.get(label_key)
                if isinstance(raw, str) and raw.strip():
                    label = raw.strip()
                    break
            if not label or not _HEEL_NAME.search(label):
                continue
            for value_key in _VALUE_FIELDS:
                raw = item.get(value_key)
                if not isinstance(raw, str):
                    continue
                shown = format_heel_height(raw)
                if shown:
                    found.append(shown)
                    break
    return found


def _same_product(name: str, title: str) -> bool:
    left = " ".join(name.casefold().split())
    right = " ".join(title.casefold().split())
    if len(left) < 3 or len(right) < 3:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    # A short shared word like "shoes" must not glue two different products together.
    if len(shorter) < 12:
        return False
    return shorter in longer


def _unique_heights(items: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: list[float] = []
    for item in items:
        mm = _as_mm(item)
        if mm is None:
            continue
        if any(abs(mm - previous) < 0.6 for previous in seen):
            continue
        seen.append(mm)
        unique.append(item)
    return unique


def _as_mm(shown: str) -> float | None:
    match = _MEASUREMENT.search(shown)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    if match.group(2).lower() in {"mm", "мм"}:
        return value
    return value * 10


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
    """Fill color, sizes, and heel height from the product page when they are missing."""
    needs_variant = not product.color or not product.sizes
    needs_heel = not product.heel_height and is_heeled_footwear(product.title, product.product_url)
    if not needs_variant and not needs_heel:
        return product
    if not _looks_like_product_page(product.product_url):
        return product
    try:
        from adapters.product_page import fetch_product_html

        final_url, html_text = fetch_product_html(product.product_url, timeout_seconds)
    except Exception as exc:
        logger.info("Could not open product page for size and color %s: %s", product.product_url, exc)
        return product
    if not html_text:
        return product
    page_url = final_url or product.product_url
    color, sizes = extract_site_facts(html_text, page_url)
    color = product.color or color
    sizes = product.sizes or sizes
    if is_heeled_footwear(product.title, product.product_url):
        heel = extract_heel_height(html_text, product.title) or product.heel_height
    else:
        heel = None
    if color == product.color and sizes == product.sizes and heel == product.heel_height:
        return product
    logger.info(
        "Product page facts for %s: color=%s sizes=%s heel=%s",
        product.external_id,
        color or "—",
        " ".join(sizes) if sizes else "—",
        heel or "—",
    )
    return replace(product, color=color, sizes=sizes, heel_height=heel)


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


def _choose(found: list[_Variant], url: str, prefer: Sequence[str] = ()) -> tuple[str | None, tuple[str, ...]]:
    if not found:
        return None, ()
    wanted = {str(token).strip().casefold() for token in prefer if str(token or "").strip()}
    sized = [item for item in found if item.sizes]
    pool = sized or found
    best = max(pool, key=lambda item: _score(item, url, wanted))
    color = best.color
    if not color:
        related = [
            item for item in found
            if item.color and set(item.ids) & set(best.ids)
        ]
        if related:
            color = max(related, key=lambda item: _score(item, url, wanted)).color
        else:
            colored = [item for item in found if item.color]
            if len(colored) == 1:
                color = colored[0].color
            elif colored:
                color = max(colored, key=lambda item: _score(item, url, wanted)).color
    return color, order_sizes(best.sizes)


def _score(item: _Variant, url: str, wanted: set[str] | None = None) -> int:
    score = len(item.sizes)
    if wanted:
        if item.color and item.color.strip().casefold() in wanted:
            score += 400
        if any(ident.strip().casefold() in wanted for ident in item.ids if ident):
            score += 400
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
    # The colour the link names, wherever the store keeps it (/37066365/75/00, _DK.BRW.html, ?cS=002).
    from core.dedup import variant_parts

    _product, color = variant_parts(url)
    if color:
        found.add(color)
        found.add(color.upper())
    return found


def _consume(node: object, parent_key: str, found: list[_Variant]) -> None:
    if isinstance(node, list):
        for item in node:
            _consume(item, parent_key, found)
        return
    if not isinstance(node, dict):
        return

    variants = node.get("hasVariant") or (
        node.get("variants") if isinstance(node.get("variants"), list) else None
    )
    if isinstance(variants, list):
        _add_grouped_variants(variants, found)

    labels = _sizes_on(node)
    color = _color_from(node, parent_key)
    if labels or color:
        found.append(_Variant(color, tuple(labels), _ids_on(node, parent_key), _is_selected(node)))

    for key, value in node.items():
        if key in ("hasVariant", "variants"):
            continue
        _consume(value, str(key), found)


def _variant_size_labels(item: dict) -> list[str]:
    for key in ("public_title", "title", "option1", "option2", "option3"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            for part in re.split(r"[/|]", val):
                cleaned = clean_size(part.strip())
                if cleaned:
                    return [cleaned]
    options = item.get("options")
    if isinstance(options, list):
        for opt in options:
            if isinstance(opt, str):
                for part in re.split(r"[/|]", opt):
                    cleaned = clean_size(part.strip())
                    if cleaned:
                        return [cleaned]
    name = item.get("name")
    if isinstance(name, str) and "-" in name:
        tail = name.rsplit("-", 1)[-1].strip()
        cleaned = clean_size(tail)
        if cleaned:
            return [cleaned]
    sku = item.get("sku")
    if isinstance(sku, str) and "-" in sku:
        tail = sku.rsplit("-", 1)[-1].strip()
        cleaned = clean_size(tail)
        if cleaned:
            return [cleaned]
    return []


def _variant_color(item: dict) -> str | None:
    for key in ("option1", "option2", "option3", "color", "colour"):
        val = item.get(key)
        if isinstance(val, str):
            text = _color_text(val)
            if text:
                return text
    options = item.get("options")
    if isinstance(options, list):
        for opt in options:
            if isinstance(opt, str):
                text = _color_text(opt)
                if text:
                    return text
    return None


def _add_grouped_variants(variants: list, found: list[_Variant]) -> None:
    grouped: dict[str, list[str]] = {}
    ids: dict[str, list[str]] = {}
    selected: set[str] = set()
    for item in variants:
        if not isinstance(item, dict):
            continue
        color = _color_from(item, "hasVariant") or _variant_color(item) or ""
        labels = _labels_from_size_value(item.get("size"))
        if not labels:
            labels = _sizes_on(item)
        if not labels:
            labels = _variant_size_labels(item)
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


# Stores carry the shade twice: the name the shopper reads and the hex the
# swatch is painted with. «Цвет: ffffff» is nothing anyone shops for.
_HEX_COLOR = re.compile(r"^#?(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)


def _plausible_color(text: str) -> bool:
    if not text or len(text) > 40:
        return False
    if not re.search(r"[A-Za-zА-Яа-яЁё]", text):
        return False
    if _HEX_COLOR.match(text):
        return False
    if _REFERENCE_CODE.search(text):
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
        yield from _iter_json_in_text(text, limit=8)


def _iter_json_in_text(text: str, limit: int):
    yield from iter_json_values(text, limit=limit)


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


_DOM_SIZE_INPUT = re.compile(
    r"<input\b[^>]*name=[\"'][^\"']*(?:size|talla|taille|taglia|größe|grosse|rozmiar)[^\"']*[\"'][^>]*value=[\"']([^\"']{1,16})[\"']",
    re.IGNORECASE,
)
_DOM_SIZE_INPUT_ALT = re.compile(
    r"<input\b[^>]*value=[\"']([^\"']{1,16})[\"'][^>]*name=[\"'][^\"']*(?:size|talla|taille|taglia|größe|grosse|rozmiar)[^\"']*[\"']",
    re.IGNORECASE,
)


def _sizes_from_dom(html_text: str) -> tuple[str, ...]:
    labels: list[str] = []
    for match in _DOM_SIZE.finditer(html_text):
        attrs = match.group(1).lower()
        if not any(token in attrs for token in ("size", "talla", "taille", "taglia", "größe", "grosse", "rozmiar")):
            continue
        label = clean_size(match.group(2))
        if label and label not in labels:
            labels.append(label)
    for pattern in (_DOM_SIZE_INPUT, _DOM_SIZE_INPUT_ALT):
        for match in pattern.finditer(html_text):
            label = clean_size(match.group(1))
            if label and label not in labels:
                labels.append(label)
    ordered = tuple(order_sizes(labels))
    if _is_measurement_list(list(ordered)):
        return ()
    return ordered


def _color_from_dom(html_text: str) -> str | None:
    for pattern in _DOM_COLOR:
        # The wrapper around the name often matches first and holds nothing but
        # whitespace, so an empty hit must not end the search.
        for match in pattern.finditer(html_text):
            text = _color_text(match.group(1))
            if text:
                return text
    for match in _DOM_COLOR_NAMED.finditer(html_text):
        window = _HIDDEN_LABEL.sub(" ", html_text[match.end() : match.end() + 400])
        text = _color_text(html_lib.unescape(window.split("<", 1)[0]))
        if text:
            return text
    return None
