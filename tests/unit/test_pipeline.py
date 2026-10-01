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
    # p-3 is 89.90 EUR, above the 80 USD store-price limit before markup.
    assert summary.fetched == 2
    assert summary.unseen == 2
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


class _LinkingTelegram(FakePublisher):
    def __init__(self) -> None:
        super().__init__("telegram")

    def publish(self, post):
        from publishers.base import PublishResult

        result = super().publish(post)
        number = len(self.published_posts)
        return PublishResult(
            success=result.success,
            platform_post_id=result.platform_post_id,
            links=(f"https://t.me/fashionalleyb/{number}",),
        )


def test_collecting_products_fills_the_queue_without_publishing(sample_products: list[RawProduct]) -> None:
    """The collect button must only stock the queue. Posting stays a separate action."""
    from unittest.mock import patch

    repo = FakeProductRepository()
    telegram = FakePublisher("telegram")
    instagram = FakePublisher("instagram")
    runner = PipelineRunner(
        source=FakeSourceAdapter(sample_products),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[telegram, instagram],
        config=AppConfig(),
        prompt_loader=MockPromptLoader(),
    )

    with patch("core.similarity.fetch_channel_posts", return_value=[]):
        summary = runner.collect_new_products()

    # p-3 costs 89.90 EUR, which is over the 80 USD store-price limit.
    assert summary.fetched == 3
    assert summary.over_price == 1
    assert summary.stored == 2
    assert summary.by_brand == {"zara": 1, "mango": 1}
    assert set(repo.products) == {"p-1", "p-2"}
    assert repo.products["p-1"]["status"] == "new"
    assert telegram.published_posts == []
    assert instagram.published_posts == []


def test_collecting_skips_products_already_in_the_channel(sample_products: list[RawProduct]) -> None:
    """A second collection must not queue what was already posted."""
    from unittest.mock import patch

    repo = FakeProductRepository()
    repo.upsert_new(sample_products[0])
    repo.mark_published("p-1", "telegram_1", None)

    runner = PipelineRunner(
        source=FakeSourceAdapter(sample_products),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[FakePublisher("telegram")],
        config=AppConfig(),
        prompt_loader=MockPromptLoader(),
    )

    with patch("core.similarity.fetch_channel_posts", return_value=[]):
        summary = runner.collect_new_products()

    assert summary.duplicates == 1
    assert summary.stored == 1
    assert summary.by_brand == {"mango": 1}


def test_retry_keeps_the_telegram_post_and_links_the_story_to_it(sample_products: list[RawProduct]) -> None:
    """Telegram succeeds and Instagram fails in cycle 1. Cycle 2 retries Instagram only.

    A product that is already in the channel must never be posted there a second
    time. The story links to the message the first cycle published.
    """
    from publishers.base import PublishResult

    repo = FakeProductRepository()
    source = FakeSourceAdapter([sample_products[0]])  # Only p-1
    llm = FakeLLMProvider(select_count=1)
    fx = FixedRateConverter()
    config = AppConfig()

    pub_telegram = _LinkingTelegram()

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
    assert len(pub_telegram.published_posts) == 1
    assert len(pub_instagram.published_posts) == 1
    assert pub_instagram.published_posts[0].telegram_links == ("https://t.me/fashionalleyb/1",)
    assert repo.products["p-1"]["status"] == "published"
    assert repo.products["p-1"]["telegram_post_id"] == "telegram_1"
    assert repo.products["p-1"]["instagram_post_id"] is not None


def test_retry_does_not_post_instagram_twice(sample_products: list[RawProduct]) -> None:
    """Instagram succeeds and Telegram fails in cycle 1. Cycle 2 posts only to Telegram."""
    repo = FakeProductRepository()
    pub_telegram = FakePublisher("telegram", should_fail=True)
    pub_instagram = FakePublisher("instagram")
    runner = PipelineRunner(
        source=FakeSourceAdapter([sample_products[0]]),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[pub_telegram, pub_instagram],
        config=AppConfig(),
        prompt_loader=MockPromptLoader(),
    )

    assert runner.run_cycle().failed == 1
    pub_telegram.should_fail = False
    assert runner.run_cycle().published == 1

    assert len(pub_telegram.published_posts) == 1
    assert len(pub_instagram.published_posts) == 1
    assert repo.products["p-1"]["status"] == "published"


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


def test_pipeline_drops_products_above_80_usd_before_markup() -> None:
    """80 USD store price is kept. 81 USD is not ingested. Cents are floored, markup is not part of the check."""
    at_limit = RawProduct(
        external_id="at-limit",
        source="zara",
        title="Cotton Shirt",
        price=Decimal("80.00"),
        currency="USD",
        photo_url="https://images.example.com/shirt.jpg",
        product_url="https://zara.com/shirt",
        in_stock=True,
    )
    # 70 USD plus the default markup is 85 USD, which must still be parsed.
    under_with_markup = RawProduct(
        external_id="under",
        source="zara",
        title="Linen Trousers",
        price=Decimal("70.00"),
        currency="USD",
        photo_url="https://images.example.com/trousers.jpg",
        product_url="https://zara.com/trousers",
        in_stock=True,
    )
    over_limit = RawProduct(
        external_id="over",
        source="mango",
        title="Wool Coat",
        price=Decimal("81.00"),
        currency="USD",
        photo_url="https://images.example.com/coat.jpg",
        product_url="https://mango.com/coat",
        in_stock=True,
    )
    repo = FakeProductRepository()
    source = FakeSourceAdapter([at_limit, under_with_markup, over_limit])
    llm = FakeLLMProvider(select_count=5)
    fx = FixedRateConverter(rates={"USD": Decimal("1.00"), "EUR": Decimal("1.08")})
    pub = FakePublisher("telegram")
    config = AppConfig(max_products_per_run=5)

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub],
        config=config,
        prompt_loader=MockPromptLoader(),
    )

    summary = runner.run_cycle()
    assert summary.fetched == 2
    assert summary.published == 2
    assert "over" not in repo.products
    assert repo.get_published_ids() == {"at-limit", "under"}

