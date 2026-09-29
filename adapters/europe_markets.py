"""European storefronts for Zara and Mango.

Playwright visits one market per scrape and skips every other country.
"""

from pathlib import Path
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# First path segment on zara.com and shop.mango.com. Turkey and other
# non-European shops are intentionally absent.
EUROPEAN_MARKET_CODES = frozenset({
    "at", "be", "bg", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr",
    "gb", "gr", "hr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mt", "nl",
    "no", "pl", "pt", "ro", "se", "si", "sk", "uk",
})

REGION_INDEX_PATH = Path("data/europe_region_index.txt")


def market_code_from_url(url: str) -> str | None:
    """Return the storefront country code for a Zara or Mango URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "zara.com" not in host and "mango.com" not in host:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    code = parts[0].lower()
    if len(code) == 2 and code.isalpha():
        return code
    return None


def is_european_store_url(url: str) -> bool:
    """Allow unknown sites. Zara and Mango must use a European storefront."""
    code = market_code_from_url(url)
    if code is None:
        return True
    return code in EUROPEAN_MARKET_CODES


def same_market(url: str, other_url: str) -> bool:
    """True when both URLs are the same Zara/Mango country, or either is unknown."""
    left = market_code_from_url(url)
    right = market_code_from_url(other_url)
    if left is None or right is None:
        return True
    return left == right


def next_region_index(region_count: int, index_path: Path | None = None) -> int:
    """Read, advance, and persist the round-robin index. One country per scrape."""
    if index_path is None:
        index_path = REGION_INDEX_PATH
    if region_count <= 0:
        return 0
    current = 0
    try:
        if index_path.exists():
            current = int(index_path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError) as exc:
        logger.debug("Could not read region index %s: %s", index_path, exc)
        current = 0
    current %= region_count
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(str((current + 1) % region_count), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist region index %s: %s", index_path, exc)
    return current
