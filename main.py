"""Composition root for fashion-autopost backend service.

Per SDD §3.12.
Wires together configuration, database repository, source adapters, LLM provider,
pricing engine, publishers (with DryRunPublisher wrapper when dry_run=True),
and the APScheduler.
"""

import argparse
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from adapters.aggregator_adapter import AggregatorAPIAdapter
from adapters.aggregator_client import HttpAggregatorClient, MockAggregatorClient
from adapters.base import SourceAdapter
from adapters.playwright_adapter import PlaywrightScraperAdapter
from config.app_config import AppConfig
from core.moderation import ConfigurableModerationGate
from core.pipeline import PipelineRunner
from core.pricing import DynamicRateConverter, FixedRateConverter, FxConverter
from llm.openai_provider import FilePromptLoader, OpenAIProvider
from logging_setup.admin_notifier import (
    CompositeAdminNotifier,
    ConsoleAdminNotifier,
    TelegramAdminNotifier,
)
from logging_setup.logger import setup_logging
from publishers.base import Publisher
from publishers.dry_run_publisher import DryRunPublisher
from publishers.image_hosting import PassthroughImageHost, S3ImageHost
from publishers.instagram_publisher import InstagramPublisher
from publishers.telegram_discovery import TelegramChatDiscoveryService
from publishers.telegram_publisher import TelegramPublisher
from scheduler.build_scheduler import build_scheduler
from storage.repository import SqlAlchemyProductRepository

logger = logging.getLogger("main")


def build_pipeline_runner(config: AppConfig) -> PipelineRunner:
    """Wire concrete implementations to abstract protocols based on config."""
    # 1. Storage / Repository
    repo = SqlAlchemyProductRepository(config.db_url)

    # 1.1 Dynamic Telegram Channel & Admin Chat Discovery
    discovery_service = TelegramChatDiscoveryService(
        bot_token=config.telegram.bot_token,
        repo=repo,
    )
    try:
        discovery_service.sync_updates()
    except Exception as exc:
        logger.warning("Telegram channel auto-discovery failed: %s", exc)

    # 2. Source Adapter & Aggregator Client (SRS §10.1)
    source: SourceAdapter
    if config.aggregator.mode == "playwright":
        logger.info("Using PlaywrightScraperAdapter for web scraping fashion catalog items.")
        source = PlaywrightScraperAdapter(
            app_config=config,
        )
    elif config.aggregator.mode == "http":
        aggregator_client = HttpAggregatorClient(
            base_url=config.aggregator.base_url,
            api_key=config.aggregator.api_key,
            timeout_seconds=config.aggregator.timeout_seconds,
        )
        source = AggregatorAPIAdapter(client=aggregator_client, stores=config.aggregator.stores)
    else:
        aggregator_client = MockAggregatorClient()
        source = AggregatorAPIAdapter(client=aggregator_client, stores=config.aggregator.stores)

    # 3. LLM Provider & Prompt Loader (SRS FR-2)
    prompt_loader = FilePromptLoader(config.prompt_path)
    llm = OpenAIProvider(
        api_key=config.openai.api_key,
        model=config.openai.model,
        temperature=config.openai.temperature,
        max_retries=config.openai.max_retries,
    )

    # 4. FX Converter (SRS §10.3)
    fx: FxConverter
    if config.fx.mode == "dynamic":
        fx = DynamicRateConverter(cached_rates=config.fx.rates, fallback_rate=config.fx.fixed_rate)
    else:
        fx = FixedRateConverter(fixed_rate=config.fx.fixed_rate, rates=config.fx.rates)

    # 5. Image Hosting for Instagram
    image_host = (
        S3ImageHost(
            endpoint_url=config.s3.endpoint_url,
            bucket_name=config.s3.bucket_name,
            access_key=config.s3.access_key,
            secret_key=config.s3.secret_key,
            region=config.s3.region,
            public_url_prefix=config.s3.public_url_prefix,
        )
        if config.s3.bucket_name
        else PassthroughImageHost()
    )

    # 6. Real Publishers (with dynamic Telegram channel resolution)
    telegram_pub = TelegramPublisher(
        bot_token=config.telegram.bot_token,
        channel_id=config.telegram.channel_id,
        repo=repo,
    )
    instagram_pub = InstagramPublisher(
        access_token=config.instagram.access_token,
        account_id=config.instagram.account_id,
        image_host=image_host,
    )

    publishers: list[Publisher] = [telegram_pub, instagram_pub]

    # 7. Dry-Run Wrapping (SRS FR-9)
    if config.dry_run:
        logger.info("Dry-run mode is ENABLED: wrapping all publishers in DryRunPublisher.")
        publishers = [DryRunPublisher(p) for p in publishers]
    else:
        logger.warning("LIVE PUBLISHING MODE: Real posts will be sent to Telegram and Instagram!")

    # 8. Moderation Gate (SRS §10.2)
    moderation_gate = ConfigurableModerationGate(
        enabled=config.moderation.enabled,
        auto_approve=config.moderation.auto_approve,
    )

    # 9. Admin Notifier (SRS §10.5)
    notifiers = []
    if config.alert.channel in ("console", "both"):
        notifiers.append(ConsoleAdminNotifier())
    if config.alert.channel in ("telegram", "both"):
        tg_chat = config.alert.telegram_chat_id or config.telegram.admin_chat_id
        notifiers.append(
            TelegramAdminNotifier(
                bot_token=config.telegram.bot_token,
                admin_chat_id=tg_chat,
                repo=repo,
            )
        )

    admin_notifier = CompositeAdminNotifier(notifiers) if notifiers else ConsoleAdminNotifier()

    # 10. Assemble PipelineRunner
    return PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=publishers,
        config=config,
        prompt_loader=prompt_loader,
        moderation_gate=moderation_gate,
        notifier=admin_notifier,
    )


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Autonomous Clothing Product Publishing Backend (Telegram & Instagram)"
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--env",
        "-e",
        default=".env",
        help="Path to .env secrets file (default: .env)",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Execute a single pipeline cycle immediately and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run mode (overrides config.yaml)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live publishing mode (dry-run = false)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and check credentials without executing pipeline",
    )
    parser.add_argument(
        "--dashboard",
        "-d",
        action="store_true",
        help="Launch the Web Admin Dashboard server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the Web Admin Dashboard (default: 8000)",
    )
    parser.add_argument(
        "--with-scheduler",
        dest="with_scheduler",
        action="store_true",
        default=True,
        help="Run background APScheduler publisher along with dashboard (default: True)",
    )
    parser.add_argument(
        "--no-scheduler",
        dest="with_scheduler",
        action="store_false",
        help="Disable background APScheduler publisher when running dashboard",
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = AppConfig.load(config_path=args.config, env_path=args.env)
    except Exception as exc:
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    # CLI flag overrides
    if args.dry_run:
        config.dry_run = True
    elif args.live:
        config.dry_run = False

    # Initialize logging with secret masking
    secrets_to_mask = [
        config.openai.api_key,
        config.telegram.bot_token,
        config.instagram.access_token,
        config.s3.access_key,
        config.s3.secret_key,
    ]
    setup_logging(log_dir="./logs", secrets_to_mask=secrets_to_mask)

    logger.info("Fashion Autopost starting up...")
    logger.info("Dry-run mode: %s", config.dry_run)
    logger.info("Target currency: %s, Markup: %s", config.target_currency, config.markup)
    logger.info("Publishing schedule: %s in %s", config.schedule.times, config.schedule.timezone)

    if args.check_config:
        print("Configuration validation successful!")
        if not config.dry_run:
            missing = config.validate_live_credentials()
            if missing:
                print(f"WARNING: The following live credentials are missing or mock: {missing}")
            else:
                print("All live credentials present.")
        else:
            print("System configured for DRY-RUN mode.")
        sys.exit(0)

    # Validate live credentials if running live
    if not config.dry_run:
        missing = config.validate_live_credentials()
        if missing:
            if args.dashboard:
                logger.warning(
                    "Running dashboard in LIVE mode with missing or unconfigured credentials: %s. "
                    "Publishing to unconfigured channels will be logged.",
                    ", ".join(missing),
                )
            else:
                logger.error(
                    "Cannot start in LIVE mode: missing or mock credentials for: %s",
                    ", ".join(missing),
                )
                sys.exit(1)

    runner = build_pipeline_runner(config)

    if args.dashboard:
        import uvicorn
        from dashboard.server import create_dashboard_app

        scheduler = None
        if args.with_scheduler:
            logger.info("Starting background APScheduler publisher alongside dashboard...")
            scheduler = build_scheduler(config.schedule, runner, blocking=False)
            scheduler.start()
            logger.info(
                "Background APScheduler started: interval=%sm, times=%s (%s)",
                config.schedule.interval_minutes,
                config.schedule.times,
                config.schedule.timezone,
            )

        app = create_dashboard_app(config, runner, runner.repo, scheduler=scheduler)
        print(f"\n[+] Fashion Autopost Admin Dashboard is running at http://0.0.0.0:{args.port}\n")
        logger.info("Starting Admin Dashboard web server on port %d...", args.port)
        try:
            uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
        finally:
            if scheduler and getattr(scheduler, "running", False):
                logger.info("Shutting down background scheduler...")
                scheduler.shutdown(wait=False)
        return

    if args.run_once:
        logger.info("Executing single pipeline run (--run-once)...")
        summary = runner.run_cycle()
        print(
            f"Execution finished: {summary.published} published, "
            f"{summary.failed} failed, {summary.pending_review} pending review."
        )
        sys.exit(0 if summary.failed == 0 else 1)

    # Daemon mode with APScheduler
    logger.info("Starting scheduler daemon...")
    scheduler = build_scheduler(config.schedule, runner, blocking=True)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down gracefully.")


if __name__ == "__main__":
    main()
