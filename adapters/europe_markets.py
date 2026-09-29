"""European storefronts for fashion brands.

Playwright visits European catalogs only and skips Turkey, the US, and other markets.
"""

from pathlib import Path
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Country codes used by Zara, Mango, Stradivarius and other EU storefronts.
# Turkey and other non-European shops are intentionally absent.
EUROPEAN_MARKET_CODES = frozenset({
    "at", "be", "bg", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr",
    "gb", "gr", "hr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mt", "nl",
    "no", "pl", "pt", "ro", "se", "si", "sk", "uk",
})

NON_EUROPEAN_MARKET_CODES = frozenset({
    "ae", "ar", "au", "br", "ca", "cl", "cn", "co", "eg", "hk", "id", "il",
    "in", "jp", "kr", "kw", "kz", "ma", "mx", "my", "nz", "om", "pe", "ph",
    "qa", "ru", "sa", "sg", "th", "tr", "tw", "ua", "us", "za", "ww",
})

REGION_INDEX_PATH = Path("data/europe_region_index.txt")

_KNOWN_MARKET_CODES = EUROPEAN_MARKET_CODES | NON_EUROPEAN_MARKET_CODES
_LOCALE_TOKEN = re.compile(r"^([a-z]{2})[_-]([a-z]{2})$")


def _code_from_token(token: str) -> str | None:
    """Read a country code from 'es', 'es_es', or 'en_gb'."""
    token = token.lower()
    if token in _KNOWN_MARKET_CODES:
        return token
    match = _LOCALE_TOKEN.match(token)
    if not match:
        return None
    left, right = match.group(1), match.group(2)
    if left in _KNOWN_MARKET_CODES:
        return left
    if right in _KNOWN_MARKET_CODES:
        return right
    return None


def market_code_from_url(url: str) -> str | None:
    """Return the storefront country code from the host or the start of the path."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if labels:
        code = _code_from_token(labels[-1])
        if code:
            return code
    parts = [part for part in parsed.path.split("/") if part]
    for part in parts[:4]:
        code = _code_from_token(part)
        if code:
            return code
    return None


def is_european_store_url(url: str) -> bool:
    """True only when the URL points at a European storefront."""
    code = market_code_from_url(url)
    return code in EUROPEAN_MARKET_CODES


def currency_for_market(code: str | None) -> str:
    """Sale currency of the European storefront. UK uses pounds, the rest euros."""
    if code in {"gb", "uk"}:
        return "GBP"
    return "EUR"


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
