"""Integration test for full pipeline cycle in dry-run mode against SQLite repository.

Per SDD §9.
"""

from decimal import Decimal
from adapters.aggregator_adapter import AggregatorAPIAdapter
from adapters.aggregator_client import MockAggregatorClient
from config.app_config import AppConfig
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from llm.openai_provider import FilePromptLoader, OpenAIProvider
from publishers.dry_run_publisher import DryRunPublisher
from publishers.instagram_publisher import InstagramPublisher
from publishers.telegram_publisher import TelegramPublisher
from storage.repository import SqlAlchemyProductRepository


def test_dry_run_cycle_end_to_end(tmp_path) -> None:
    """Run full cycle in dry-run mode and verify DB status transitions."""
    db_file = tmp_path / "test_app.db"
    prompt_file = tmp_path / "test_prompt.txt"
    prompt_file.write_text("Curate fashion items.", encoding="utf-8")

    db_url = f"sqlite:///{db_file}"
    repo = SqlAlchemyProductRepository(db_url)

    source = AggregatorAPIAdapter(MockAggregatorClient(), stores=["zara", "mango"])
    llm = OpenAIProvider(api_key="mock-key")
    prompt_loader = FilePromptLoader(prompt_file)
    fx = FixedRateConverter(fixed_rate=Decimal("1.08"))

    tg_pub = DryRunPublisher(TelegramPublisher("mock-token", "@mock_chan"))
    ig_pub = DryRunPublisher(InstagramPublisher("mock-token", "mock_acc"))

    config = AppConfig(
        db_url=db_url,
        prompt_path=str(prompt_file),
        dry_run=True,
        markup=Decimal("15.00"),
        max_products_per_run=2,
    )

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[tg_pub, ig_pub],
        config=config,
        prompt_loader=prompt_loader,
    )

    summary = runner.run_cycle()

    assert summary.fetched == 5
    assert summary.unseen == 5
    assert summary.selected == 2
    assert summary.published == 2
    assert summary.failed == 0

    # Verify database persistence
    published_ids = repo.get_published_ids()
    assert len(published_ids) == 2

    for ext_id in published_ids:
        rec = repo.get_by_external_id(ext_id)
        assert rec is not None
        assert rec.status == "published"
        assert rec.price_final is not None
        assert rec.price_final > rec.price_original
        assert rec.description_gpt is not None
        assert rec.telegram_post_id is not None
        assert "DRYRUN_TELEGRAM" in rec.telegram_post_id
        assert rec.instagram_post_id is not None
        assert "DRYRUN_INSTAGRAM" in rec.instagram_post_id
        assert rec.published_at is not None
