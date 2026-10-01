"""Unpublished products can be removed from the database; published ones stay."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from fastapi.testclient import TestClient

from config.app_config import AppConfig
from dashboard.server import create_dashboard_app
from storage.models import ProductRecord
from storage.repository import SqlAlchemyProductRepository


def _client(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"dashboard": {"admin_key": "secret-key"}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'delete.db'}")
    app = create_dashboard_app(config=config, runner=MagicMock(), repo=repo)
    client = TestClient(app)
    client.headers["X-Admin-Key"] = config.dashboard.admin_key
    return client, repo


def _add(repo: SqlAlchemyProductRepository, external_id: str, status: str, telegram_post_id: str | None = None) -> None:
    with repo._get_session() as session:
        session.add(
            ProductRecord(
                external_id=external_id,
                source="mango",
                title=external_id,
                price_original=Decimal("29.99"),
                currency_original="EUR",
                photo_url="https://example.com/photo.jpg",
                status=status,
                telegram_post_id=telegram_post_id,
            )
        )
        session.commit()


def test_delete_unpublished_product(tmp_path: Path):
    client, repo = _client(tmp_path)
    _add(repo, "mango-new-1", "new")

    res = client.delete("/api/products/mango-new-1")
    assert res.status_code == 200
    assert repo.get_by_external_id("mango-new-1") is None


def test_delete_refuses_published_product(tmp_path: Path):
    client, repo = _client(tmp_path)
    _add(repo, "mango-live-1", "published", telegram_post_id="182")

    res = client.delete("/api/products/mango-live-1")
    assert res.status_code == 409
    assert repo.get_by_external_id("mango-live-1") is not None


def test_delete_refuses_failed_product_that_already_posted(tmp_path: Path):
    client, repo = _client(tmp_path)
    _add(repo, "mango-partial-1", "failed", telegram_post_id="159")

    res = client.delete("/api/products/mango-partial-1")
    assert res.status_code == 409
    assert repo.get_by_external_id("mango-partial-1") is not None


def test_clearing_the_catalog_removes_published_rows_too(tmp_path: Path):
    """Starting the catalogue over has to wipe everything the single delete protects."""
    client, repo = _client(tmp_path)
    _add(repo, "mango-live-1", "published", telegram_post_id="182")
    _add(repo, "mango-partial-1", "failed", telegram_post_id="159")
    _add(repo, "mango-new-1", "new")
    repo.log_event("ERROR", "publish", "instagram failed", external_id="mango-partial-1")

    res = client.delete("/api/products")

    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == 3
    assert "3" in body["message"]
    assert repo.get_by_external_id("mango-live-1") is None
    assert repo.get_by_external_id("mango-new-1") is None
    assert repo.get_published_signatures() == set()


def test_clearing_the_catalog_needs_admin_credentials(tmp_path: Path):
    client, repo = _client(tmp_path)
    _add(repo, "mango-live-1", "published", telegram_post_id="182")
    del client.headers["X-Admin-Key"]

    assert client.delete("/api/products").status_code == 401
    assert repo.get_by_external_id("mango-live-1") is not None
