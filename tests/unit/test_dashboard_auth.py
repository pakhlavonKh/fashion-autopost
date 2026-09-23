"""Unit tests for Dashboard security, authentication gate, and protected APIs."""

from pathlib import Path
from unittest.mock import MagicMock
import yaml
from fastapi.testclient import TestClient

from config.app_config import AppConfig
from dashboard.server import create_dashboard_app
from storage.repository import SqlAlchemyProductRepository


def test_dashboard_public_assets_accessible_without_auth(tmp_path: Path):
    """Static assets (HTML, CSS, JS) must be accessible publicly without credentials."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"dashboard": {"admin_key": "secret-key-123"}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'auth_test.db'}")
    runner = MagicMock()
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    client = TestClient(app)

    # 1. Root index.html
    res_index = client.get("/")
    assert res_index.status_code == 200

    # 2. Direct style.css
    res_css = client.get("/style.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")

    # 3. Direct app.js
    res_js = client.get("/app.js")
    assert res_js.status_code == 200
    assert "javascript" in res_js.headers.get("content-type", "")


def test_dashboard_api_blocks_unauthorized_access(tmp_path: Path):
    """Protected API endpoints must reject requests with missing or invalid keys."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"dashboard": {"admin_key": "my-secret-token"}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'auth_test2.db'}")
    runner = MagicMock()
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    client = TestClient(app)

    # Missing credentials -> 401
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/products").status_code == 401
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/auth/verify").status_code == 401

    # Wrong credentials -> 401
    assert client.get("/api/stats", headers={"X-Admin-Key": "wrong-key"}).status_code == 401
    assert client.get("/api/stats", headers={"Authorization": "Bearer wrong-key"}).status_code == 401
    assert client.get("/api/stats?key=wrong-key").status_code == 401


def test_dashboard_api_allows_authorized_access(tmp_path: Path):
    """Protected API endpoints must allow access with valid bearer token, X-Admin-Key, or query param."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"schedule": {"times": ["10:00"]}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    key = config.dashboard.admin_key
    assert key

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'auth_test3.db'}")
    runner = MagicMock()
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    client = TestClient(app)

    # 1. Via X-Admin-Key
    res1 = client.get("/api/stats", headers={"X-Admin-Key": key})
    assert res1.status_code == 200
    assert res1.json()["status"] == "online"

    # 2. Via Authorization: Bearer
    res2 = client.get("/api/stats", headers={"Authorization": f"Bearer {key}"})
    assert res2.status_code == 200

    # 3. Via query parameter key=
    res3 = client.get(f"/api/stats?key={key}")
    assert res3.status_code == 200

    # 4. Auth verify endpoint
    res4 = client.get("/api/auth/verify", headers={"X-Admin-Key": key})
    assert res4.status_code == 200
    assert res4.json()["authenticated"] is True


def test_dashboard_auth_login_endpoint(tmp_path: Path):
    """The /api/auth/login endpoint validates admin credentials and returns tokens."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"schedule": {"times": ["10:00"]}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    key = config.dashboard.admin_key
    assert key

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'auth_test4.db'}")
    runner = MagicMock()
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    client = TestClient(app)

    # Incorrect key
    bad_res = client.post("/api/auth/login", json={"key": "bad-password"})
    assert bad_res.status_code == 401

    # Correct key via 'key' field
    ok_res = client.post("/api/auth/login", json={"key": key})
    assert ok_res.status_code == 200
    data = ok_res.json()
    assert data["authenticated"] is True
    assert data["token"] == key

    # Correct key via 'password' field
    ok_res2 = client.post("/api/auth/login", json={"password": key})
    assert ok_res2.status_code == 200
    assert ok_res2.json()["authenticated"] is True
