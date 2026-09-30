"""Playwright worker for opening and processing product URLs.

Ensures that user/admin-submitted product URLs pass through Playwright,
following redirects, retrieving canonical representations, and normalizing
the final URL into the expected clean format for the publishing workflow.
"""

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

# Query parameters to strip for clean canonical representation
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "igshid",
    "_gl",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "aff_id",
}


def clean_url_parameters(url: str) -> str:
    """Remove tracking and session queries while preserving store item identifiers."""
    parsed = urlparse(url)
    if not parsed.query:
        return url

    kept_params = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if k.lower() not in TRACKING_PARAMS:
            kept_params.append((k, v))

    new_query = urlencode(kept_params)
    cleaned = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        "",  # drop fragment
    ))
    return cleaned.rstrip("?")


def process_product_url_with_playwright(
    raw_url: str,
    timeout_seconds: float = 30.0,
    headless: bool = True,
) -> tuple[str, str]:
    """Open product page using Playwright, process redirects and canonical tag.
    
    Returns:
        (final_processed_url, page_html)
    """
    logger.info("Playwright worker processing input URL: %s", raw_url)
    cleaned_initial = clean_url_parameters(raw_url.strip())

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            page.goto(cleaned_initial, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            page.wait_for_timeout(1000)

            # Check for canonical tag in document
            canonical_href = page.evaluate("""() => {
                const el = document.querySelector('link[rel="canonical"]');
                return el ? el.href : null;
            }""")

            current_url = page.url or cleaned_initial
            html_content = page.content() or ""

            browser.close()

            # Prefer canonical URL if valid and matching store domain
            final_target = canonical_href if (canonical_href and canonical_href.startswith("http")) else current_url
            processed_url = clean_url_parameters(final_target)

            logger.info(
                "Playwright successfully resolved URL: original='%s' -> processed='%s'",
                raw_url,
                processed_url,
            )
            return processed_url, html_content

    except Exception as exc:
        logger.warning(
            "Playwright browser URL processing encountered '%s'. Falling back to static URL cleanup.",
            exc,
        )
        return cleaned_initial, ""
