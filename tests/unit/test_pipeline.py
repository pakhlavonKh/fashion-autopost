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
