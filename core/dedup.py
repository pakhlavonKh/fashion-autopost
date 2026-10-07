from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse
from adapters.base import RawProduct

if TYPE_CHECKING:
    from storage.repository import ProductRepository

logger = logging.getLogger(__name__)


# The colourway rides in the query: Zara ?v1=, Mango ?c=, Pull&Bear ?cS=,
# Bershka, Lefties and the other Inditex chains ?colorId=, Salesforce stores
# ?dwvar_<product>_color=, Shopify ?variant=. Tracking tags are still dropped.
_COLOR_QUERY_KEYS = {
    "v1", "c", "cs", "colorid", "colourid", "color_id", "colour_id",
    "color", "colour", "colorcode", "colourcode", "variant",
}
_SFCC_COLOR_KEY = re.compile(r"^dwvar_.+_colou?r$", re.IGNORECASE)
_MANGO_COLOR_IN_PATH = re.compile(r"/(\d{7,10})/([0-9a-z]{2,3})(?:/|$)", re.IGNORECASE)
# Salesforce stores name the colour after the product id: CK1-61720277_DK.BRW.html.
_SFCC_COLOR_IN_PATH = re.compile(r"[-_/]([A-Za-z]{0,3}\d{6,10})_([A-Za-z0-9][A-Za-z0-9.\-]{0,15})\.html?$", re.IGNORECASE)
_ZARA_PRODUCT_IN_PATH = re.compile(r"-p(\d{6,})(?:\.html|[/?#]|$)", re.IGNORECASE)
_LAST_SEGMENT_ID = re.compile(r"(?:^|[-_./a-z])(\d{6,12})(?=\.html?$|$|[-_/])", re.IGNORECASE)


def is_color_key(key: str) -> bool:
    """A query parameter that picks the colourway on some store."""
    lowered = (key or "").lower()
    return lowered in _COLOR_QUERY_KEYS or bool(_SFCC_COLOR_KEY.match(lowered))


def normalize_url(url: str | None) -> str:
    """Canonical product URL: no fragment, no tracking, colour query kept.

    Two colours of one model differ only by the colour parameter (?v1=, ?c=,
    ?cS=, ?colorId=, ...). That parameter stays, so the links are not treated
    as the same post. utm and similar tags go.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        kept = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if is_color_key(key) and value
        ]
        path = parsed.path.rstrip("/")
        clean = f"{parsed.scheme}://{parsed.netloc}{path}".lower()
        if kept:
            clean += "?" + urlencode(kept).lower()
        return clean
    except Exception:
        return url.split("#")[0].rstrip("/").lower()


def variant_parts(url: str | None, keep_case: bool = False) -> tuple[str, str]:
    """(product id, colour token). Colour is empty when the link does not name one.

    Mango keeps the colour in the path (/37016751/99) or in ?c=, Salesforce
    stores after the id (_DK.BRW.html), Zara in ?v1=, Pull&Bear in ?cS=, the
    other Inditex chains in ?colorId=. The token is lower-cased for comparing;
    keep_case returns it as the store wrote it, for building links.
    """
    if not url:
        return "", ""

    def token(value: str) -> str:
        return value.strip() if keep_case else value.strip().lower()

    parsed = urlparse(url)
    path = parsed.path or ""
    mango = _MANGO_COLOR_IN_PATH.search(path)
    if mango:
        return mango.group(1), token(mango.group(2))
    color = ""
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if is_color_key(key) and value.strip():
            color = token(value)
            break
    sfcc = _SFCC_COLOR_IN_PATH.search(path)
    if sfcc:
        digits = re.sub(r"\D", "", sfcc.group(1))
        return digits, color or token(sfcc.group(2))
    product_id = ""
    zara = _ZARA_PRODUCT_IN_PATH.search(path)
    if zara:
        product_id = zara.group(1)
    else:
        skus = extract_skus(url, None)
        product_id = max(skus, key=len) if skus else ""
    if not product_id:
        last = path.rstrip("/").rsplit("/", 1)[-1]
        found = _LAST_SEGMENT_ID.findall(last)
        product_id = found[-1] if found else ""
    return product_id, color


def extract_skus(url: str | None, external_id: str | None = None) -> set[str]:
    """Extract numeric/alphanumeric SKUs from URL path or external ID."""
    skus: set[str] = set()
    if external_id:
        m = re.search(r"(?:^|[-_:])(\d{6,10})(?:$|[-_:])", external_id)
        if m:
            skus.add(m.group(1))

    if url:
        # Pattern 1: Mango style (/37016751/)
        m1 = re.search(r"/(\d{7,10})(?:/|$)", url)
        if m1:
            skus.add(m1.group(1))

        # Pattern 2: Zara style (-p01234567.html or p01234567)
        m2 = re.search(r"-p([0-9A-Za-z]{6,})\.html", url) or re.search(r"p(\d{6,})", url)
        if m2:
            skus.add(m2.group(1))

        # Pattern 3: H&M style (productpage.1309848015.html)
        m_hm = re.search(r"productpage\.(\d{6,10})", url)
        if m_hm:
            skus.add(m_hm.group(1))

        # Pattern 4: Any generic 6-10 digits in URL path
        m3 = re.search(r"[-_/\.](\d{6,10})", url)
        if m3:
            skus.add(m3.group(1))

    return skus


def extract_duplicate_signatures(
    source: str = "",
    external_id: str = "",
    product_url: str | None = None,
    title: str | None = None,
) -> set[str]:
    """Generate all canonical matching keys for a product.

    A candidate matches if ANY of its keys matches an existing published key.
    Keys include:
    - ext:{normalized_external_id}
    - url:{canonical_url} (query parameters & trailing slashes stripped)
    - sku:{source}:{extracted_sku} (e.g. sku:mango:37016751)
    """
    sigs: set[str] = set()
    clean_src = (source or "").strip().lower()

    if external_id:
        clean_ext = external_id.strip().lower()
        sigs.add(f"ext:{clean_ext}")

    if product_url:
        canon_url = normalize_url(product_url)
        if canon_url:
            sigs.add(f"url:{canon_url}")

    product_id, color = variant_parts(product_url)
    # A colour token makes this one colourway. The bare article number is not
    # added in that case, or black and beige of the same model collide.
    if product_id and color:
        qualified = f"{product_id}:{color}"
        sigs.add(f"sku:{clean_src}:{qualified}" if clean_src else f"sku:{qualified}")
    else:
        for sku in extract_skus(product_url, external_id):
            if sku:
                sigs.add(f"sku:{clean_src}:{sku}" if clean_src else f"sku:{sku}")

    return sigs


def filter_unseen(products: list[RawProduct], repo: ProductRepository) -> list[RawProduct]:
    """Drop any product that matches a published product or appears multiple times in the batch.

    Employs triple-layer deduplication:
    1. Deterministic external_id match against published records.
    2. Canonical URL match against published records.
    3. Brand SKU match against published records (e.g. Mango 8-digit product code).
    4. In-batch duplicate elimination.
    """
    # 1. Fetch published duplicate signatures from repository
    if hasattr(repo, "get_published_signatures"):
        published_signatures = repo.get_published_signatures()
    else:
        published_signatures = {f"ext:{pid.strip().lower()}" for pid in repo.get_published_ids()}

    # Also add raw published external_ids as defensive fallback
    for pid in repo.get_published_ids():
        published_signatures.add(f"ext:{pid.strip().lower()}")

    seen_batch_signatures: set[str] = set()
    unseen: list[RawProduct] = []

    for product in products:
        prod_sigs = extract_duplicate_signatures(
            source=product.source,
            external_id=product.external_id,
            product_url=product.product_url,
            title=product.title,
        )

        # Layer 1-3: Check against previously published products
        overlap = prod_sigs & published_signatures
        if overlap:
            logger.debug(
                "Discarding previously published product: external_id=%s, url=%s, matched_signatures=%s",
                product.external_id,
                product.product_url,
                overlap,
            )
            continue

        # Layer 4: Check duplicate within the current candidate batch
        batch_overlap = prod_sigs & seen_batch_signatures
        if batch_overlap:
            logger.debug(
                "Discarding in-batch duplicate product: external_id=%s, url=%s, matched_signatures=%s",
                product.external_id,
                product.product_url,
                batch_overlap,
            )
            continue

        seen_batch_signatures.update(prod_sigs)
        unseen.append(product)

    logger.info(
        "filter_unseen: %d candidates in, %d discarded as duplicates, %d remaining",
        len(products),
        len(products) - len(unseen),
        len(unseen),
    )
    return unseen
