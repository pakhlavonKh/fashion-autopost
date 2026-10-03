"""Unit tests for PipelineRunner orchestration.

Per SDD §2.2, §5, §6 and §9.
Validates per-product error isolation, moderation gates, daily caps, and admin notification.
"""

from dataclasses import replace
from decimal import Decimal
import pytest

from adapters.base import RawProduct
from config.app_config import AppConfig
from core.moderation import ConfigurableModerationGate
from core.pipeline import CycleSummary, PipelineRunner
from core.pricing import FixedRateConverter
from llm.base import SelectionResult
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
        color="белый",
        sizes=("S", "M", "L"),
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
        color="бежевый",
        sizes=("XS", "S", "M"),
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


def _blocked(external_id: str, title: str, product_url: str | None = None) -> RawProduct:
    """A candidate whose store page answered with a bot wall: no sizes, no colour."""
    return RawProduct(
        external_id=external_id,
        source="zara",
        title=title,
        price=Decimal("49.00"),
        currency="USD",
        photo_url=f"https://images.example.com/{external_id}.jpg",
        product_url=f"https://zara.com/{external_id}" if product_url is None else product_url,
        in_stock=True,
    )


def _runner(repo: FakeProductRepository, products: list[RawProduct], pub: FakePublisher) -> PipelineRunner:
    return PipelineRunner(
        source=FakeSourceAdapter(products),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(fixed_rate=Decimal("1.0")),
        publishers=[pub],
        config=AppConfig(max_products_per_run=1),
        prompt_loader=MockPromptLoader(),
    )


def test_a_product_without_a_size_grid_is_never_posted() -> None:
    """A caption of nothing but the title and the price must not reach the channel."""
    repo = FakeProductRepository()
    pub = FakePublisher("telegram")
    blocked = _blocked("blocked-1", "Пиджак из шерсти")

    summary = _runner(repo, [blocked], pub).run_cycle()

    assert pub.published_posts == []
    assert summary.published == 0
    assert repo.products["blocked-1"]["status"] == "failed"
    assert summary.errors


def test_a_blocked_store_page_hands_the_slot_to_the_next_candidate(
    sample_products: list[RawProduct],
) -> None:
    """One unreadable page must not cost the channel its scheduled post."""
    repo = FakeProductRepository()
    pub = FakePublisher("telegram")
    blocked = _blocked("blocked-1", "Пиджак из шерсти")
    readable = sample_products[1]

    summary = _runner(repo, [blocked, readable], pub).run_cycle()

    assert summary.published == 1
    assert repo.get_published_ids() == {readable.external_id}
    assert repo.products["blocked-1"]["status"] == "failed"
    caption = pub.published_posts[0].text
    assert "Размеры от S до L." in caption


def test_instagram_reuses_the_facts_telegram_already_published() -> None:
    """The second platform must not lose the lines when the store blocks it later."""
    repo = FakeProductRepository()
    blocked = _blocked("blocked-1", "Пиджак из шерсти", product_url="")
    repo.upsert_new(blocked)
    repo.mark_selected(
        "blocked-1",
        "Размеры от XS до XL.\nЦвет: черный.",
        Decimal("89.00"),
    )
    pub = FakePublisher("telegram")

    summary = CycleSummary()
    _runner(repo, [blocked], pub)._process_single_product(blocked, "", summary)

    assert summary.published == 1
    assert "Размеры от XS до XL." in pub.published_posts[0].text


class _TranslatingLLM(FakeLLMProvider):
    """Stands in for the model that renames a foreign store title in Russian."""

    def select_products(
        self,
        candidates: list[RawProduct],
        prompt: str,
        max_items: int,
    ) -> list[SelectionResult]:
        return [
            SelectionResult(external_id=item.external_id, title="Куртка-бомбер", description="")
            for item in candidates[:max_items]
        ]


def test_publishing_straight_from_the_queue_still_names_the_garment_in_russian(
    sample_products: list[RawProduct],
) -> None:
    """«Опубликовать сейчас» skips the copy step, and the channel reads Russian."""
    repo = FakeProductRepository()
    pub = FakePublisher("telegram")
    foreign = replace(sample_products[1], title="BLOUSON BOMBER À PATTES")
    repo.upsert_new(foreign)

    runner = _runner(repo, [foreign], pub)
    runner.llm = _TranslatingLLM()
    published, _message = runner.publish_next_eligible_product()

    assert published
    assert pub.published_posts[0].title == "Куртка-бомбер"
    assert repo.products[foreign.external_id]["title"] == "Куртка-бомбер"


def test_a_title_the_model_already_wrote_in_russian_is_left_alone(
    sample_products: list[RawProduct],
) -> None:
    """A title that already reads in Russian must not cost a second model call."""
    repo = FakeProductRepository()
    pub = FakePublisher("telegram")
    product = sample_products[1]
    repo.upsert_new(product)

    runner = _runner(repo, [product], pub)
    runner.llm = _TranslatingLLM()
    summary = CycleSummary()
    runner._process_single_product(product, "", summary, title_override="Рубашка из поплина")

    assert pub.published_posts[0].title == "Рубашка из поплина"


class _RejectingCuratorLLM(FakeLLMProvider):
    """The curation prompt rejects busy prints; the plain translator still names them."""

    def __init__(self, translation: str | None) -> None:
        super().__init__()
        self.translation = translation
        self.translated: list[str] = []

    def select_products(
        self,
        candidates: list[RawProduct],
        prompt: str,
        max_items: int,
    ) -> list[SelectionResult]:
        return []

    def translate_title(self, title: str) -> str | None:
        self.translated.append(title)
        return self.translation


def test_a_product_the_curator_rejects_still_gets_a_russian_title(
    sample_products: list[RawProduct],
) -> None:
    """Zara «imprimé fleuri» went out in French: the curation prompt chose 0 items."""
    repo = FakeProductRepository()
    pub = FakePublisher("telegram")
    foreign = replace(sample_products[1], title="CHEMISE BALLON À IMPRIMÉ FLEURI")
    repo.upsert_new(foreign)

    runner = _runner(repo, [foreign], pub)
    llm = _RejectingCuratorLLM("Рубашка-баллон с цветочным принтом")
    runner.llm = llm
    summary = CycleSummary()
    runner._process_single_product(foreign, "", summary)

    assert llm.translated == ["CHEMISE BALLON À IMPRIMÉ FLEURI"]
    assert pub.published_posts[0].title == "Рубашка-баллон с цветочным принтом"


class _TranslatorDownLLM(_TranslatingLLM):
    def translate_title(self, title: str) -> str | None:
        raise RuntimeError("translator unavailable")


def test_when_the_plain_translator_fails_the_curation_call_is_the_fallback(
    sample_products: list[RawProduct],
) -> None:
    repo = FakeProductRepository()
    pub = FakePublisher("telegram")
    foreign = replace(sample_products[1], title="BLOUSON BOMBER À PATTES")
    repo.upsert_new(foreign)

    runner = _runner(repo, [foreign], pub)
    runner.llm = _TranslatorDownLLM()
    summary = CycleSummary()
    runner._process_single_product(foreign, "", summary)

    assert pub.published_posts[0].title == "Куртка-бомбер"

