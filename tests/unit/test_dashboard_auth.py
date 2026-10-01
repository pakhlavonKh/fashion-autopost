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


def test_dashboard_auth_configurable_username_and_password(tmp_path: Path, monkeypatch):
    """Admin username and password can be custom configured via YAML and ENV and validated."""
    import base64
    monkeypatch.delenv("DASHBOARD_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("DASHBOARD_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_ADMIN_KEY", raising=False)

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        yaml.dump({
            "dashboard": {
                "admin_username": "fashion_manager",
                "admin_password": "CustomSecretPass2026!",
            }
        }),
        encoding="utf-8",
    )
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    assert config.dashboard.admin_username == "fashion_manager"
    assert config.dashboard.admin_password == "CustomSecretPass2026!"

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'auth_custom.db'}")
    runner = MagicMock()
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    client = TestClient(app)

    # 1. Reject invalid username
    res_wrong_user = client.post(
        "/api/auth/login",
        json={"username": "wrong_user", "password": "CustomSecretPass2026!"},
    )
    assert res_wrong_user.status_code == 401
    assert "Foydalanuvchi nomi" in res_wrong_user.json()["detail"]

    # 2. Reject invalid password
    res_wrong_pass = client.post(
        "/api/auth/login",
        json={"username": "fashion_manager", "password": "wrong_password"},
    )
    assert res_wrong_pass.status_code == 401
    assert "parol" in res_wrong_pass.json()["detail"].lower()

    # 3. Successful login with matching username and password
    res_ok = client.post(
        "/api/auth/login",
        json={"username": "fashion_manager", "password": "CustomSecretPass2026!"},
    )
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["authenticated"] is True
    assert data["username"] == "fashion_manager"
    assert data["token"] == "CustomSecretPass2026!"

    # 4. Bearer token access with returned token
    res_stats = client.get("/api/stats", headers={"Authorization": f"Bearer {data['token']}"})
    assert res_stats.status_code == 200

    # 5. Basic Auth access
    creds = base64.b64encode(b"fashion_manager:CustomSecretPass2026!").decode()
    res_basic = client.get("/api/stats", headers={"Authorization": f"Basic {creds}"})
    assert res_basic.status_code == 200

    # 6. Verify auth endpoint returns configured username
    res_verify = client.get("/api/auth/verify", headers={"Authorization": f"Bearer {data['token']}"})
    assert res_verify.status_code == 200
    assert res_verify.json()["username"] == "fashion_manager"

    # 7. Test ENV variable override for username and password
    monkeypatch.setenv("DASHBOARD_ADMIN_USERNAME", "env_director")
    monkeypatch.setenv("DASHBOARD_ADMIN_PASSWORD", "EnvDirectorPass999!")
    env_config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    assert env_config.dashboard.admin_username == "env_director"
    assert env_config.dashboard.admin_password == "EnvDirectorPass999!"


def _publish_now_client(tmp_path: Path, name: str) -> tuple[TestClient, MagicMock, str]:
    cfg_file = tmp_path / f"{name}.yaml"
    cfg_file.write_text(yaml.dump({"schedule": {"times": ["10:00"]}}), encoding="utf-8")
    config = AppConfig.load(config_path=cfg_file, env_path="non_existent.env")
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / f'{name}.db'}")
    runner = MagicMock()
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    return TestClient(app), runner, config.dashboard.admin_key


def test_publish_now_needs_admin_credentials(tmp_path: Path):
    """The on-demand publish button must not be reachable without the admin key."""
    client, runner, _key = _publish_now_client(tmp_path, "publish_now_auth")

    assert client.post("/api/publish-now").status_code == 401
    assert client.post("/api/publish-now", headers={"X-Admin-Key": "wrong"}).status_code == 401
    runner.publish_next_eligible_product.assert_not_called()


def test_publish_now_publishes_the_next_queued_product(tmp_path: Path):
    """One post goes out immediately and the catalog is not re-scraped for it."""
    client, runner, key = _publish_now_client(tmp_path, "publish_now_queue")
    runner.publish_next_eligible_product.return_value = (True, "Опубликован следующий подходящий товар: Жакет")

    res = client.post("/api/publish-now", headers={"X-Admin-Key": key})

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["published"] == 1
    assert "Жакет" in body["message"]
    runner.run_cycle.assert_not_called()


def test_publish_now_falls_back_to_a_cycle_when_the_queue_is_empty(tmp_path: Path):
    """An empty queue must still produce a post, the same way the schedule does."""
    from core.pipeline import CycleSummary

    client, runner, key = _publish_now_client(tmp_path, "publish_now_cycle")
    runner.publish_next_eligible_product.return_value = (False, "В базе нет подходящих товаров для публикации.")
    runner.run_cycle.return_value = CycleSummary(fetched=5, unseen=1, selected=1, published=1)

    res = client.post("/api/publish-now", headers={"X-Admin-Key": key})

    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["published"] == 1
    runner.run_cycle.assert_called_once_with()


def test_collect_products_needs_admin_credentials(tmp_path: Path):
    """Collecting hits ten brand sites, so it must not be reachable without the admin key."""
    client, runner, _key = _publish_now_client(tmp_path, "collect_auth")

    assert client.post("/api/collect-products").status_code == 401
    runner.collect_new_products.assert_not_called()


def test_collect_products_reports_what_landed_in_the_queue(tmp_path: Path):
    from core.pipeline import CollectionSummary

    client, runner, key = _publish_now_client(tmp_path, "collect_ok")
    runner.collect_new_products.return_value = CollectionSummary(
        fetched=48,
        over_price=11,
        duplicates=5,
        stored=32,
        by_brand={"mango": 18, "zara": 9, "bershka": 5},
    )

    res = client.post("/api/collect-products", headers={"X-Admin-Key": key})

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["stored"] == 32
    assert body["by_brand"] == {"mango": 18, "zara": 9, "bershka": 5}
    assert "32" in body["message"]
    assert "mango 18" in body["message"]


def test_collect_products_explains_an_empty_result(tmp_path: Path):
    from core.pipeline import CollectionSummary

    client, runner, key = _publish_now_client(tmp_path, "collect_empty")
    runner.collect_new_products.return_value = CollectionSummary(fetched=6, over_price=2, duplicates=4)

    res = client.post("/api/collect-products", headers={"X-Admin-Key": key})

    body = res.json()
    assert body["success"] is False
    assert body["stored"] == 0
    assert "Просмотрено 6" in body["message"]
    assert "уже публиковались 4" in body["message"]


def test_publish_now_reports_why_nothing_was_published(tmp_path: Path):
    """When no post could go out, the admin sees the reason instead of a silent success."""
    from core.pipeline import CycleSummary

    client, runner, key = _publish_now_client(tmp_path, "publish_now_empty")
    runner.publish_next_eligible_product.return_value = (False, "В базе нет подходящих товаров для публикации.")
    runner.run_cycle.return_value = CycleSummary(skipped_daily_cap=True)

    res = client.post("/api/publish-now", headers={"X-Admin-Key": key})

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert body["published"] == 0
    assert "лимит" in body["message"]


