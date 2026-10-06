"""The bot asks which colour to post and posts exactly that colour."""

from decimal import Decimal
from pathlib import Path

from adapters.base import RawProduct
from tests.unit.test_admin_intake import ADMIN_ID, _bot, _callback, _message, _Runner

BASE = "https://www.zara.com/uz/ru/linen-dress-p03067301.html"
BLACK = f"{BASE}?v1=101"
BEIGE = f"{BASE}?v1=102"
NAVY = f"{BASE}?v1=103"


class _ColourRunner(_Runner):
    """Store page with three colours; each colour page gives its own description."""

    def __init__(self) -> None:
        super().__init__()
        self.prepared: list[str] = []

    def prepare_manual_draft_data(self, product_url: str) -> dict:
        self.prepared.append(product_url)
        names = {BLACK: "черный", BEIGE: "бежевый", NAVY: "синий"}
        opened = product_url if product_url in names else BLACK
        return {
            "processed_url": opened,
            "title": "Платье",
            "price_final": 55,
            "currency": "USD",
            "header": "Платье-55$",
            "description": f"Платье-55$\nРазмеры от XS до L.\nЦвет: {names[opened]}.",
            "photo_url": f"https://example.com/{names[opened]}.jpg",
            "photo_count": 12,
            "colors": [
                {"code": "101", "name": "BLACK", "name_ru": "черный", "url": BLACK, "selected": opened == BLACK},
                {"code": "102", "name": "ECRU", "name_ru": "бежевый", "url": BEIGE, "selected": opened == BEIGE},
                {"code": "103", "name": "NAVY", "name_ru": "синий", "url": NAVY, "selected": opened == NAVY},
            ],
        }


def _publish(bot, scheduler, draft_id: int, update_id: int = 50) -> None:
    bot.handle_update(_message(ADMIN_ID, "сейчас", update_id=update_id))
    bot.handle_update(_callback(ADMIN_ID, f"dest:telegram:{draft_id}", update_id=update_id + 1))
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft_id}", update_id=update_id + 2))
    job = scheduler.jobs[-1]
    job["func"](*job["args"])


def test_link_with_several_colours_asks_which_one(tmp_path: Path) -> None:
    bot, sent, _scheduler, _runner = _bot(tmp_path, runner=_ColourRunner())
    bot.handle_update(_message(ADMIN_ID, BEIGE, update_id=1))
    assert "открываю страницу" in sent[0][1].lower()
    menu = sent[-1][1]
    assert "3 цвета" in menu
    assert "Бежевый — по вашей ссылке" in menu
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None
    assert draft.status == "awaiting_color"
    assert draft.product_url == BEIGE


def test_picking_another_colour_posts_that_colour(tmp_path: Path) -> None:
    runner = _ColourRunner()
    bot, sent, scheduler, _ = _bot(tmp_path, runner=runner)
    bot.handle_update(_message(ADMIN_ID, BEIGE, update_id=1))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"color:{draft.id}:2", update_id=2))
    assert "Цвет: синий." in sent[-1][1]
    assert runner.prepared == [BEIGE, NAVY]
    bot.handle_update(_callback(ADMIN_ID, f"desc:auto:{draft.id}", update_id=3))
    _publish(bot, scheduler, draft.id)
    assert runner.urls == [NAVY]
    assert runner.custom_descriptions == ["Платье-55$\nРазмеры от XS до L.\nЦвет: синий."]


def test_the_link_colour_is_ready_without_a_second_page_read(tmp_path: Path) -> None:
    runner = _ColourRunner()
    bot, sent, _scheduler, _ = _bot(tmp_path, runner=runner)
    bot.handle_update(_message(ADMIN_ID, BEIGE, update_id=1))
    bot.handle_update(_message(ADMIN_ID, "2", update_id=2))
    assert runner.prepared == [BEIGE]
    assert "Цвет: бежевый." in sent[-1][1]
    assert "Фото на сайте: 12" in sent[-1][1]


def test_all_colours_go_out_as_separate_posts(tmp_path: Path) -> None:
    runner = _ColourRunner()
    bot, sent, scheduler, _ = _bot(tmp_path, runner=runner)
    bot.handle_update(_message(ADMIN_ID, BLACK, update_id=1))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"color:{draft.id}:all", update_id=2))
    assert "для каждого цвета" in sent[-1][1]
    bot.handle_update(_callback(ADMIN_ID, f"desc:auto:{draft.id}", update_id=3))
    bot.handle_update(_message(ADMIN_ID, "сейчас", update_id=4))
    bot.handle_update(_callback(ADMIN_ID, f"dest:both:{draft.id}", update_id=5))
    assert "3 отдельных поста" in sent[-1][1]
    bot.handle_update(_callback(ADMIN_ID, f"approve:{draft.id}", update_id=6))
    job = scheduler.jobs[-1]
    job["func"](*job["args"])
    assert runner.urls == [BLACK, BEIGE, NAVY]
    assert [desc.splitlines()[-1] for desc in runner.custom_descriptions] == [
        "Цвет: черный.",
        "Цвет: бежевый.",
        "Цвет: синий.",
    ]
    assert sent[-1][1] == "Готово: опубликовано 3 из 3 цветов."
    assert bot.repo.get_manual_post(draft.id).status == "published"


def test_own_text_for_all_colours_names_each_colour(tmp_path: Path) -> None:
    runner = _ColourRunner()
    bot, _sent, scheduler, _ = _bot(tmp_path, runner=runner)
    bot.handle_update(_message(ADMIN_ID, f"{BLACK}\nЛьняное платье-60$\nЦвет: черный.", update_id=1))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_message(ADMIN_ID, "все", update_id=2))
    assert bot.repo.get_manual_post(draft.id).status == "awaiting_time"
    _publish(bot, scheduler, draft.id)
    assert runner.custom_descriptions == [
        "Льняное платье-60$\nЦвет: черный.",
        "Льняное платье-60$\nЦвет: бежевый.",
        "Льняное платье-60$\nЦвет: синий.",
    ]


def _mark_published(bot, url: str, external_id: str) -> None:
    bot.repo.upsert_new(
        RawProduct(
            external_id=external_id,
            source="zara",
            title="Linen dress",
            price=Decimal("40.00"),
            currency="EUR",
            photo_url="https://example.com/x.jpg",
            product_url=url,
            in_stock=True,
        )
    )
    bot.repo.mark_published(external_id, "tg-1", None)


def test_published_colour_is_flagged_and_the_others_stay_new(tmp_path: Path) -> None:
    runner = _ColourRunner()
    bot, sent, scheduler, _ = _bot(tmp_path, runner=runner)
    _mark_published(bot, BLACK, "zara-03067301-101")
    # The admin sends the beige link: beige is new, so no repeat warning.
    bot.handle_update(_message(ADMIN_ID, BEIGE, update_id=1))
    menu = sent[-1][1]
    assert "Черный — ✅ уже публиковался" in menu
    assert "Бежевый — по вашей ссылке" in menu and "Бежевый — по вашей ссылке, ✅" not in menu
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"color:{draft.id}:all", update_id=2))
    bot.handle_update(_callback(ADMIN_ID, f"desc:auto:{draft.id}", update_id=3))
    _publish(bot, scheduler, draft.id)
    assert runner.urls == [BEIGE, NAVY]


def test_repeat_of_a_published_colour_is_confirmed_and_posts_that_colour(tmp_path: Path) -> None:
    runner = _ColourRunner()
    bot, sent, scheduler, _ = _bot(tmp_path, runner=runner)
    _mark_published(bot, BEIGE, "zara-03067301-102")
    bot.handle_update(_message(ADMIN_ID, BEIGE, update_id=1))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"color:{draft.id}:1", update_id=2))
    assert "уже публиковался" in sent[-1][1]
    assert bot.repo.get_manual_post(draft.id).status == "awaiting_repeat"

    bot.handle_update(_callback(ADMIN_ID, f"repeat:yes:{draft.id}", update_id=3))
    assert "Цвет: бежевый." in sent[-1][1]
    bot.handle_update(_callback(ADMIN_ID, f"desc:auto:{draft.id}", update_id=4))
    received: dict = {}
    original = runner.publish_manual_url

    def spy(url, **kwargs):
        received.update(kwargs)
        return original(url, **kwargs)

    runner.publish_manual_url = spy
    _publish(bot, scheduler, draft.id)
    assert runner.urls == [BEIGE]
    assert received["bypass_duplicate_gate"] is True


def test_declining_a_repeat_returns_to_the_colour_menu(tmp_path: Path) -> None:
    bot, sent, _scheduler, _ = _bot(tmp_path, runner=_ColourRunner())
    _mark_published(bot, BEIGE, "zara-03067301-102")
    bot.handle_update(_message(ADMIN_ID, BEIGE, update_id=1))
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    bot.handle_update(_callback(ADMIN_ID, f"color:{draft.id}:1", update_id=2))
    bot.handle_update(_callback(ADMIN_ID, f"repeat:no:{draft.id}", update_id=3))
    assert "Какой цвет опубликовать" in sent[-1][1]
    assert bot.repo.get_manual_post(draft.id).status == "awaiting_color"
