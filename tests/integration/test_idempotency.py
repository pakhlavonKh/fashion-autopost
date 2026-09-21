"""Integration test for publication idempotency across cycles.

Per SDD §9 and SRS NFR-5.
Runs two identical pipeline cycles and asserts that no item is re-published twice.
"""

from decimal import Decimal
from adapters.aggregator_adapter import AggregatorAPIAdapter
from adapters.aggregator_client import MockAggregatorClient
from config.app_config import AppConfig
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from llm.openai_provider import FilePromptLoader, OpenAIProvider
from publishers.dry_run_publisher import DryRunPublisher
from publishers.telegram_publisher import TelegramPublisher
from storage.repository import SqlAlchemyProductRepository


def test_idempotency_no_duplicate_publishing(tmp_path) -> None:
    """Run two consecutive cycles against the same product source.
    
    First run must publish the items.
    Second run must identify all items as already published and publish 0 items.
    """
    db_file = tmp_path / "idempotency.db"
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Curate fashion.", encoding="utf-8")

    db_url = f"sqlite:///{db_file}"
    repo = SqlAlchemyProductRepository(db_url)
    source = AggregatorAPIAdapter(MockAggregatorClient(), stores=["zara", "mango"])
    llm = OpenAIProvider(api_key="mock-key")
    prompt_loader = FilePromptLoader(prompt_file)
    fx = FixedRateConverter()
    pub = DryRunPublisher(TelegramPublisher("mock-token", "@mock"))

    # Config allowing all 5 mock items to be published
    config = AppConfig(
        db_url=db_url,
        prompt_path=str(prompt_file),
        dry_run=True,
        markup=Decimal("10.00"),
        max_products_per_run=10,
    )

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[pub],
        config=config,
        prompt_loader=prompt_loader,
    )

    # First cycle: all 5 in-stock items are processed and published
    summary1 = runner.run_cycle()
    assert summary1.fetched == 5
    assert summary1.unseen == 5
    assert summary1.published == 5
    assert len(repo.get_published_ids()) == 5

    # Second cycle: all 5 items are already known; 0 items are published
    summary2 = runner.run_cycle()
    assert summary2.fetched == 5
    assert summary2.unseen == 0
    assert summary2.published == 0
    assert len(repo.get_published_ids()) == 5
