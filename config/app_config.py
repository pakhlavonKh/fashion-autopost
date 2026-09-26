"""Application configuration loading and validation using Pydantic.

Supports loading secrets from .env and operational parameters from config.yaml.
Allows hot-reloading of operational parameters (prompt path, markup, schedule, etc.)
per cycle without restarting the service.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
import os
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class AggregatorSettings(BaseModel):
    """Settings for third-party aggregator source."""
    mode: Literal["mock", "http", "playwright"] = "mock"
    base_url: str = "https://api.aggregator.example.com"
    api_key: str = Field(default="mock-aggregator-key")
    timeout_seconds: float = 30.0
    stores: list[str] = Field(default_factory=lambda: ["zara", "mango"])


class StoreSelectorConfig(BaseModel):
    """Optional declarative CSS selector overrides for custom or non-standard websites."""
    item: str | None = None
    title: str | None = None
    price: str | None = None
    image: str | None = None
    link: str | None = None
    id_attr: str | None = None


class ScraperStoreConfig(BaseModel):
    """Configuration for an individual store scrape target."""
    enabled: bool = True
    url: str
    currency: str = "EUR"
    max_items: int = 10
    selectors: StoreSelectorConfig | None = None
    cookie_button: str | None = None
    scroll_steps: int | None = None


class ScraperSettings(BaseModel):
    """Settings for Playwright-based web scraper."""
    headless: bool = True
    timeout_seconds: float = 30.0
    scroll_steps: int = 3
    wait_after_scroll_ms: int = 1500
    user_agent: str | None = None
    stores: dict[str, ScraperStoreConfig] = Field(
        default_factory=lambda: {
            "zara": ScraperStoreConfig(
                enabled=True,
                url="https://www.zara.com/tr/tr/kadin-yeni-l1180.html",
                currency="TRY",
                max_items=10,
            ),
            "mango": ScraperStoreConfig(
                enabled=True,
                url="https://shop.mango.com/es/en/c/women/new-now",
                currency="EUR",
                max_items=10,
            ),
        }
    )


class OpenAISettings(BaseModel):
    """Settings for OpenAI LLM provider."""
    api_key: str = Field(default="mock-openai-key")
    model: str = "gpt-4.1"
    temperature: float = 0.7
    max_retries: int = 2


DEFAULT_TELEGRAM_BIO_FOOTER = (
    "Европейское качество\n"
    "Обращаться: @nigora_7\n"
    "Тел:+998998484044\n"
    "✨Отзывы: @otzivi_fashbou\n"
    "Товары в наличии: @vnalichiifash\n\n"
    "Наш Instagram:\n"
    "https://www.instagram.com/fashionnestboutique?igsh=Z29pN2tscmJhd3Mx\n\n"
    "Наш основной Telegram-канал:\n"
    "https://t.me/fashionalleyb"
)


class TelegramSettings(BaseModel):
    """Settings for Telegram Bot publisher."""
    enabled: bool = True
    bot_token: str = Field(default="mock-telegram-token")
    channel_id: str | None = None
    admin_chat_id: str | None = None
    bio_footer: str = Field(default=DEFAULT_TELEGRAM_BIO_FOOTER)


class InstagramSettings(BaseModel):
    """Settings for Instagram Graph API publisher."""
    enabled: bool = True
    access_token: str = Field(default="mock-instagram-token")
    account_id: str = Field(default="mock-account-id")


class S3Settings(BaseModel):
    """Settings for S3-compatible image hosting."""
    endpoint_url: str | None = None
    bucket_name: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    region: str = "us-east-1"
    public_url_prefix: str | None = None


class FxSettings(BaseModel):
    """Settings for currency conversion."""
    mode: Literal["fixed", "dynamic"] = "fixed"
    fixed_rate: Decimal = Decimal("1.0")
    # Base rates relative to target currency, e.g. {"EUR": 1.08, "TRY": 0.03}
    rates: dict[str, Decimal] = Field(default_factory=lambda: {
        "EUR": Decimal("1.08"),
        "USD": Decimal("1.00"),
        "TRY": Decimal("0.029"),
        "GBP": Decimal("1.29"),
    })


class ScheduleSettings(BaseModel):
    """Settings for cron-like publication schedule and autonomous intervals."""
    posts_per_day: int = 96
    times: list[str] = Field(default_factory=list)
    interval_minutes: int | None = 15
    timezone: str = "UTC"

    @field_validator("times")
    @classmethod
    def validate_times(cls, v: list[str]) -> list[str]:
        for item in v:
            parts = item.split(":")
            if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                raise ValueError(f"Invalid time format '{item}', expected HH:MM")
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"Time '{item}' out of range (00:00 - 23:59)")
        return v


class ModerationSettings(BaseModel):
    """Settings for post moderation gate (SRS §10.2)."""
    enabled: bool = False
    auto_approve: bool = True


class AlertSettings(BaseModel):
    """Settings for critical error notifications (SRS §10.5)."""
    channel: Literal["telegram", "console", "none"] = "console"
    telegram_chat_id: str | None = None


class DashboardSettings(BaseModel):
    """Settings for the admin dashboard server and security authentication."""
    enabled: bool = True
    port: int = 8000
    host: str = "0.0.0.0"
    admin_username: str = "admin"
    admin_password: str = "fashion-admin-2026"
    admin_key: str = "fashion-admin-2026"


class AppConfig(BaseModel):
    """Root configuration object combining env secrets and YAML parameters."""

    # Operational settings
    markup: Decimal = Decimal("15.00")
    target_currency: str = "USD"
    max_products_per_run: int = 5
    daily_publish_cap: int | None = None  # SRS §10.4
    prompt_path: str = "./prompt.txt"
    dry_run: bool = False
    db_url: str = "sqlite:///./data/app.db"
    include_product_link: bool = True

    # Component settings
    aggregator: AggregatorSettings = Field(default_factory=AggregatorSettings)
    scraper: ScraperSettings = Field(default_factory=ScraperSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    instagram: InstagramSettings = Field(default_factory=InstagramSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    fx: FxSettings = Field(default_factory=FxSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    moderation: ModerationSettings = Field(default_factory=ModerationSettings)
    alert: AlertSettings = Field(default_factory=AlertSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)

    # Config file tracking for hot-reload
    _config_path: Path | None = None

    @classmethod
    def load(cls, config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> "AppConfig":
        """Load configuration from .env and config.yaml.
        
        Secrets are loaded from environment variables (overriding YAML where present).
        Operational parameters are loaded from YAML.
        """
        env_file = Path(env_path)
        if env_file.exists():
            load_dotenv(env_file, override=True)
        elif str(env_path) in (".env", ""):
            load_dotenv(override=False)

        yaml_data: dict[str, Any] = {}
        cfg_file = Path(config_path)
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
                if isinstance(content, dict):
                    yaml_data = content

        # Map environment variables into settings
        aggregator_data = yaml_data.get("aggregator", {})
        if os.getenv("AGGREGATOR_API_KEY"):
            aggregator_data["api_key"] = os.environ["AGGREGATOR_API_KEY"]
        if os.getenv("AGGREGATOR_MODE"):
            aggregator_data["mode"] = os.environ["AGGREGATOR_MODE"]
        yaml_data["aggregator"] = aggregator_data

        openai_data = yaml_data.get("openai", {})
        if os.getenv("OPENAI_API_KEY"):
            openai_data["api_key"] = os.environ["OPENAI_API_KEY"]
        if os.getenv("OPENAI_MODEL"):
            openai_data["model"] = os.environ["OPENAI_MODEL"]
        yaml_data["openai"] = openai_data

        scraper_data = yaml_data.get("scraper", {})
        if os.getenv("SCRAPER_HEADLESS") is not None:
            scraper_data["headless"] = os.environ["SCRAPER_HEADLESS"].strip().lower() in ("true", "1", "yes")
        if os.getenv("SCRAPER_TIMEOUT_SECONDS"):
            try:
                scraper_data["timeout_seconds"] = float(os.environ["SCRAPER_TIMEOUT_SECONDS"])
            except ValueError:
                pass
        yaml_data["scraper"] = scraper_data

        telegram_data = yaml_data.get("telegram", {})
        if os.getenv("TELEGRAM_ENABLED") is not None:
            telegram_data["enabled"] = os.environ["TELEGRAM_ENABLED"].strip().lower() in ("true", "1", "yes")
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            telegram_data["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
        if os.getenv("TELEGRAM_CHANNEL_ID"):
            telegram_data["channel_id"] = os.environ["TELEGRAM_CHANNEL_ID"]
        if os.getenv("TELEGRAM_ADMIN_CHAT_ID"):
            telegram_data["admin_chat_id"] = os.environ["TELEGRAM_ADMIN_CHAT_ID"]
        yaml_data["telegram"] = telegram_data

        instagram_data = yaml_data.get("instagram", {})
        if os.getenv("INSTAGRAM_ENABLED") is not None:
            instagram_data["enabled"] = os.environ["INSTAGRAM_ENABLED"].strip().lower() in ("true", "1", "yes")
        if os.getenv("INSTAGRAM_ACCESS_TOKEN"):
            instagram_data["access_token"] = os.environ["INSTAGRAM_ACCESS_TOKEN"]
        if os.getenv("INSTAGRAM_ACCOUNT_ID"):
            instagram_data["account_id"] = os.environ["INSTAGRAM_ACCOUNT_ID"]
        yaml_data["instagram"] = instagram_data

        s3_data = yaml_data.get("s3", {})
        if os.getenv("S3_ENDPOINT"):
            s3_data["endpoint_url"] = os.environ["S3_ENDPOINT"]
        if os.getenv("S3_BUCKET"):
            s3_data["bucket_name"] = os.environ["S3_BUCKET"]
        if os.getenv("S3_ACCESS_KEY"):
            s3_data["access_key"] = os.environ["S3_ACCESS_KEY"]
        if os.getenv("S3_SECRET_KEY"):
            s3_data["secret_key"] = os.environ["S3_SECRET_KEY"]
        if os.getenv("S3_REGION"):
            s3_data["region"] = os.environ["S3_REGION"]
        if os.getenv("S3_PUBLIC_URL_PREFIX"):
            s3_data["public_url_prefix"] = os.environ["S3_PUBLIC_URL_PREFIX"]
        yaml_data["s3"] = s3_data

        if os.getenv("DB_URL"):
            yaml_data["db_url"] = os.environ["DB_URL"]

        if os.getenv("DRY_RUN") is not None:
            yaml_data["dry_run"] = os.environ["DRY_RUN"].strip().lower() in ("true", "1", "yes")

        dashboard_data = yaml_data.get("dashboard", {})
        # Configurable admin username
        if os.getenv("DASHBOARD_ADMIN_USERNAME"):
            dashboard_data["admin_username"] = os.environ["DASHBOARD_ADMIN_USERNAME"].strip()
        elif os.getenv("DASHBOARD_USERNAME"):
            dashboard_data["admin_username"] = os.environ["DASHBOARD_USERNAME"].strip()

        # Configurable admin password
        if os.getenv("DASHBOARD_ADMIN_PASSWORD"):
            dashboard_data["admin_password"] = os.environ["DASHBOARD_ADMIN_PASSWORD"].strip()
            dashboard_data["admin_key"] = os.environ["DASHBOARD_ADMIN_PASSWORD"].strip()
        elif os.getenv("DASHBOARD_PASSWORD"):
            dashboard_data["admin_password"] = os.environ["DASHBOARD_PASSWORD"].strip()
            dashboard_data["admin_key"] = os.environ["DASHBOARD_PASSWORD"].strip()
        elif os.getenv("DASHBOARD_ADMIN_KEY"):
            if "admin_password" not in dashboard_data:
                dashboard_data["admin_password"] = os.environ["DASHBOARD_ADMIN_KEY"].strip()
            dashboard_data["admin_key"] = os.environ["DASHBOARD_ADMIN_KEY"].strip()

        if "admin_password" in dashboard_data and "admin_key" not in dashboard_data:
            dashboard_data["admin_key"] = dashboard_data["admin_password"]
        elif "admin_key" in dashboard_data and "admin_password" not in dashboard_data:
            dashboard_data["admin_password"] = dashboard_data["admin_key"]

        yaml_data["dashboard"] = dashboard_data

        config = cls.model_validate(yaml_data)
        config._config_path = cfg_file
        return config

    def reload_hot_fields(self) -> None:
        """Reload fields that business owners can edit without restart.
        
        Per SRS §1.2 & FR-3.2, FR-8.2:
        - markup
        - schedule
        - prompt_path
        - dry_run
        - daily_publish_cap
        - target_currency
        """
        if not self._config_path or not self._config_path.exists():
            return

        with open(self._config_path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
            if not isinstance(content, dict):
                return

        if "markup" in content:
            self.markup = Decimal(str(content["markup"]))
        if "target_currency" in content:
            self.target_currency = str(content["target_currency"])
        if "max_products_per_run" in content:
            self.max_products_per_run = int(content["max_products_per_run"])
        if "daily_publish_cap" in content:
            val = content["daily_publish_cap"]
            self.daily_publish_cap = int(val) if val is not None else None
        if "prompt_path" in content:
            self.prompt_path = str(content["prompt_path"])
        if "dry_run" in content:
            self.dry_run = bool(content["dry_run"])
        if "schedule" in content and isinstance(content["schedule"], dict):
            self.schedule = ScheduleSettings.model_validate(content["schedule"])
        if "fx" in content and isinstance(content["fx"], dict):
            self.fx = FxSettings.model_validate(content["fx"])
        if "moderation" in content and isinstance(content["moderation"], dict):
            self.moderation = ModerationSettings.model_validate(content["moderation"])
        if "alert" in content and isinstance(content["alert"], dict):
            self.alert = AlertSettings.model_validate(content["alert"])
        if "telegram" in content and isinstance(content["telegram"], dict):
            self.telegram = TelegramSettings.model_validate(content["telegram"])
        if "instagram" in content and isinstance(content["instagram"], dict):
            self.instagram = InstagramSettings.model_validate(content["instagram"])
        if "scraper" in content and isinstance(content["scraper"], dict):
            self.scraper = ScraperSettings.model_validate(content["scraper"])
        if "dashboard" in content and isinstance(content["dashboard"], dict):
            d_cfg = dict(content["dashboard"])
            if "admin_password" in d_cfg and "admin_key" not in d_cfg:
                d_cfg["admin_key"] = d_cfg["admin_password"]
            elif "admin_key" in d_cfg and "admin_password" not in d_cfg:
                d_cfg["admin_password"] = d_cfg["admin_key"]
            self.dashboard = DashboardSettings.model_validate(d_cfg)

    def validate_live_credentials(self) -> list[str]:
        """Verify that credentials are valid for live non-dry-run operation."""
        missing = []
        if not self.openai.api_key or "mock" in self.openai.api_key.lower():
            missing.append("OPENAI_API_KEY")

        if self.telegram.enabled:
            if not self.telegram.bot_token or "mock" in self.telegram.bot_token.lower():
                missing.append("TELEGRAM_BOT_TOKEN")

        if self.instagram.enabled:
            if not self.instagram.access_token or "mock" in self.instagram.access_token.lower():
                missing.append("INSTAGRAM_ACCESS_TOKEN")
            if not self.instagram.account_id or "mock" in self.instagram.account_id.lower():
                missing.append("INSTAGRAM_ACCOUNT_ID")

        if not self.telegram.enabled and not self.instagram.enabled:
            missing.append("NO_PUBLISHERS_ENABLED (enable at least telegram or instagram)")

        return missing
