"""Tests for Telegram-to-Instagram-Story workflow with URL consistency and AI highlight selection.

Per user requirements:
- Product published to Telegram channel first.
- Capture and store Telegram message URL against Product.
- Instagram Story uses exact Telegram message URL as Link Sticker destination.
- AI Highlight Selection with ChatGPT choosing existing or new Highlight.
- StoryJob tracks product_id, instagram_account_id, highlight_name, story_id, status.
- Failure handling:
  - Telegram fails -> STOP, do not create Instagram story.
  - Telegram succeeds, Instagram fails -> Telegram SUCCESS, Instagram FAILED (retryable).
  - Story published, Highlight fails -> Story PUBLISHED, Highlight FAILED (retry only Highlight).
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from adapters.base import RawProduct
from config.app_config import AppConfig
from core.composer import ComposedPost
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from llm.base import HighlightSelectionResult
from publishers.base import PublishResult
from publishers.playwright_story_worker import PlaywrightStoryWorker
from storage.models import ProductRecord, StoryJobRecord
from tests.conftest import FakeLLMProvider, FakeProductRepository, FakePublisher


class _MockPrompt:
    def load_prompt(self) -> str:
        return "stylish copy prompt"


def test_telegram_url_saved_against_product(sample_products: list[RawProduct]) -> None:
    repo = FakeProductRepository()
    product = sample_products[0]
    repo.upsert_new(product)

    class _TelegramWithLink(FakePublisher):
        def publish(self, post: ComposedPost) -> PublishResult:
            return PublishResult(
                success=True,
                platform_post_id="tg_1234",
                links=("https://t.me/fashion_channel/1234",),
            )

    telegram = _TelegramWithLink("telegram")
    instagram = FakePublisher("instagram")

    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[telegram, instagram],
        config=AppConfig(),
        prompt_loader=_MockPrompt(),
    )

    summary = runner.run_cycle()
    assert summary.published == 1

    stored = repo.get_by_external_id(product.external_id)
    assert stored is not None
    assert stored.telegram_message_id == "tg_1234"
    assert stored.telegram_message_url == "https://t.me/fashion_channel/1234"
    assert stored.telegramMessageUrl == "https://t.me/fashion_channel/1234"
    assert stored.telegramPublishedAt is not None


def test_ai_highlight_selection_existing_and_create() -> None:
    llm = FakeLLMProvider()

    # Product matching existing highlight
    sneaker_prod = {
        "name": "Nike Air Max 95",
        "category": "Sneakers",
        "description": "Iconic running shoes",
    }
    existing = ["New Arrivals", "Sneakers", "T-Shirts", "Accessories", "Sale"]
    res1 = llm.select_highlight(sneaker_prod, existing)
    assert res1.highlight == "Sneakers"
    assert res1.selected_highlight_name == "Sneakers"
    assert res1.confidence >= 0.90

    # Product without suitable highlight
    dress_prod = {
        "name": "Summer Floral Dress",
        "category": "Dresses",
        "description": "Lightweight linen summer dress",
    }
    res2 = llm.select_highlight(dress_prod, ["Sneakers", "T-Shirts"])
    assert res2.create_highlight is True
    assert res2.suggested_name == "Dresses"
    assert res2.selected_highlight_name == "Dresses"


def test_story_worker_uses_exact_telegram_url(tmp_path: Path) -> None:
    repo = FakeProductRepository()
    prod_rec = ProductRecord(
        id=42,
        external_id="zara-999",
        source="zara",
        title="Nike Air Max 95",
        price_original=Decimal("120.00"),
        currency_original="USD",
        photo_url="https://example.com/p1.jpg",
        status="selected",
        telegram_message_url="https://t.me/example_channel/1234",
    )
    repo.products["zara-999"] = {
        "id": 42,
        "external_id": "zara-999",
        "source": "zara",
        "title": "Nike Air Max 95",
        "price": Decimal("120.00"),
        "currency": "USD",
        "status": "selected",
        "telegram_message_url": "https://t.me/example_channel/1234",
    }

    mock_client = MagicMock()
    mock_client.publish_linked_story.return_value = "story_pk_777"
    mock_client.add_story.return_value = "hl_id_888"

    worker = PlaywrightStoryWorker(
        repo=repo,
        llm=FakeLLMProvider(),
        account_id="invito.live",
        highlight_client=mock_client,
    )

    img = tmp_path / "test.jpg"
    img.write_bytes(b"image-content")

    # Mock render_story_collage to return valid image path
    with patch("publishers.playwright_story_worker.render_story_collage", return_value=img):
        ok, msg, story_id = worker.process_story_job(
            product_id=42,
            prepared_images=[img],
            price_label="120$",
        )

    assert ok is True
    assert story_id == "story_pk_777"

    # Verify link sticker destination is EXACT Telegram URL
    mock_client.publish_linked_story.assert_called_once()
    call_kwargs = mock_client.publish_linked_story.call_args.kwargs
    assert call_kwargs["link_url"] == "https://t.me/example_channel/1234"

    # Verify story job recorded in DB
    jobs = repo.get_story_jobs(product_id=42)
    assert len(jobs) == 1
    assert jobs[0].status == "completed"
    assert jobs[0].story_id == "story_pk_777"
    assert jobs[0].productId == 42


def test_telegram_failure_stops_before_instagram_story(sample_products: list[RawProduct]) -> None:
    repo = FakeProductRepository()
    product = sample_products[0]
    repo.upsert_new(product)

    telegram = FakePublisher("telegram", should_fail=True)
    instagram = FakePublisher("instagram")

    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[telegram, instagram],
        config=AppConfig(),
        prompt_loader=_MockPrompt(),
    )

    summary = runner.run_cycle()
    assert summary.failed == 1
    assert summary.published == 0

    # Invariant: Instagram was never called because Telegram failed!
    assert len(telegram.published_posts) == 0
    assert len(instagram.published_posts) == 0

    # No story jobs created
    assert len(repo.get_story_jobs()) == 0


def test_highlight_failure_keeps_story_and_retries_only_highlight(tmp_path: Path) -> None:
    repo = FakeProductRepository()
    repo.products["item-1"] = {
        "id": 10,
        "external_id": "item-1",
        "source": "zara",
        "title": "Silk Blouse",
        "price": Decimal("80.00"),
        "currency": "USD",
        "status": "selected",
        "telegram_message_url": "https://t.me/channel/555",
    }

    mock_client = MagicMock()
    mock_client.publish_linked_story.return_value = "story_999"
    # Highlight addition fails on attempt 1
    mock_client.add_story.side_effect = RuntimeError("Instagram Highlight API temporary rate limit")

    worker = PlaywrightStoryWorker(
        repo=repo,
        llm=FakeLLMProvider(),
        account_id="invito.live",
        highlight_client=mock_client,
    )

    img = tmp_path / "blouse.jpg"
    img.write_bytes(b"blouse-image")

    with patch("publishers.playwright_story_worker.render_story_collage", return_value=img):
        # Attempt 1: Story publishes, Highlight fails
        ok1, msg1, story_id1 = worker.process_story_job(10, [img])

    assert ok1 is False
    assert story_id1 == "story_999"
    jobs = repo.get_story_jobs(product_id=10)
    assert len(jobs) == 1
    assert jobs[0].status == "highlight_failed"
    assert jobs[0].story_id == "story_999"

    # Attempt 2: Retry
    # Invariant: Do NOT publish another Story! Retry ONLY the Highlight operation!
    mock_client.publish_linked_story.reset_mock()
    mock_client.add_story.reset_mock()
    mock_client.add_story.side_effect = None
    mock_client.add_story.return_value = "hl_reel_123"

    ok2, msg2, story_id2 = worker.process_story_job(10, [img])
    assert ok2 is True
    assert story_id2 == "story_999"

    # Verify story was NOT published a second time
    mock_client.publish_linked_story.assert_not_called()
    # Verify add_story was called
    mock_client.add_story.assert_called_once()
    assert jobs[0].status == "completed"
