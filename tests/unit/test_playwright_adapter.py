"""Unit tests for PlaywrightScraperAdapter.

Tests normalization, protocol adherence, error isolation, and filtering.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from adapters.base import RawProduct, SourceAdapter
from adapters.playwright_adapter import PlaywrightScraperAdapter
from config.app_config import ScraperSettings, ScraperStoreConfig


def test_playwright_adapter_protocol_conformance() -> None:
    """PlaywrightScraperAdapter implements SourceAdapter protocol."""
    adapter = PlaywrightScraperAdapter()
    assert isinstance(adapter, SourceAdapter)


def test_normalize_valid_item() -> None:
    """Normalize well-formed scraped dict into RawProduct DTO."""
    adapter = PlaywrightScraperAdapter()
    raw = {
        "id": "zara-12345",
        "brand": "zara",
        "name": "Silk Floral Blouse",
        "price": Decimal("79.95"),
        "currency": "EUR",
        "image": "https://static.zara.net/blouse.jpg",
        "url": "https://www.zara.com/sample",
        "available": True,
    }

    product = adapter._normalize_item(raw)
    assert isinstance(product, RawProduct)
    assert product.external_id == "zara-12345"
    assert product.source == "zara"
    assert product.title == "Silk Floral Blouse"
    assert product.price == Decimal("79.95")
    assert product.currency == "EUR"
    assert product.photo_url == "https://static.zara.net/blouse.jpg"
    assert product.product_url == "https://www.zara.com/sample"
    assert product.in_stock is True


def test_normalize_missing_fields_validation() -> None:
    """Validation errors on missing mandatory product attributes."""
    adapter = PlaywrightScraperAdapter()

    # Missing ID
    with pytest.raises(ValueError, match="missing ID/SKU"):
        adapter._normalize_item({"name": "Coat", "price": 100, "image": "http://img"})

    # Missing Title
    with pytest.raises(ValueError, match="missing title/name"):
        adapter._normalize_item({"id": "1", "name": "", "price": 100, "image": "http://img"})

    # Missing Price
    with pytest.raises(ValueError, match="missing price"):
        adapter._normalize_item({"id": "1", "name": "Coat", "image": "http://img"})

    # Missing Image
    with pytest.raises(ValueError, match="missing image URL"):
        adapter._normalize_item({"id": "1", "name": "Coat", "price": 100, "image": ""})


def test_store_error_isolation() -> None:
    """If one store fails during scraping, the adapter continues with other stores."""
    config = ScraperSettings(
        stores={
            "failing_store": ScraperStoreConfig(url="https://failing.example.com", enabled=True),
            "working_store": ScraperStoreConfig(url="https://working.example.com", enabled=True),
        }
    )

    adapter = PlaywrightScraperAdapter(
        config=config,
        selected_stores=["failing_store", "working_store"],
    )

    def mock_scrape_single(context, store_name, store_cfg):
        if store_name == "failing_store":
            raise RuntimeError("Anti-bot block or network timeout")
        return [
            {
                "id": "work-1",
                "brand": "working",
                "name": "Working Item",
                "price": Decimal("50.00"),
                "currency": "EUR",
                "image": "https://example.com/item.jpg",
                "url": "https://example.com/p1",
                "available": True,
            }
        ]

    # Patch browser and _scrape_single_store
    with patch("adapters.playwright_adapter.sync_playwright") as mock_pw:
        mock_p = MagicMock()
        mock_pw.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_p.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context

        with patch.object(adapter, "_scrape_single_store", side_effect=mock_scrape_single):
            products = adapter.fetch_products()

    assert len(products) == 1
    assert products[0].external_id == "work-1"
    assert products[0].title == "Working Item"
