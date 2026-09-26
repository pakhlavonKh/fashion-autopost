"""Unit tests for PipelineRunner orchestration.

Per SDD §2.2, §5, §6 and §9.
Validates per-product error isolation, moderation gates, daily caps, and admin notification.
"""

from decimal import Decimal
import pytest

from adapters.base import RawProduct
from config.app_config import AppConfig
from core.moderation import ConfigurableModerationGate
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from tests.conftest import (
    FakeLLMProvider,
    FakeProductRepository,
    FakePublisher,
    FakeSourceAdapter,
)


class MockPromptLoader:
    def load_prompt(self) -> str:
        return "Curate fashion items."


class MockAdminNotifier:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, str, str | None]] = []

    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        self.alerts.append((stage, message, external_id))


def test_pipeline_runner_happy_path(sample_products: list[RawProduct]) -> None:
    """Validate full pipeline cycle with fakes."""
    repo = FakeProductRepository()
    source = FakeSourceAdapter(sample_products)
    llm = FakeLLMProvider(select_count=2)
    fx = FixedRateConverter(fixed_rate=Decimal("1.0"))
    pub1 = FakePublisher("telegram")
    pub2 = FakePublisher("instagram")
    config = AppConfig()

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub1, pub2],
        config=config,
        prompt_loader=MockPromptLoader(),
    )

    summary = runner.run_cycle()
    assert summary.fetched == 3
    assert summary.unseen == 3
    assert summary.selected == 2
    assert summary.published == 2
    assert summary.failed == 0

    assert len(pub1.published_posts) == 2
    assert len(pub2.published_posts) == 2
    assert repo.get_published_ids() == {"p-1", "p-2"}


def test_pipeline_runner_per_product_isolation(sample_products: list[RawProduct]) -> None:
    """If one publisher fails for a product, other items in the batch must still publish."""
    repo = FakeProductRepository()
    source = FakeSourceAdapter(sample_products)
    llm = FakeLLMProvider(select_count=2)
    fx = FixedRateConverter()
    
    # Custom publisher that fails only on p-1
    class FailingPublisher(FakePublisher):
        def publish(self, post):
            if "p-1" in post.product_url or "p1" in post.product_url:
                from publishers.base import PublishResult
                return PublishResult(success=False, error="Simulated p-1 error")
            return super().publish(post)

    pub_failing = FailingPublisher("telegram")
    config = AppConfig()
    notifier = MockAdminNotifier()

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub_failing],
        config=config,
        prompt_loader=MockPromptLoader(),
        notifier=notifier,
    )

    summary = runner.run_cycle()
    assert summary.selected == 2
    assert summary.published == 1
    assert summary.failed == 1
    assert "p-2" in repo.get_published_ids()
    assert "p-1" not in repo.get_published_ids()
    assert len(notifier.alerts) >= 1


def test_pipeline_runner_daily_cap(sample_products: list[RawProduct]) -> None:
    """When daily publish cap is reached, pipeline skips processing."""
    repo = FakeProductRepository()
    source = FakeSourceAdapter(sample_products)
    llm = FakeLLMProvider(select_count=2)
    fx = FixedRateConverter()
    pub = FakePublisher("telegram")
    config = AppConfig(daily_publish_cap=1)

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub],
        config=config,
        prompt_loader=MockPromptLoader(),
    )

    # First cycle publishes 1 item (due to cap remaining = 1)
    summary1 = runner.run_cycle()
    assert summary1.published == 1

    # Second cycle detects daily cap reached (1 published today >= 1 cap)
    summary2 = runner.run_cycle()
    assert summary2.skipped_daily_cap is True
    assert summary2.published == 0


def test_pipeline_runner_moderation_gate(sample_products: list[RawProduct]) -> None:
    """When moderation is enabled and auto_approve=False, products enter pending_review."""
    repo = FakeProductRepository()
    source = FakeSourceAdapter(sample_products)
    llm = FakeLLMProvider(select_count=1)
    fx = FixedRateConverter()
    pub = FakePublisher("telegram")
    config = AppConfig()
    gate = ConfigurableModerationGate(enabled=True, auto_approve=False)

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub],
        config=config,
        prompt_loader=MockPromptLoader(),
        moderation_gate=gate,
    )

    summary = runner.run_cycle()
    assert summary.selected == 1
    assert summary.published == 0
    assert summary.pending_review == 1
    assert len(pub.published_posts) == 0
    assert repo.products["p-1"]["status"] == "pending_review"


def test_partial_failure_does_not_duplicate_successful_channel(sample_products: list[RawProduct]) -> None:
    """If Telegram succeeds but Instagram fails in cycle 1, cycle 2 must NOT re-post to Telegram."""
    from publishers.base import PublishResult

    repo = FakeProductRepository()
    source = FakeSourceAdapter([sample_products[0]])  # Only p-1
    llm = FakeLLMProvider(select_count=1)
    fx = FixedRateConverter()
    config = AppConfig()

    pub_telegram = FakePublisher("telegram")

    class FlakyInstagramPublisher(FakePublisher):
        def __init__(self, platform_name: str) -> None:
            super().__init__(platform_name)
            self.should_fail = True

        def publish(self, post):
            if self.should_fail:
                return PublishResult(success=False, error="Simulated Meta 500 error")
            return super().publish(post)

    pub_instagram = FlakyInstagramPublisher("instagram")

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub_telegram, pub_instagram],
        config=config,
        prompt_loader=MockPromptLoader(),
    )

    # Cycle 1: Telegram succeeds, Instagram fails
    summary1 = runner.run_cycle()
    assert summary1.selected == 1
    assert summary1.published == 0
    assert summary1.failed == 1
    assert len(pub_telegram.published_posts) == 1
    assert len(pub_instagram.published_posts) == 0
    # Telegram post ID must already be recorded in DB
    assert repo.products["p-1"]["telegram_post_id"] is not None
    assert repo.products["p-1"]["status"] == "failed"

    # Cycle 2: Instagram is now working
    pub_instagram.should_fail = False
    summary2 = runner.run_cycle()

    assert summary2.selected == 1
    assert summary2.published == 1
    assert summary2.failed == 0
    # Telegram was SKIPPED in cycle 2 because it already succeeded in cycle 1!
    assert len(pub_telegram.published_posts) == 1
    # Instagram was published in cycle 2
    assert len(pub_instagram.published_posts) == 1
    # Both post IDs are now present and status is published
    assert repo.products["p-1"]["status"] == "published"
    assert repo.products["p-1"]["telegram_post_id"] is not None
    assert repo.products["p-1"]["instagram_post_id"] is not None


def test_pipeline_runner_channel_disabled(sample_products: list[RawProduct]) -> None:
    """When a channel is disabled in config, it is not called and the cycle succeeds."""
    repo = FakeProductRepository()
    source = FakeSourceAdapter([sample_products[0]])
    llm = FakeLLMProvider(select_count=1)
    fx = FixedRateConverter()

    config = AppConfig()
    config.telegram.enabled = True
    config.instagram.enabled = False  # Disable Instagram

    pub_tg = FakePublisher("telegram")
    pub_ig = FakePublisher("instagram")

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub_tg, pub_ig],
        config=config,
        prompt_loader=MockPromptLoader(),
    )

    summary = runner.run_cycle()
    assert summary.selected == 1
    assert summary.published == 1
    assert len(pub_tg.published_posts) == 1
    assert len(pub_ig.published_posts) == 0  # Instagram never called!
    assert repo.products["p-1"]["status"] == "published"

