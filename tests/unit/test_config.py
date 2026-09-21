"""Unit tests for configuration loading, validation, and hot reload.

Per SDD §3.11 and §9.
"""

from decimal import Decimal
from pathlib import Path
import tempfile
import pytest
from pydantic import ValidationError
import yaml

from config.app_config import AppConfig, ScheduleSettings


def test_schedule_settings_validation() -> None:
    """Valid and invalid time strings."""
    valid = ScheduleSettings(times=["09:30", "18:00"])
    assert valid.times == ["09:30", "18:00"]

    with pytest.raises(ValidationError):
        ScheduleSettings(times=["25:00"])  # Hour out of range

    with pytest.raises(ValidationError):
        ScheduleSettings(times=["12:65"])  # Minute out of range

    with pytest.raises(ValidationError):
        ScheduleSettings(times=["noon"])  # Malformed string


def test_hot_reload_fields() -> None:
    """Test updating markup and schedule in YAML file and calling reload_hot_fields."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump({
            "markup": 12.50,
            "dry_run": True,
            "schedule": {"times": ["11:00"]},
        }, f)
        cfg_path = Path(f.name)

    try:
        config = AppConfig.load(config_path=cfg_path, env_path="non_existent.env")
        assert config.markup == Decimal("12.50")
        assert config.schedule.times == ["11:00"]

        # Modify file on disk
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump({
                "markup": 25.00,
                "dry_run": False,
                "schedule": {"times": ["09:00", "21:00"]},
            }, f)

        # Trigger hot reload
        config.reload_hot_fields()
        assert config.markup == Decimal("25.00")
        assert config.dry_run is False
        assert config.schedule.times == ["09:00", "21:00"]

    finally:
        if cfg_path.exists():
            cfg_path.unlink()


def test_validate_live_credentials() -> None:
    """Detect missing or mock credentials when attempting live mode."""
    cfg = AppConfig()
    missing = cfg.validate_live_credentials()
    assert "OPENAI_API_KEY" in missing
    assert "TELEGRAM_BOT_TOKEN" in missing
    assert "INSTAGRAM_ACCESS_TOKEN" in missing
