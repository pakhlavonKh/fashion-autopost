from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from adapters.base import RawProduct
from adapters.playwright_url_processor import clean_url_parameters, process_product_url_with_playwright
from core.description_rules import HighHeelDescriptionRule, apply_description_rules
from core.outfits import OutfitCoordinator
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from core.similarity import analyze_channel_history, rank_products_by_channel_similarity, score_product_similarity
from storage.models import ProductRecord
from storage.repository import SqlAlchemyProductRepository
from tests.conftest import FakeProductRepository, FakePublisher, FakeSourceAdapter


def _make_raw_product(
    external_id: str,
    title: str = "Test Product",
    source: str = "zara",
    price: float = 40.0,
    currency: str = "EUR",
    original_url: str = "https://www.zara.com/es/es/product-123.html?utm_source=fb",
    heel_height: str | None = None,
) -> RawProduct:
    return RawProduct(
        external_id=external_id,
        source=source,
        title=title,
        price=Decimal(str(price)),
        currency=currency,
        photo_url="https://example.com/img1.jpg",
        product_url=original_url,
        in_stock=True,
        photo_urls=["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
        original_product_url=original_url,
        heel_height=heel_height,
    )


# --- 1. Playwright URL Processing & Traceability ---
def test_clean_url_parameters_strips_tracking() -> None:
    url = "https://www.zara.com/item.html?utm_source=newsletter&utm_medium=email&id=12345&fbclid=xyz"
    cleaned = clean_url_parameters(url)
    assert "utm_source" not in cleaned
    assert "fbclid" not in cleaned
    assert "id=12345" in cleaned


def test_process_product_url_fallback() -> None:
    url = "https://www.mango.com/dress.html?gclid=abc12345"
    cleaned, _ = process_product_url_with_playwright(url, timeout_seconds=0.1)
    assert "gclid" not in cleaned
    assert "mango.com/dress.html" in cleaned


# --- 2. Brand Pause in Storage & Selection ---
def test_brand_pause_and_unpause_in_repo(tmp_path: Path) -> None:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    assert repo.is_brand_paused("Massimo Dutti") is False

    repo.set_brand_paused("Massimo Dutti", True)
    assert repo.is_brand_paused("Massimo Dutti") is True
    assert "massimo dutti" in repo.get_paused_brands()

    brands = repo.get_brand_settings()
    md = next((b for b in brands if b["name"].lower() == "massimo dutti"), None)
    assert md is not None
    assert md["is_paused"] is True
    assert md["status"] == "Paused"

    repo.set_brand_paused("Massimo Dutti", False)
    assert repo.is_brand_paused("Massimo Dutti") is False


def test_paused_brand_products_excluded_from_selection(tmp_path: Path) -> None:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    p_zara = _make_raw_product("z1", source="zara")
    p_md = _make_raw_product("md1", source="massimo dutti")
    repo.upsert_new(p_zara)
    repo.upsert_new(p_md)

    # Pause Massimo Dutti
    repo.set_brand_paused("massimo dutti", True)

    unposted = repo.get_unposted_products(10)
    paused_brands = repo.get_paused_brands()
    active_unposted = [p for p in unposted if p.source.lower() not in paused_brands]
    sources = [p.source.lower() for p in active_unposted]
    assert "zara" in sources
    assert "massimo dutti" not in sources


# --- 3. High-Heel Shoes Description Rule ---
def test_high_heel_description_rule_extraction() -> None:
    rule = HighHeelDescriptionRule()
    p1 = _make_raw_product("h1", title="Leather High-Heel Sandals", heel_height="8.5 cm")
    res1 = rule.apply(p1, "Beautiful sandals\nPrice: $60")
    assert "Heel height: 8.5 cm" in res1

    # Rule without heel height should not invent a value
    p2 = _make_raw_product("h2", title="High Heel Pumps", heel_height=None)
    res2 = rule.apply(p2, "Pumps\nPrice: $70")
    assert "Heel height" not in res2


def test_high_heel_rule_parses_description_text() -> None:
    rule = HighHeelDescriptionRule()
    p = RawProduct(
        external_id="h3",
        source="zara",
        title="Stiletto high heels",
        price=Decimal("50.0"),
        currency="EUR",
        photo_url="https://example.com/shoes.jpg",
        product_url="https://example.com/shoes",
        in_stock=True,
    )
    desc = "Fabulous evening stiletto shoes. Features a 10 cm heel for an elegant silhouette."
    res = rule.apply(p, desc)
    assert "Heel height: 10 cm" in res

    # apply_description_rules pipeline function
    full_res = apply_description_rules(p, desc)
    assert "Heel height: 10 cm" in full_res


# --- 4. Maximum Product Price Setting ---
def test_max_product_price_setting_in_repo(tmp_path: Path) -> None:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    assert repo.get_system_setting("max_source_price_usd") is None

    repo.set_system_setting("max_source_price_usd", "60.0")
    assert repo.get_system_setting("max_source_price_usd") == "60.0"


def test_pipeline_filters_products_by_dynamic_max_price(tmp_path: Path) -> None:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    # Set max price to $50
    repo.set_system_setting("max_source_price_usd", "50.0")

    p_cheap = _make_raw_product("c1", price=30.0, currency="USD")
    p_expensive = _make_raw_product("e1", price=70.0, currency="USD")
    repo.upsert_new(p_cheap)
    repo.upsert_new(p_expensive)

    from config.app_config import AppConfig
    from tests.conftest import FakeLLMProvider

    class _MockPromptLoader:
        def load_prompt(self) -> str:
            return "Curate fashion."

    telegram_pub = FakePublisher("telegram")
    instagram_pub = FakePublisher("instagram")

    runner = PipelineRunner(
        source=FakeSourceAdapter([p_cheap, p_expensive]),
        repo=repo,
        llm=FakeLLMProvider(select_count=2),
        fx=FixedRateConverter(fixed_rate=Decimal("1.0")),
        publishers=[telegram_pub, instagram_pub],
        config=AppConfig(),
        prompt_loader=_MockPromptLoader(),
    )

    # Effective max price should be 50.0 from repo system settings
    assert runner.get_effective_max_source_price() == Decimal("50.0")

    # _split_by_source_price should keep cheap product ($30) and reject expensive product ($70)
    kept, rejected = runner._split_by_source_price([p_cheap, p_expensive])
    assert [p.external_id for p in kept] == ["c1"]
    assert [p.external_id for p in rejected] == ["e1"]


# --- 8. Analyze Existing Telegram Channel Products (Similarity) ---
def test_channel_history_profiler_and_similarity(tmp_path: Path) -> None:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    # Insert published products
    p1 = _make_raw_product("pub1", title="Summer Floral Dress", source="zara", price=45.0)
    p2 = _make_raw_product("pub2", title="Maxi Linen Dress", source="zara", price=55.0)
    repo.upsert_new(p1)
    repo.upsert_new(p2)
    repo.save_telegram_publication("pub1", "100", "https://t.me/c/1/100")
    repo.mark_published("pub1", "100", None)
    repo.save_telegram_publication("pub2", "101", "https://t.me/c/1/101")
    repo.mark_published("pub2", "101", None)

    profile = analyze_channel_history(repo, limit=50)

    assert profile.total_sample == 2
    assert "dress" in profile.top_keywords or "summer" in profile.top_keywords

    # Score candidates
    cand_similar = _make_raw_product("cand1", title="Boho Maxi Dress", source="zara", price=50.0)
    cand_unrelated = _make_raw_product("cand2", title="Unrelated Winter Wool Overcoat", source="nike", price=120.0)

    score_sim = score_product_similarity(cand_similar, profile)
    score_unrel = score_product_similarity(cand_unrelated, profile)

    assert score_sim > score_unrel

    # Test ranking with pause & price filters
    repo.set_brand_paused("nike", True)
    ranked = rank_products_by_channel_similarity([cand_similar, cand_unrelated], repo, max_price_usd=Decimal("100.0"))
    assert len(ranked) == 1
    assert ranked[0][0].external_id == "cand1"


# --- 9. Publish Product Combinations as Outfits/Looks ---
def test_outfit_look_combination_assembly() -> None:
    items = [
        _make_raw_product("t1", title="Cotton Knit Sweater", price=30.0),
        _make_raw_product("b1", title="Wide Leg Trousers", price=40.0),
        _make_raw_product("s1", title="Leather Loafers Shoes", price=50.0),
        _make_raw_product("d1", title="Summer Cocktail Dress", price=60.0),
        _make_raw_product("a1", title="Leather Handbag Accessory", price=20.0),
    ]

    outfit = OutfitCoordinator.find_coordinated_outfit(items)

    assert outfit is not None
    assert len(outfit.items) in (2, 3)
    assert outfit.outfit_id.startswith("outfit_")
    assert len(outfit.positions) == len(outfit.items)


# --- 10. Duplicate Publication Warning and Admin Approval ---
def test_duplicate_publication_detection_and_approval_flow(tmp_path: Path) -> None:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    p = _make_raw_product("dup1", title="Silk Blouse", original_url="https://zara.com/blouse.html")
    repo.upsert_new(p)
    repo.save_telegram_publication("dup1", "555", "https://t.me/c/1/555")
    repo.mark_published("dup1", "555", None)

    # Create duplicate approval record
    appr = repo.create_duplicate_approval(
        external_id="dup1",
        title="Silk Blouse",
        source="zara",
        telegram_url="https://t.me/c/1/555",
        original_published_at=datetime.now(timezone.utc),
    )
    assert appr.status == "pending"

    # Admin approves
    repo.resolve_duplicate_approval(appr.id, "approved")
    updated = repo.get_duplicate_approval(appr.id)
    assert updated is not None
    assert updated.status == "approved"

    # Admin rejects another
    appr2 = repo.create_duplicate_approval(
        external_id="dup2",
        title="Wool Scarf",
        source="zara",
        telegram_url="https://t.me/c/1/556",
    )
    repo.resolve_duplicate_approval(appr2.id, "rejected")
    updated2 = repo.get_duplicate_approval(appr2.id)
    assert updated2 is not None
    assert updated2.status == "rejected"


# --- 5 & 6. Separate Telegram and Instagram Publishing Schedules with Randomization ---
def test_generate_schedule_times() -> None:
    from scheduler.build_scheduler import generate_schedule_times

    # Telegram: 06:00 to 23:00 hourly (6, 7, 8, ... 23 = 18 slots)
    tg_times = generate_schedule_times("06:00", "23:00", 60)
    assert len(tg_times) == 18
    assert tg_times[0] == "06:00"
    assert tg_times[-1] == "23:00"

    # Instagram: 06:00 to 21:00 every 3 hours (6, 9, 12, 15, 18, 21 = 6 slots)
    ig_times = generate_schedule_times("06:00", "21:00", 180)
    assert ig_times == ["06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]


def test_independent_schedules_registration(tmp_path: Path) -> None:
    from apscheduler.schedulers.background import BackgroundScheduler
    from config.app_config import AppConfig, ScheduleSettings
    from scheduler.build_scheduler import register_schedule_jobs
    from tests.conftest import FakeLLMProvider

    class _MockPromptLoader:
        def load_prompt(self) -> str:
            return "Curate fashion."

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    runner = PipelineRunner(
        source=FakeSourceAdapter([]),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[FakePublisher("telegram"), FakePublisher("instagram")],
        config=AppConfig(),
        prompt_loader=_MockPromptLoader(),
    )

    scheduler = BackgroundScheduler()
    schedule_cfg = ScheduleSettings(timezone="UTC")

    register_schedule_jobs(scheduler, runner, schedule_cfg)

    jobs = scheduler.get_jobs()
    tg_jobs = [j for j in jobs if j.id.startswith("tg_sched_")]
    ig_jobs = [j for j in jobs if j.id.startswith("ig_sched_")]

    assert len(tg_jobs) == 18  # 06:00 to 23:00 hourly
    assert len(ig_jobs) == 6   # 06:00 to 21:00 every 3 hours
    assert not any(job.id.startswith("pipeline_cycle_cron_") for job in jobs)


def test_yaml_clock_does_not_publish_instagram_every_hour(tmp_path: Path) -> None:
    from apscheduler.schedulers.background import BackgroundScheduler
    from config.app_config import AppConfig, ScheduleSettings
    from scheduler.build_scheduler import register_schedule_jobs
    from tests.conftest import FakeLLMProvider

    class _MockPromptLoader:
        def load_prompt(self) -> str:
            return "Curate fashion."

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'sched.db'}")
    runner = PipelineRunner(
        source=FakeSourceAdapter([]),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[FakePublisher("telegram"), FakePublisher("instagram")],
        config=AppConfig(),
        prompt_loader=_MockPromptLoader(),
    )
    scheduler = BackgroundScheduler()
    register_schedule_jobs(
        scheduler,
        runner,
        ScheduleSettings(
            timezone="Asia/Tashkent",
            times=["06:00", "07:00", "08:00", "09:00"],
            interval_minutes=None,
        ),
    )
    jobs = scheduler.get_jobs()
    assert any(job.id.startswith("ig_sched_") for job in jobs)
    assert not any(job.id.startswith("pipeline_cycle_cron_") for job in jobs)
    ig_jobs = [job for job in jobs if job.id.startswith("ig_sched_")]
    assert [job.id for job in ig_jobs] == [
        "ig_sched_0600",
        "ig_sched_0900",
        "ig_sched_1200",
        "ig_sched_1500",
        "ig_sched_1800",
        "ig_sched_2100",
    ]


def test_instagram_cycle_posts_the_telegram_product(tmp_path: Path) -> None:
    from config.app_config import AppConfig
    from tests.conftest import FakeLLMProvider

    class _MockPromptLoader:
        def load_prompt(self) -> str:
            return "Curate fashion."

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'ig.db'}")
    product = _make_raw_product(
        "mango-boot",
        title="Ботильоны",
        source="mango",
        original_url="https://example.com/ankle-boot",
    )
    repo.upsert_new(product)
    repo.mark_published(product.external_id, "@fashionalleyb:88", None)
    repo.save_telegram_publication(
        product.external_id,
        "@fashionalleyb:88",
        "https://t.me/fashionalleyb/88",
    )
    instagram = FakePublisher("instagram")
    telegram = FakePublisher("telegram")
    runner = PipelineRunner(
        source=FakeSourceAdapter([_make_raw_product("other-shoe", original_url="https://example.com/other")]),
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(),
        publishers=[telegram, instagram],
        config=AppConfig(),
        prompt_loader=_MockPromptLoader(),
    )
    runner.image_downloader.download_all = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
    summary = runner.run_instagram_cycle()
    assert summary.published == 1
    assert len(instagram.published_posts) == 1
    assert instagram.published_posts[0].telegram_links == ("https://t.me/fashionalleyb/88",)
    assert telegram.published_posts == []
    saved = repo.get_by_external_id("mango-boot")
    assert saved is not None
    assert saved.instagram_post_id
    assert saved.telegram_post_id == "@fashionalleyb:88"


# --- 7. Mobile-Responsive Admin Panel ---
def test_mobile_responsive_css_and_viewport() -> None:
    html_file = Path("dashboard/static/index.html")
    assert html_file.exists()
    html_content = html_file.read_text(encoding="utf-8")
    # Verify proper viewport meta tag
    assert 'name="viewport"' in html_content
    assert "width=device-width" in html_content

    css_file = Path("dashboard/static/style.css")
    assert css_file.exists()
    css_content = css_file.read_text(encoding="utf-8")
    # Verify responsive media queries for tablet and smartphone
    assert "@media (max-width: 768px)" in css_content
    assert "@media (max-width: 480px)" in css_content
    # Verify touch targets and overflow handling
    assert "overflow-x: auto" in css_content
    assert "min-height: 44px" in css_content


# --- Dashboard API Endpoints ---
def test_dashboard_brand_pause_and_settings_endpoints(tmp_path: Path) -> None:
    from starlette.testclient import TestClient
    from config.app_config import AppConfig
    from dashboard.server import create_dashboard_app

    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'test.db'}")
    config = AppConfig()
    runner = MagicMock()
    runner.publish_approved_duplicate.return_value = (True, "Published approved duplicate")
    runner.publish_next_eligible_product.return_value = (True, "Published next product")
    app = create_dashboard_app(config=config, runner=runner, repo=repo)
    client = TestClient(app)

    headers = {"X-Admin-Key": config.dashboard.admin_key}

    # 1. Brands API
    resp = client.get("/api/brands", headers=headers)
    assert resp.status_code == 200

    pause_resp = client.post("/api/brands/zara/pause", json={"is_paused": True}, headers=headers)
    assert pause_resp.status_code == 200
    assert pause_resp.json()["is_paused"] is True
    assert repo.is_brand_paused("zara") is True

    # 2. Settings API
    set_resp = client.post("/api/settings", json={"max_source_price_usd": 60.0}, headers=headers)
    assert set_resp.status_code == 200
    assert repo.get_system_setting("max_source_price_usd") == "60.0"

    get_resp = client.get("/api/settings", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["max_source_price_usd"] == 60.0

    # 3. Duplicate Approvals API
    appr = repo.create_duplicate_approval("dup_test", "Test Dress", "zara")
    dups_resp = client.get("/api/duplicate-approvals", headers=headers)
    assert dups_resp.status_code == 200
    assert len(dups_resp.json()) >= 1

    resolve_resp = client.post(f"/api/duplicate-approvals/{appr.id}/resolve", json={"status": "approved"}, headers=headers)
    assert resolve_resp.status_code == 200
    assert repo.get_duplicate_approval(appr.id).status == "approved"

