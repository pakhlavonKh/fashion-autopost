"""Base scraper protocol and shared utility functions for Playwright scrapers."""

from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

CURRENCY_SYMBOL_MAP = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "₺": "TRY",
    "TL": "TRY",
    "CHF": "CHF",
    "CAD": "CAD",
    "AUD": "AUD",
}


def parse_price(text: str, default_currency: str = "EUR") -> tuple[Decimal, str]:
    """Extract decimal price and currency from formatted price string.
    
    Examples:
        "89,90 €" -> (Decimal('89.90'), 'EUR')
        "$129.00" -> (Decimal('129.00'), 'USD')
        "149.99 EUR" -> (Decimal('149.99'), 'EUR')
        "£59.99" -> (Decimal('59.99'), 'GBP')
        "49,95" -> (Decimal('49.95'), default_currency)
    """
    if not text:
        raise ValueError("Empty price text")

    clean_text = text.strip()
    detected_currency = default_currency

    for sym, code in CURRENCY_SYMBOL_MAP.items():
        if sym in clean_text:
            detected_currency = code
            clean_text = clean_text.replace(sym, "")
            break
    else:
        for code in ["EUR", "USD", "GBP", "TRY", "CHF", "CAD", "AUD"]:
            if code in clean_text.upper():
                detected_currency = code
                clean_text = re.sub(code, "", clean_text, flags=re.IGNORECASE)
                break

    clean_text = clean_text.strip()
    match = re.search(r"\d[\d\s.,]*\d|\d+", clean_text)
    if not match:
        raise ValueError(f"Could not parse numerical price from: '{text}'")

    num_str = match.group(0).strip().replace(" ", "")

    if "," in num_str and "." in num_str:
        if num_str.rfind(",") > num_str.rfind("."):
            num_str = num_str.replace(".", "").replace(",", ".")
        else:
            num_str = num_str.replace(",", "")
    elif "," in num_str:
        num_str = num_str.replace(",", ".")

    try:
        price = Decimal(num_str).quantize(Decimal("0.01"))
        return price, detected_currency
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value '{num_str}' from '{text}': {exc}") from exc


def extract_best_image_url(img_src: str | None, srcset: str | None = None, base_url: str = "") -> str:
    """Choose best high-resolution image URL from src or srcset."""
    if srcset and srcset.strip():
        # srcset format: "url1 400w, url2 800w, url3 1200w"
        parts = [p.strip() for p in srcset.split(",") if p.strip()]
        candidates = []
        for part in parts:
            tokens = part.split()
            if tokens:
                candidates.append(tokens[0])
        if candidates:
            selected = candidates[-1]
            if selected.startswith("//"):
                return f"https:{selected}"
            elif base_url and not selected.startswith(("http://", "https://")):
                return urljoin(base_url, selected)
            return selected

    if not img_src or not img_src.strip():
        return ""

    selected = img_src.strip()
    if selected.startswith("//"):
        selected = f"https:{selected}"
    elif base_url and not selected.startswith(("http://", "https://")):
        selected = urljoin(base_url, selected)

    return selected


def dismiss_cookie_banner(page: Any, custom_button: str | None = None, timeout_ms: int = 2500) -> bool:
    """Attempt to dismiss cookie consent dialogs."""
    selectors = []
    if custom_button:
        selectors.append(custom_button)

    selectors.extend([
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        "button[id*='onetrust-accept']",
        "button:has-text('Accept all')",
        "button:has-text('Aceptar todas')",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "button:has-text('Aceptar')",
        "button:has-text('Allow all')",
        "button:has-text('I agree')",
        "button:has-text('Consent')",
        "button[data-testid*='cookie-accept']",
        "#cookie-accept",
        ".cookie-accept",
    ])

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=timeout_ms):
                btn.click()
                logger.debug("Dismissed cookie consent banner with selector: %s", sel)
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue

    return False


def scroll_page_down(page: Any, steps: int = 3, wait_ms: int = 1200) -> None:
    """Scroll down incrementally to trigger lazy loading."""
    for step in range(steps):
        try:
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            page.wait_for_timeout(wait_ms)
        except Exception as exc:
            logger.debug("Error during page scroll step %d: %s", step, exc)
            break


def generate_deterministic_id(brand: str, raw_id: str | None = None, url: str = "") -> str:
    """Generate a stable, deterministic external_id for a product across process runs.
    
    Never uses Python's randomized hash() function.
    """
    import hashlib

    clean_brand = brand.strip().lower()
    if raw_id:
        clean_raw = str(raw_id).strip()
        if clean_raw.lower().startswith(f"{clean_brand}-"):
            return clean_raw
        return f"{clean_brand}-{clean_raw}"

    if url:
        # Pattern 1: Mango style (/37016751/)
        match = re.search(r"/(\d{7,10})(?:/|$)", url)
        if match:
            return f"{clean_brand}-{match.group(1)}"
        # Pattern 2: Zara style (-p01234567.html or p12345)
        match = re.search(r"-p([0-9A-Za-z]+)\.html", url) or re.search(r"p(\d{5,})", url)
        if match:
            return f"{clean_brand}-{match.group(1)}"
        # Pattern 3: Any 6+ digits in path
        match = re.search(r"[-_/](\d{6,})", url)
        if match:
            return f"{clean_brand}-{match.group(1)}"

        # Deterministic SHA-256 fallback from canonical URL
        canon = url.split("?")[0].rstrip("/").lower()
        sha = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:10]
        return f"{clean_brand}-{sha}"

    return f"{clean_brand}-item"


@runtime_checkable
class StoreScraper(Protocol):
    """Protocol for store-specific scrapers."""

    def scrape(
        self,
        page: Any,
        url: str,
        max_items: int = 10,
        default_currency: str = "EUR",
    ) -> list[dict[str, Any]]:
        """Scrape product listings from page into raw dictionaries."""
        ...
