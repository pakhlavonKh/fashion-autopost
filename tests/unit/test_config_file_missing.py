"""config.yaml left git: a server can end up without it, or with a directory in its place.

git pull removes the old tracked copy, and Docker then mounts an empty directory where the
file should be. The service failed to start ("Is a directory"), restarted forever, and the
Telegram bot stopped answering. It now runs with the tracked template's settings, says what
to fix, and a dashboard change writes the whole file rather than one field of it.
"""

from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from config.app_config import CONFIG_TEMPLATE_NAME, AppConfig
from dashboard.server import create_dashboard_app
from storage.repository import SqlAlchemyProductRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / CONFIG_TEMPLATE_NAME


def _server_after_pull(tmp_path: Path) -> Path:
    """A server checkout after git pull: the template is there, config.yaml is not."""
    shutil.copy(TEMPLATE, tmp_path / CONFIG_TEMPLATE_NAME)
    return tmp_path


def _load(server: Path) -> AppConfig:
    return AppConfig.load(config_path=server / "config.yaml", env_path=server / "no.env")


def test_the_template_holds_the_production_settings_and_no_password() -> None:
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert data["schedule"]["timezone"] == "Asia/Tashkent"
    assert data["schedule"]["interval_minutes"] is None
    assert data["markup"] == 10.0
    assert data["instagram"]["enabled"] is False
    assert sorted(data["scraper"]["stores"]) == ["hm", "mango", "stradivarius", "zara"]
    assert "admin_password" not in data["dashboard"]
    assert "admin_key" not in data["dashboard"]


def test_a_directory_in_place_of_config_yaml_does_not_stop_the_service(tmp_path: Path) -> None:
    server = _server_after_pull(tmp_path)
    (server / "config.yaml").mkdir()  # what Docker mounts when the file is missing on the host
    config = _load(server)
    assert config.schedule.timezone == "Asia/Tashkent"
    assert config.markup == Decimal("10.0")
    assert config._settings_source == server / CONFIG_TEMPLATE_NAME
    config.reload_hot_fields()  # nothing to re-read, and no error every cycle
    assert config.markup == Decimal("10.0")


def test_a_missing_config_yaml_runs_on_the_template_not_on_defaults(tmp_path: Path) -> None:
    config = _load(_server_after_pull(tmp_path))
    assert config.schedule.timezone == "Asia/Tashkent"  # the default is UTC
    assert config.schedule.interval_minutes is None  # the default scrapes the catalogs every 15 minutes
    assert config.markup == Decimal("10.0")  # the default is 15
    assert config.max_products_per_run == 1
    assert config.daily_publish_cap == 300


def test_config_yaml_wins_over_the_template(tmp_path: Path) -> None:
    server = _server_after_pull(tmp_path)
    (server / "config.yaml").write_text(yaml.dump({"markup": 12.5}), encoding="utf-8")
    config = _load(server)
    assert config.markup == Decimal("12.5")
    assert config._settings_source == server / "config.yaml"


def test_without_config_yaml_or_template_the_defaults_still_load(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").mkdir()
    config = _load(tmp_path)
    assert config._settings_source is None
    assert config.schedule.timezone == "UTC"


def test_main_starts_with_a_directory_in_place_of_config_yaml(tmp_path: Path) -> None:
    """The start that failed on the server: python main.py while config.yaml is a directory."""
    server = _server_after_pull(tmp_path)
    (server / "config.yaml").mkdir()
    env = server / "server.env"
    env.write_text(
        "TELEGRAM_BOT_TOKEN=123456:test\nOPENAI_API_KEY=sk-test\nINSTAGRAM_ENABLED=false\n"
        "DASHBOARD_ADMIN_PASSWORD=secret\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "main.py"), "--config", str(server / "config.yaml"),
         "--env", str(env), "--check-config"],
        cwd=server,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Configuration validation successful" in output
    assert "is a directory" in output
    assert "cp config.example.yaml config.yaml" in output


def _dashboard(server: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_ADMIN_PASSWORD", "dash-secret")
    config = _load(server)
    repo = SqlAlchemyProductRepository(f"sqlite:///{server / 'dash.db'}")
    return TestClient(create_dashboard_app(config=config, runner=MagicMock(), repo=repo))


def test_a_dashboard_change_without_config_yaml_writes_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server_after_pull(tmp_path)
    client = _dashboard(server, monkeypatch)
    res = client.post("/api/config", json={"markup": 12}, headers={"X-Admin-Key": "dash-secret"})
    assert res.status_code == 200, res.text
    saved = yaml.safe_load((server / "config.yaml").read_text(encoding="utf-8"))
    assert saved["markup"] == 12
    # Not a one-field file: the next start keeps the schedule, timezone and stores.
    assert saved["schedule"]["timezone"] == "Asia/Tashkent"
    assert sorted(saved["scraper"]["stores"]) == ["hm", "mango", "stradivarius", "zara"]
    assert _load(server).markup == Decimal("12")


def test_a_dashboard_change_explains_a_directory_in_place_of_config_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server_after_pull(tmp_path)
    (server / "config.yaml").mkdir()
    client = _dashboard(server, monkeypatch)
    res = client.post("/api/config", json={"markup": 12}, headers={"X-Admin-Key": "dash-secret"})
    assert res.status_code == 500
    assert "cp config.example.yaml config.yaml" in res.json()["detail"]
