"""The bot against the real PipelineRunner: only the network is replaced.

The dialogue tests use a stub runner. These go through the real
prepare_manual_draft_data and publish_manual_url, so an error on that path
(a wrong call, a missing import) fails here instead of in the admin's chat.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from config.app_config import AppConfig
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from tests.conftest import FakeLLMProvider, FakePublisher
from tests.unit.test_admin_intake import ADMIN_ID, _bot, _callback, _message

PRODUCT = "https://shop.mango.com/es/es/p/mujer/marroquineria/neceseres/bolso-vanity-acolchado/37066365"
BURGUNDY = f"{PRODUCT}/75/00"
BLACK = f"{PRODUCT}/99/00"


def _mango_page(selected: str) -> str:
    """A Mango product page the way the Next.js storefront ships it: escaped JSON in RSC chunks."""
    shots = "".join(
        f'<img src="https://media.mango.com/is/image/punto/37066365-{selected}-{index:02d}?wid=1080">'
        for index in range(1, 7)
    )
    swatches = (
        '<img src="https://media.mango.com/is/image/punto/37066365-75-99?wid=40">'
        '<img src="https://media.mango.com/is/image/punto/37066365-99-99?wid=40">'
    )
    payload = (
        '{\\"product\\":{\\"id\\":\\"37066365\\",\\"name\\":\\"Bolso vanity acolchado\\",'
        '\\"colors\\":[{\\"id\\":\\"75\\",\\"label\\":\\"Burdeos\\",\\"sizes\\":[{\\"id\\":\\"00\\",\\"label\\":\\"TALLA ÚNICA\\"}]},'
        '{\\"id\\":\\"99\\",\\"label\\":\\"Negro\\",\\"sizes\\":[{\\"id\\":\\"00\\",\\"label\\":\\"TALLA ÚNICA\\"}]}]}}'
    )
    return f"""
<html><head>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Product","name":"Bolso vanity acolchado","sku":"37066365",
 "image":["https://media.mango.com/is/image/punto/37066365-{selected}-01"],
 "offers":{{"@type":"Offer","price":"25.99","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}}}
</script></head><body>
{shots}{swatches}
<script>self.__next_f.push([1,"5:{payload}\\n"])</script>
</body></html>
"""


@pytest.fixture
def mango_pages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []

    def fetch(url: str, timeout_seconds: float = 20.0) -> tuple[str, str]:
        opened.append(url)
        selected = "99" if "/99" in url else "75"
        return url, _mango_page(selected)

    monkeypatch.setattr("adapters.product_page._fetch_html_fast", fetch)
    monkeypatch.setattr("adapters.product_page.fetch_product_html", fetch)
    monkeypatch.setattr(
        "core.pipeline.process_product_url_with_playwright",
        lambda url, headless=True: (url, ""),
    )
    return opened


def _runner() -> PipelineRunner:
    class _Prompt:
        def load_prompt(self) -> str:
            return "write copy"

    from tests.conftest import FakeProductRepository

    return PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=FakeProductRepository(),
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(fixed_rate=Decimal("1.08")),
        publishers=[FakePublisher("telegram")],
        config=AppConfig(),
        prompt_loader=_Prompt(),
    )


def test_real_draft_preparation_returns_text_and_colours(mango_pages: list[str]) -> None:
    data = _runner().prepare_manual_draft_data(BURGUNDY)
    assert data["description"]
    assert data["processed_url"] == BURGUNDY
    assert [(item["code"], item["selected"]) for item in data["colors"]] == [("75", True), ("99", False)]
    assert data["colors"][1]["url"] == BLACK


def test_bot_with_real_runner_offers_both_mango_colours(tmp_path: Path, mango_pages: list[str]) -> None:
    runner = _runner()
    bot, sent, _scheduler, _ = _bot(tmp_path, runner=runner)  # type: ignore[arg-type]
    bot.handle_update(_message(ADMIN_ID, BURGUNDY, update_id=1))
    menu = sent[-1][1]
    assert "2 цвета" in menu
    assert "по вашей ссылке" in menu
    draft = bot.repo.get_awaiting_manual(str(ADMIN_ID))
    assert draft is not None and draft.status == "awaiting_color"

    bot.handle_update(_callback(ADMIN_ID, f"color:{draft.id}:1", update_id=2))
    assert "Краткое описание товара" in sent[-1][1]
    assert bot.repo.get_manual_post(draft.id).product_url == BLACK
