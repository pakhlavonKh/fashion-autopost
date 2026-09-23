"""Unit test for admin adding new websites dynamically via config and dashboard API.

Verifies:
1. Adding a new store to config.yaml and calling reload_hot_fields() updates the scraper.
2. Dashboard REST endpoints for listing, creating, and deleting scraping targets.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
import tempfile
import yaml
from fastapi.testclient import TestClient

from adapters.playwright_adapter import PlaywrightScraperAdapter
from config.app_config import AppConfig
from dashboard.server import create_dashboard_app
from storage.repository import SqlAlchemyProductRepository


def test_dynamic_store_hot_reload_in_adapter() -> None:
    """When an admin adds a new store to config.yaml, the adapter discovers it on next cycle."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(
            {
                "scraper": {
                    "stores": {
                        "zara": {"url": "https://www.zara.com", "enabled": True},
                    }
                }
            },
            f,
        )
        cfg_path = Path(f.name)

    try:
        config = AppConfig.load(config_path=cfg_path, env_path="non_existent.env")
        adapter = PlaywrightScraperAdapter(app_config=config)

        # Initially 1 store
        assert "zara" in adapter.current_config.stores
        assert "boutique" not in adapter.current_config.stores

        # Admin adds 'boutique' to config.yaml
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "scraper": {
                        "stores": {
                            "zara": {"url": "https://www.zara.com", "enabled": True},
                            "boutique": {
                                "url": "https://boutique.example.com",
                                "currency": "USD",
                                "enabled": True,
                            },
                        }
                    }
                },
                f,
            )

        # Trigger hot reload (as occurs at start of cycle or via dashboard)
        config.reload_hot_fields()

        # Adapter now discovers the new store dynamically without code changes
        assert "boutique" in adapter.current_config.stores
        assert adapter.current_config.stores["boutique"].url == "https://boutique.example.com"
        assert adapter.current_config.stores["boutique"].currency == "USD"
    finally:
        if cfg_path.exists():
            cfg_path.unlink()


def test_dashboard_scraper_stores_api(tmp_path: Path) -> None:
    """Admin uses Dashboard API to list, add, and delete scraping website targets."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        yaml.dump({
            "scraper": {
                "stores": {
                    "zara": {"url": "https://www.zara.com", "enabled": True},
                }
            }
        }),
        encoding="utf-8",
    )

    db_url = f"sqlite:///{tmp_path / 'dash_test.db'}"
    repo = SqlAlchemyProductRepository(db_url)
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    mock_runner = MagicMock()

    app = create_dashboard_app(config=config, runner=mock_runner, repo=repo)
    client = TestClient(app)

    # 1. GET /api/scraper/stores
    res = client.get("/api/scraper/stores")
    assert res.status_code == 200
    stores = res.json()["stores"]
    assert "zara" in stores

    # 2. POST /api/scraper/stores - Admin adds new website
    new_store_payload = {
        "name": "asos",
        "url": "https://www.asos.com/women/new-in",
        "currency": "GBP",
        "max_items": 12,
        "enabled": True,
        "selectors": {
            "item": "article[data-auto-id='productTile']",
            "title": "h2",
            "price": "span[data-auto-id='productTilePrice']",
        },
    }
    res_post = client.post("/api/scraper/stores", json=new_store_payload)
    assert res_post.status_code == 200
    assert res_post.json()["success"] is True

    # Check that config was hot-reloaded
    assert "asos" in config.scraper.stores
    assert config.scraper.stores["asos"].currency == "GBP"
    assert config.scraper.stores["asos"].selectors.item == "article[data-auto-id='productTile']"

    # 3. DELETE /api/scraper/stores/asos
    res_del = client.delete("/api/scraper/stores/asos")
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True
    assert "asos" not in config.scraper.stores
