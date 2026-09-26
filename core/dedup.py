"""Deduplication service.

Per SDD §3.2 and SRS FR-1.4 & FR-6.2.
Filters candidate products against previously published items stored in the repository
using multi-layer deterministic signatures (external_id, canonical URL, and brand SKU),
and prevents duplicate items within the current candidate batch.
"""

import logging
import re
from urllib.parse import urlparse
from adapters.base import RawProduct
from storage.repository import ProductRepository

logger = logging.getLogger(__name__)


def normalize_url(url: str | None) -> str:
    """Normalize a product URL by removing query strings, fragments, and trailing slashes."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()
        return clean
    except Exception:
        return url.split("?")[0].rstrip("/").lower()


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

        # Pattern 3: Any generic 6-10 digits in URL path
        m3 = re.search(r"[-_/](\d{6,10})", url)
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
