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
from core.pricing import DynamicRateConverter, FixedRateConverter
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
    def __init__(self, ok: bool = True, draft_data: dict | None = None) -> None:
        self.urls: list[str] = []
        self.filters: list[str | None] = []
        self.custom_descriptions: list[str | None] = []
        self.ok = ok
        self.draft_data = draft_data

    def prepare_manual_draft_data(self, product_url: str) -> dict:
        if self.draft_data is not None:
            return self.draft_data
        return {
            "processed_url": product_url,
            "title": "Wool coat",
            "price_final": 55,
            "currency": "USD",
            "header": "Wool coat-55$",
            "description": "",
            "photo_url": "https://example.com/coat.jpg",
        }

    def publish_manual_url(self, url: str, on_platform=None, publishers_filter=None, custom_description=None, **kwargs) -> tuple[bool, str]:
        self.urls.append(url)
        self.filters.append(publishers_filter)
        self.custom_descriptions.append(custom_description)
        if not self.ok:
            if on_platform is not None:
                on_platform("telegram", False, "канал недоступен")
                return False, ""
            return False, "Не удалось опубликовать: канал недоступен"
        if on_platform is not None:
            if publishers_filter in (None, "both", "telegram"):
                on_platform("telegram", True, "")
            if publishers_filter in (None, "both", "instagram"):
                on_platform("instagram", True, "")
            return True, ""
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


def _callback(user_id: int, data: str, update_id: int = 100) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": {"id": user_id, "first_name": "Admin"},
            "message": {
                "message_id": update_id,
                "chat": {"id": user_id, "type": "private"},
            },
            "data": data,
        },
    }


def _bot(
    tmp_path: Path,
    runner: _Runner | None = None,
    instagram_enabled: bool = True,
) -> tuple[AdminIntakeBot, list[tuple[str, str]], _Scheduler, _Runner]:
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
        instagram_enabled=instagram_enabled,
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
    assert "краткое описание" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_description"

    desc = "Слингбэки с вышивкой-98$\nРазмеры с 35 по 42.\nВысота каблука 4,5 см.\nЦвет: черный."
    bot.handle_update(_message(ADMIN_ID, desc, update_id=2))
    assert "какое время" in sent[-1][1].lower()

    bot.handle_update(_message(ADMIN_ID, "после обеда", update_id=3))
    assert "не понял время" in sent[-1][1].lower()

    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=4))
    assert "куда опубликовать" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_destination"

    # Admin chooses "both" via inline keyboard callback -> sends prerender
    bot.handle_update(_callback(ADMIN_ID, f"dest:both:{draft.id}", update_id=5))
    assert "предпросмотр" in sent[-1][1].lower()
    assert "европейское качество" in sent[-1][1].lower()
    assert "@nigora_7" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_approval"

    # Admin clicks approve
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=6))
    assert sent[-1][1].startswith("Поставил пост на ")
    assert "telegram и instagram" in sent[-1][1].lower()
    assert len(scheduler.jobs) == 1
    scheduled_post = bot.repo.get_manual_post(scheduler.jobs[0]["args"][0])
    assert scheduled_post is not None
    assert scheduled_post.status == "scheduled"
    assert scheduled_post.product_url == link
    assert scheduled_post.target_channel == "both"
    assert scheduled_post.custom_description == desc

    job = scheduler.jobs[0]
    job["func"](*job["args"])
    assert runner.urls == [link]
    assert runner.filters == [None]
    assert runner.custom_descriptions == [desc]
    assert "опубликован в тг" in sent[-2][1].lower()
    assert "опубликован в инсте" in sent[-1][1].lower()
    finished = bot.repo.get_manual_post(draft.id)
    assert finished is not None
    assert finished.status == "published"


def test_immediate_publish_uses_a_short_confirmation(tmp_path: Path) -> None:
    bot, sent, scheduler, _runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "сейчас", update_id=3))
    assert "куда опубликовать" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    bot.handle_update(_callback(ADMIN_ID, f"dest:both:{draft.id}", update_id=4))
    assert "предпросмотр" in sent[-1][1].lower()
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=5))
    assert sent[-1][1] == "Публикую сейчас в Telegram и Instagram"
    assert len(scheduler.jobs) == 1


def test_platform_failure_is_reported_on_its_own(tmp_path: Path) -> None:
    bot, sent, scheduler, _runner = _bot(tmp_path, runner=_Runner(ok=False))
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "сейчас", update_id=3))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    bot.handle_update(_callback(ADMIN_ID, f"dest:both:{draft.id}", update_id=4))
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=5))
    scheduler.jobs[0]["func"](*scheduler.jobs[0]["args"])
    assert sent[-1][1] == "Не удалось опубликовать в ТГ: канал недоступен"
    finished = bot.repo.get_manual_post(scheduler.jobs[0]["args"][0])
    assert finished is not None
    assert finished.status == "failed"


def test_duplicate_scheduled_link_is_not_queued_twice(tmp_path: Path) -> None:
    bot, sent, scheduler, _runner = _bot(tmp_path)
    link = "https://shop.mango.com/es/es/p/coat/87012345"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "21:00", update_id=3))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    bot.handle_update(_callback(ADMIN_ID, f"dest:both:{draft.id}", update_id=4))
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=5))
    bot.handle_update(_message(ADMIN_ID, link, update_id=6))
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
        color="бежевый",
        sizes=("S", "M", "L"),
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


def test_manual_publish_keeps_admin_caption_and_appends_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin's own part 1 must publish as written, with the shop footer after it."""
    from publishers.telegram_publisher import TelegramPublisher

    photo = tmp_path / "shoe.jpg"
    photo.write_bytes(b"local-photo")
    part1 = (
        "Слингбэки с вышивкой-98$\n"
        "Размеры с 35 по 42.\n"
        "Высота каблука 4,5 см.\n"
        "Цвет: черный."
    )
    product = RawProduct(
        external_id="mango-37016751",
        source="mango",
        title="Slingback heels",
        price=Decimal("49.99"),
        currency="EUR",
        photo_url=str(photo),
        product_url="https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/99",
        in_stock=True,
        photo_urls=[str(photo)],
        color="black",
        sizes=("35", "36", "37", "38", "39", "40", "41", "42"),
    )
    monkeypatch.setattr("core.pipeline.fetch_product_page", lambda url, headless=True: product)

    repo = FakeProductRepository()
    telegram = FakePublisher("telegram")
    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(fixed_rate=Decimal("1.08")),
        publishers=[telegram],
        config=AppConfig(),
        prompt_loader=type("Prompt", (), {"load_prompt": lambda self: "unused"})(),
    )

    ok, _message = runner.publish_manual_url(
        product.product_url,
        custom_description=part1,
        publishers_filter="telegram",
    )
    assert ok is True
    assert len(telegram.published_posts) == 1
    caption = TelegramPublisher(bot_token="test")._format_caption(telegram.published_posts[0])
    assert caption.startswith(part1)
    assert "Европейское качество" in caption
    assert "Обращаться: @nigora_7" in caption
    assert "Тел:+998998484044" in caption
    assert "@otzivi_fashbou" in caption
    assert "@vnalichiifash" in caption
    assert "https://www.instagram.com/fashionnestboutique" in caption
    assert "https://t.me/fashionalleyb" in caption
    assert caption.index(part1) < caption.index("Европейское качество")


def test_parse_product_html_non_euro_currencies() -> None:
    # Turkish Lira from Turkey storefront
    tr_html = """
    <html><head>
      <meta property="og:title" content="Oversized Trench | ZARA" />
      <meta property="og:image" content="https://static.zara.net/photos/trench.jpg" />
      <meta property="product:price:amount" content="1.490,00" />
      <meta property="product:price:currency" content="TRY" />
    </head></html>
    """
    p_tr = parse_product_html(tr_html, "https://www.zara.com/tr/tr/oversized-trench-p01234567.html")
    assert p_tr is not None
    assert p_tr.currency == "TRY"
    assert p_tr.price == Decimal("1490.00")

    # Polish Zloty from Poland storefront
    pl_html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Product","name":"Knit Cardigan",
       "image":["https://media.mango.com/is/image/punto/cardigan.jpg"],
       "offers":{"@type":"Offer","price":"199.99","priceCurrency":"PLN"}}
      </script>
    </head></html>
    """
    p_pl = parse_product_html(pl_html, "https://shop.mango.com/pl/pl/p/cardigan-p777")
    assert p_pl is not None
    assert p_pl.currency == "PLN"
    assert p_pl.price == Decimal("199.99")


def test_publish_manual_url_dynamic_fx_and_multi_photo_album(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p1 = tmp_path / "img1.jpg"
    p2 = tmp_path / "img2.jpg"
    p3 = tmp_path / "img3.jpg"
    p1.write_bytes(b"img1")
    p2.write_bytes(b"img2")
    p3.write_bytes(b"img3")

    product = RawProduct(
        external_id="zara-998877",
        source="zara",
        title="Linen Dress",
        price=Decimal("1200.00"),
        currency="TRY",
        photo_url=str(p1),
        product_url="https://www.zara.com/tr/tr/linen-dress-p998877.html",
        in_stock=True,
        photo_urls=[str(p1), str(p2), str(p3)],
        color="белый",
        sizes=("XS", "S", "M", "L"),
    )
    monkeypatch.setattr("core.pipeline.fetch_product_page", lambda url, headless=True: product)

    repo = FakeProductRepository()
    telegram = FakePublisher("telegram")
    instagram = FakePublisher("instagram")

    class _Prompt:
        def load_prompt(self) -> str:
            return "chic dress caption"

    # Dynamic converter with cached TRY: 0.030 USD
    fx = DynamicRateConverter(
        cached_rates={"TRY": Decimal("0.030"), "USD": Decimal("1.00"), "EUR": Decimal("1.08")},
        fallback_rate=Decimal("1.08"),
    )

    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=repo,
        llm=FakeLLMProvider(select_count=1),
        fx=fx,
        publishers=[telegram, instagram],
        config=AppConfig(markup=Decimal("10.00"), max_source_price_usd=80),
        prompt_loader=_Prompt(),
    )

    ok, message = runner.publish_manual_url(product.product_url)
    assert ok is True
    # 1200 TRY * 0.030 = 36 USD + 10 markup = 46 USD
    assert "46" in message or "Linen Dress" in message

    # Verify both publishers received multi-photo album/carousel
    tg_post = telegram.published_posts[0]
    assert len(tg_post.photo_urls) == 3
    assert tg_post.price == Decimal("46.00")

    ig_post = instagram.published_posts[0]
    assert len(ig_post.photo_urls) == 3
    assert ig_post.price == Decimal("46.00")


def test_admin_selects_telegram_destination(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=3))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_destination"

    bot.handle_update(_callback(ADMIN_ID, f"dest:telegram:{draft.id}", update_id=4))
    assert "предпросмотр" in sent[-1][1].lower()
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=5))
    assert sent[-1][1].startswith("Поставил пост на ")
    assert "telegram" in sent[-1][1].lower()
    assert "instagram" not in sent[-1][1].lower()

    job = scheduler.jobs[0]
    job["func"](*job["args"])
    assert runner.filters == ["telegram"]
    assert "опубликован в тг" in sent[-1][1].lower()
    assert "опубликован в инсте" not in sent[-1][1].lower()


def test_admin_selects_instagram_destination(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "19:00", update_id=3))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None

    bot.handle_update(_callback(ADMIN_ID, f"dest:instagram:{draft.id}", update_id=4))
    assert "предпросмотр" in sent[-1][1].lower()
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=5))
    assert sent[-1][1].startswith("Поставил пост на ")
    assert "instagram" in sent[-1][1].lower()

    job = scheduler.jobs[0]
    job["func"](*job["args"])
    assert runner.filters == ["instagram"]
    assert "опубликован в инсте" in sent[-1][1].lower()
    assert "опубликован в тг" not in sent[-1][1].lower()


def test_admin_destination_text_fallback_and_combined(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    # Link then description, then time, then typed destination
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "20:00", update_id=3))
    bot.handle_update(_message(ADMIN_ID, "тг", update_id=4))
    assert "предпросмотр" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=5))
    assert sent[-1][1].startswith("Поставил пост на ")
    assert "telegram" in sent[-1][1].lower()

    # Next link: time and destination combined in one message
    link2 = "https://shop.mango.com/es/es/p/coat/87012345"
    bot.handle_update(_message(ADMIN_ID, link2, update_id=6))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=7))
    bot.handle_update(_message(ADMIN_ID, "сейчас инста", update_id=8))
    assert "предпросмотр" in sent[-1][1].lower()
    draft2 = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft2.id}", update_id=9))
    assert "публикую сейчас в instagram" in sent[-1][1].lower()


def test_link_and_caption_in_one_message_skip_the_description_step(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    part1 = (
        "Слингбэки с вышивкой-98$\n"
        "Размеры с 35 по 42.\n"
        "Высота каблука 4,5 см.\n"
        "Цвет: черный."
    )
    bot.handle_update(_message(ADMIN_ID, f"{link}\n{part1}", update_id=1))
    assert "какое время" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_time"
    assert draft.custom_description == part1


def test_a_different_colour_link_is_not_called_a_repeat(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path)
    black = "https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/99"
    beige = "https://shop.mango.com/es/es/p/mujer/zapatos/slingback/37016751/01"
    bot.repo.upsert_new(
        RawProduct(
            external_id="mango-37016751-99",
            source="mango",
            title="Slingback",
            price=Decimal("49.99"),
            currency="EUR",
            photo_url="https://example.com/black.jpg",
            product_url=black,
            in_stock=True,
        )
    )
    bot.repo.mark_published("mango-37016751-99", "tg-1", None)
    bot.handle_update(_message(ADMIN_ID, beige))
    assert "уже публиковался" not in sent[-1][1].lower()
    assert bot.repo.get_awaiting_manual(str(ADMIN_ID)) is not None


def test_instagram_off_schedules_telegram_without_asking(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path, instagram_enabled=False)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Пальто-78$", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=3))
    assert "предпросмотр" in sent[-1][1].lower()
    assert "telegram" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.target_channel == "telegram"
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=4))
    assert "telegram" in sent[-1][1].lower()
    assert "instagram" not in sent[-1][1].lower()
    job = scheduler.jobs[0]
    job["func"](*job["args"])
    assert runner.filters == ["telegram"]


def test_admin_accepts_auto_description(tmp_path: Path) -> None:
    auto_desc = "Слингбэки с вышивкой-98$\nРазмеры с 35 по 42.\nВысота каблука 4,5 см.\nЦвет: черный."
    mock_draft_data = {
        "processed_url": "https://example.com/shoes",
        "title": "Слингбэки с вышивкой",
        "price_final": 98,
        "currency": "USD",
        "header": "Слингбэки с вышивкой-98$",
        "description": auto_desc,
        "photo_url": "https://example.com/shoes.jpg",
    }
    runner = _Runner(draft_data=mock_draft_data)
    bot, sent, scheduler, _ = _bot(tmp_path, runner=runner)

    bot.handle_update(_message(ADMIN_ID, "https://example.com/shoes", update_id=1))
    assert "краткое описание товара (часть 1)" in sent[-1][1].lower()
    assert "использовать это описание" in sent[-1][1].lower()
    assert "слингбэки с вышивкой-98$" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_description"
    assert draft.custom_description == auto_desc

    # Admin clicks [ ✅ Использовать это описание ]
    bot.handle_update(_callback(ADMIN_ID, f"desc:auto:{draft.id}", update_id=2))
    assert "описание принято" in sent[-1][1].lower()
    assert "какое время" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft.status == "awaiting_time"


def test_admin_prerender_edit_buttons_and_cancel(tmp_path: Path) -> None:
    bot, sent, scheduler, runner = _bot(tmp_path)
    link = "https://www.zara.com/es/es/wool-coat-p12345678.html"
    bot.handle_update(_message(ADMIN_ID, link, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "Описание 1", update_id=2))
    bot.handle_update(_message(ADMIN_ID, "18:30", update_id=3))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"dest:both:{draft.id}", update_id=4))
    assert "предпросмотр" in sent[-1][1].lower()

    # Edit description
    bot.handle_update(_callback(ADMIN_ID, f"edit:desc:{draft.id}", update_id=5))
    assert "новое краткое описание" in sent[-1][1].lower()
    bot.handle_update(_message(ADMIN_ID, "Новое описание 2", update_id=6))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft.custom_description == "Новое описание 2"

    # Edit time
    bot.handle_update(_callback(ADMIN_ID, f"edit:time:{draft.id}", update_id=7))
    assert "какое время" in sent[-1][1].lower()
    bot.handle_update(_message(ADMIN_ID, "19:45", update_id=8))

    # Edit destination
    bot.handle_update(_callback(ADMIN_ID, f"edit:dest:{draft.id}", update_id=9))
    assert "куда опубликовать" in sent[-1][1].lower()
    bot.handle_update(_callback(ADMIN_ID, f"dest:telegram:{draft.id}", update_id=10))
    assert "предпросмотр" in sent[-1][1].lower()
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft.target_channel == "telegram"

    # Cancel
    bot.handle_update(_callback(ADMIN_ID, f"cancel:{draft.id}", update_id=11))
    assert "отменена" in sent[-1][1].lower()
    assert scheduler.jobs == []



