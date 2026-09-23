"""Integration test for full publishing cycle using PlaywrightScraperAdapter.

Per SDD §9.
Validates end-to-end pipeline execution from Playwright web scraping through
deduplication, LLM selection, pricing calculation, dry-run publishing, and DB persistence.
"""

from decimal import Decimal
from pathlib import Path
from adapters.playwright_adapter import PlaywrightScraperAdapter
from config.app_config import AppConfig, ScraperSettings, ScraperStoreConfig
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from llm.openai_provider import FilePromptLoader, OpenAIProvider
from publishers.dry_run_publisher import DryRunPublisher
from publishers.instagram_publisher import InstagramPublisher
from publishers.telegram_publisher import TelegramPublisher
from storage.repository import SqlAlchemyProductRepository


def test_playwright_pipeline_end_to_end(tmp_path: Path) -> None:
    """Execute end-to-end pipeline using Playwright against local HTML fixture."""
    # 1. Create a local HTML page with fashion products
    html_file = tmp_path / "fashion_catalog.html"
    html_file.write_text(
        """
        <!DOCTYPE html>
        <html>
        <body>
            <ul class="product-grid__product-list">
                <li class="product-grid-product" data-product-id="pw-101">
                    <a class="product-link" href="https://www.zara.com/sample/dress-101.html">
                        <div class="product-grid-product-info">
                            <h2 class="product-grid-product-info__name">Emerald Satin Midi Dress</h2>
                            <span class="money-amount__main">99,95 €</span>
                        </div>
                    </a>
                    <img class="media-image__image" src="https://static.zara.net/dress101.jpg" alt="Emerald Satin Dress" />
                </li>
                <li class="product-grid-product" data-product-id="pw-102">
                    <a class="product-link" href="https://www.zara.com/sample/coat-102.html">
                        <div class="product-grid-product-info">
                            <h2 class="product-grid-product-info__name">Oversized Cashmere Trench Coat</h2>
                            <span class="money-amount__main">189,00 €</span>
                        </div>
                    </a>
                    <img class="media-image__image" src="https://static.zara.net/coat102.jpg" alt="Cashmere Coat" />
                </li>
            </ul>
        </body>
        </html>
        """,
        encoding="utf-8",
    )

    file_url = html_file.as_uri()

    # 2. Setup SQLite DB and prompt file
    db_file = tmp_path / "test_pw_app.db"
    prompt_file = tmp_path / "test_pw_prompt.txt"
    prompt_file.write_text("Select stylish fashion items and write captivating captions.", encoding="utf-8")

    db_url = f"sqlite:///{db_file}"
    repo = SqlAlchemyProductRepository(db_url)

    # 3. Configure PlaywrightScraperAdapter pointing to local file URL
    scraper_config = ScraperSettings(
        headless=True,
        timeout_seconds=15.0,
        stores={
            "zara": ScraperStoreConfig(
                enabled=True,
                url=file_url,
                currency="EUR",
                max_items=5,
            )
        },
    )

    source = PlaywrightScraperAdapter(config=scraper_config, selected_stores=["zara"])
    llm = OpenAIProvider(api_key="mock-openai-key")
    prompt_loader = FilePromptLoader(prompt_file)
    fx = FixedRateConverter(fixed_rate=Decimal("1.08"))

    tg_pub = DryRunPublisher(TelegramPublisher("mock-bot-token", "@mock_fashion"))
    ig_pub = DryRunPublisher(InstagramPublisher("mock-ig-token", "mock_ig_account"))

    app_config = AppConfig(
        db_url=db_url,
        prompt_path=str(prompt_file),
        dry_run=True,
        markup=Decimal("20.00"),
        max_products_per_run=2,
    )

    runner = PipelineRunner(
        source=source,
        repo=repo,
        llm=llm,
        fx=fx,
        publishers=[tg_pub, ig_pub],
        config=app_config,
        prompt_loader=prompt_loader,
    )

    # 4. Run pipeline cycle
    summary = runner.run_cycle()

    assert summary.fetched == 2
    assert summary.unseen == 2
    assert summary.selected == 2
    assert summary.published == 2
    assert summary.failed == 0

    # 5. Verify database persistence
    published_ids = repo.get_published_ids()
    assert len(published_ids) == 2
    assert "zara-pw-101" in published_ids
    assert "zara-pw-102" in published_ids

    rec = repo.get_by_external_id("zara-pw-101")
    assert rec is not None
    assert rec.status == "published"
    assert rec.price_original == Decimal("99.95")
    assert rec.price_final is not None
    # 99.95 * 1.08 + 20.00 = 107.946 + 20.00 = 127.95
    assert rec.price_final == Decimal("127.95")
    assert rec.description_gpt is not None
    assert "DRYRUN_TELEGRAM" in rec.telegram_post_id
    assert "DRYRUN_INSTAGRAM" in rec.instagram_post_id
