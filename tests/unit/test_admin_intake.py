"""Tests for admin product-link intake: time parsing, page parsing, and the bot dialogue."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import zoneinfo

import pytest

from adapters.base import RawProduct
from adapters.product_page import parse_product_html
from config.app_config import DEFAULT_TELEGRAM_ADMIN_USER_IDS, AppConfig, TelegramSettings
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from core.publish_time import parse_publish_time
from publishers.admin_intake_bot import AdminIntakeBot, extract_product_url
from storage.repository import SqlAlchemyProductRepository
from tests.conftest import FakeLLMProvider, FakeProductRepository, FakePublisher


TASHKENT = "Asia/Tashkent"
ADMIN_ID = 5532256714


def _now() -> datetime:
    return datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)  # 12:00 in Tashkent


def test_default_admins_are_the_three_allowed_accounts() -> None:
    assert TelegramSettings().admin_user_ids == DEFAULT_TELEGRAM_ADMIN_USER_IDS
    assert DEFAULT_TELEGRAM_ADMIN_USER_IDS == [5532256714, 333588697, 370255715]


def test_parse_publish_time_clock_date_and_now() -> None:
    same_day = parse_publish_time("18:30", _now(), TASHKENT)
    local = same_day.when.astimezone(zoneinfo.ZoneInfo(TASHKENT))
    assert (local.day, local.hour, local.minute) == (29, 18, 30)
    assert same_day.note == ""

    rolled = parse_publish_time("09:00", _now(), TASHKENT)
    rolled_local = rolled.when.astimezone(zoneinfo.ZoneInfo(TASHKENT))
    assert (rolled_local.day, rolled_local.hour) == (30, 9)
    assert "завтра" in rolled.note

    dated = parse_publish_time("29.09 18:30", _now(), TASHKENT)
    dated_local = dated.when.astimezone(zoneinfo.ZoneInfo(TASHKENT))
    assert (dated_local.day, dated_local.hour, dated_local.minute) == (29, 18, 30)

    tomorrow = parse_publish_time("завтра 10:15", _now(), TASHKENT)
    tomorrow_local = tomorrow.when.astimezone(zoneinfo.ZoneInfo(TASHKENT))
    assert (tomorrow_local.day, tomorrow_local.hour, tomorrow_local.minute) == (30, 10, 15)

    immediate = parse_publish_time("сейчас", _now(), TASHKENT)
    assert immediate.note == "сейчас"
    assert immediate.when == _now()

    with pytest.raises(ValueError):
        parse_publish_time("29.09 09:00", _now(), TASHKENT)
    with pytest.raises(ValueError):
        parse_publish_time("привет", _now(), TASHKENT)


def test_parse_product_html_json_ld_and_open_graph() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Wool coat","sku":"12345678",
     "image":["https://static.zara.net/photos/coat.jpg"],
     "offers":{"@type":"Offer","price":"89.95","priceCurrency":"EUR",
               "availability":"https://schema.org/InStock"}}
    </script>
    </head></html>
    """
    product = parse_product_html(html, "https://www.zara.com/es/es/wool-coat-p12345678.html")
    assert product is not None
    assert product.source == "zara"
    assert product.title == "Wool coat"
    assert product.price == Decimal("89.95")
    assert product.currency == "EUR"
    assert product.photo_url.endswith("coat.jpg")
    assert product.external_id == "zara-12345678"

    og = """
    <html><head>
      <meta property="og:title" content="Linen shirt | MANGO" />
      <meta property="og:image" content="https://media.mango.com/is/image/punto/1.jpg" />
      <meta property="product:price:amount" content="49.99" />
      <meta property="product:price:currency" content="GBP" />
    </head></html>
    """
    mango = parse_product_html(og, "https://shop.mango.com/gb/en/p/linen/87012345")
    assert mango is not None
    assert mango.title == "Linen shirt"
    assert mango.price == Decimal("49.99")
    assert mango.currency == "GBP"
    assert mango.source == "mango"

    assert parse_product_html("<html><title>Nope</title></html>", "https://example.com/p") is None


def test_parse_product_group_and_gallery() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"ProductGroup","name":"Mom Jeans",
     "hasVariant":[
       {"@type":"Product","sku":"1352386006002","name":"Mom Jeans - Beige",
        "image":"https://image.hm.com/assets/hm/aa/aa/one.jpg?imwidth=768",
        "offers":{"@type":"Offer","price":29.99,"priceCurrency":"EUR",
                  "url":"https://www2.hm.com/es_es/productpage.1352386006.html",
                  "availability":"https://schema.org/InStock"}},
       {"@type":"Product","sku":"1352386001002","name":"Mom Jeans - Black",
        "image":"https://image.hm.com/assets/hm/bb/bb/other.jpg",
        "offers":{"@type":"Offer","price":29.99,"priceCurrency":"EUR",
                  "url":"https://www2.hm.com/es_es/productpage.1352386001.html"}}
     ]}
    </script>
    </head><body>
    <script>{"productArticleDetails":{"articleCode":"1352386006","productName":"Mom Jeans","variations":{
      "1352386006":{"name":"Beige","whitePriceValue":29.99,"priceCurrency":"EUR","images":[
        {"baseUrl":"https://image.hm.com/assets/hm/aa/aa/one.jpg","assetType":"LOOKBOOK"},
        {"baseUrl":"https://image.hm.com/assets/hm/aa/aa/two.jpg","assetType":"LOOKBOOK"},
        {"baseUrl":"https://image.hm.com/assets/hm/aa/aa/swatch.jpg","assetType":"SWATCH"}
      ]}
    }}}</script>
    </body></html>
    """
    product = parse_product_html(html, "https://www2.hm.com/es_es/productpage.1352386006.html")
    assert product is not None
    assert product.source == "hm"
    assert product.price == Decimal("29.99")
    assert product.currency == "EUR"
    assert "Beige" in product.title
    assert product.external_id == "hm-1352386006"
    assert product.photo_urls[0].endswith("one.jpg")
    assert any(url.endswith("two.jpg") for url in product.photo_urls)
    assert all("swatch" not in url for url in product.photo_urls)
    assert all("other.jpg" not in url for url in product.photo_urls)


def test_extract_product_url_trims_trailing_punctuation() -> None:
    assert extract_product_url("смотри https://www.zara.com/es/dress-p1.html.") == (
        "https://www.zara.com/es/dress-p1.html"
    )
    assert extract_product_url("без ссылки") is None


class _Scheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger=None, id=None, args=None, **kwargs) -> None:
        self.jobs.append({"func": func, "id": id, "args": list(args or []), "trigger": trigger})


class _Runner:
    def __init__(self, ok: bool = True) -> None:
        self.urls: list[str] = []
        self.ok = ok

    def publish_manual_url(self, url: str) -> tuple[bool, str]:
        self.urls.append(url)
        if not self.ok:
            return False, "Не удалось опубликовать: канал недоступен"
        return True, "Пост опубликован: Wool coat\nЦена: 55.00 USD"


def _message(user_id: int, text: str, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": user_id, "first_name": "Admin"},
            "chat": {"id": user_id, "type": "private", "first_name": "Admin"},
            "text": text,
        },
    }


def _bot(tmp_path: Path, runner: _Runner | None = None) -> tuple[AdminIntakeBot, list[tuple[str, str]], _Scheduler, _Runner]:
    repo = SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'manual.db'}")
    scheduler = _Scheduler()
    runner = runner or _Runner()
    sent: list[tuple[str, str]] = []
    bot = AdminIntakeBot(
        bot_token="test-token",
        admin_user_ids=list(DEFAULT_TELEGRAM_ADMIN_USER_IDS),
        repo=repo,
        runner=runner,
        timezone_name=TASHKENT,
        scheduler=scheduler,
        sender=lambda chat_id, text: sent.append((chat_id, text)),
    )
    return bot, sent, scheduler, runner


def test_non_admin_cannot_schedule_a_post(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path)
    bot.handle_update(_message(111, "https://www.zara.com/es/es/wool-coat-p12345678.html"))
    assert sent == [("111", "Доступ только для администраторов.")]
    assert scheduler.jobs == []
    assert runner.urls == []
    assert bot.repo.get_awaiting_manual("111") is None


def test_admin_link_then_time_publishes_with_pipeline(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    assert "какое время" in sent[-1][1].lower()
    assert bot.repo.get_awaiting_manual(str(ADMIN_ID)) is not None

    bot.handle_update(_message(ADMIN_ID, "после обеда", update_id=2))
    assert "не понял время" in sent[-1][1].lower()

    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=3))
    assert "поставил пост" in sent[-1][1].lower()
    assert "Asia/Tashkent" in sent[-1][1]
    assert len(scheduler.jobs) == 1
    draft = bot.repo.get_manual_post(scheduler.jobs[0]["args"][0])
    assert draft is not None
    assert draft.status == "scheduled"
    assert draft.product_url == link

    job = scheduler.jobs[0]
    job["func"](*job["args"])
    assert runner.urls == [link]
    assert "пост опубликован" in sent[-1][1].lower()
    finished = bot.repo.get_manual_post(draft.id)
    assert finished is not None
    assert finished.status == "published"


def test_duplicate_scheduled_link_is_not_queued_twice(tmp_path: Path) -> None:
    bot, sent, scheduler, _runner = _bot(tmp_path)
    link = "https://shop.mango.com/es/es/p/coat/87012345"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "21:00", update_id=2))
    bot.handle_update(_message(ADMIN_ID, link, update_id=3))
    assert "уже стоит в очереди" in sent[-1][1]
    assert len(scheduler.jobs) == 1


def test_cancel_drops_the_pending_link(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path)
    bot.handle_update(_message(ADMIN_ID, "https://www.zara.com/es/es/dress-p9.html"))
    bot.handle_update(_message(ADMIN_ID, "/cancel", update_id=2))
    assert "отменено" in sent[-1][1].lower()
    assert bot.repo.get_awaiting_manual(str(ADMIN_ID)) is None


def test_already_published_link_is_rejected(tmp_path: Path) -> None:
    bot, sent, scheduler, _runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.repo.upsert_new(
        RawProduct(
            external_id="zara-12345678",
            source="zara",
            title="Wool coat",
            price=Decimal("40.00"),
            currency="EUR",
            photo_url="https://example.com/coat.jpg",
            product_url=link,
            in_stock=True,
        )
    )
    bot.repo.mark_published("zara-12345678", "tg-1", "ig-1")
    bot.handle_update(_message(ADMIN_ID, link))
    assert "уже публиковался" in sent[-1][1]
    assert scheduler.jobs == []


def test_publish_manual_url_uses_the_regular_publishers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = tmp_path / "coat.jpg"
    photo.write_bytes(b"local-photo")
    second = tmp_path / "coat-2.jpg"
    second.write_bytes(b"local-photo-2")
    product = RawProduct(
        external_id="zara-12345678",
        source="zara",
        title="Wool coat",
        price=Decimal("40.00"),
        currency="EUR",
        photo_url=str(photo),
        product_url="https://www.zara.com/es/es/wool-coat-p12345678.html",
        in_stock=True,
        photo_urls=[str(photo), str(second)],
    )
    monkeypatch.setattr("core.pipeline.fetch_product_page", lambda url, headless=True: product)

    repo = FakeProductRepository()
    telegram = FakePublisher("telegram")
    instagram = FakePublisher("instagram")

    class _Prompt:
        def load_prompt(self) -> str:
            return "write copy"

    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(fixed_rate=Decimal("1.08")),
        publishers=[telegram, instagram],
        config=AppConfig(),
        prompt_loader=_Prompt(),
    )

    ok, message = runner.publish_manual_url(product.product_url)
    assert ok is True
    assert "Wool coat" in message
    assert len(telegram.published_posts) == 1
    assert len(instagram.published_posts) == 1
    assert repo.products["zara-12345678"]["status"] == "published"
    assert repo.get_unposted_products() == []
