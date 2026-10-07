"""The Chrome read itself, driven by a stand-in for nodriver's tab.

Keeps the fullest view of the page, survives a tab that dies mid-read, asks the
store's own data only when the read is short of something, and never runs two
Chrome reads at once (one read used to stop the other's Chrome).
"""

import asyncio
import json
import sys
import threading
import time
import types

import pytest

import adapters.product_page as product_page
from adapters.product_page import _append_to_body, _browser_html, _fetch_html_browser, _product_ids
from core.gallery import keep_single_product
from tests.unit.test_web_component_pages import PAGE, SHADOW, STORE_API, URL

FULL = _append_to_body(PAGE, SHADOW)
STORE_BLOCK = '<script type="application/json" data-bot-source="store-api">' + STORE_API + "</script>"


class _Tab:
    def __init__(self, contents: list[str], fail_after: int | None = None, store: str = "[]") -> None:
        self.url = URL
        self.contents = contents
        self.reads = 0
        self.fail_after = fail_after
        self.store = store
        self.scrolls = 0
        self.store_asks = 0

    async def sleep(self, seconds: float) -> None:
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise RuntimeError("the tab was closed")

    async def get_content(self) -> str:
        html = self.contents[min(self.reads, len(self.contents) - 1)]
        self.reads += 1
        return html

    async def evaluate(self, script: str, await_promise: bool = False, return_by_value: bool = False):
        if "scrollBy" in script:
            self.scrolls += 1
            return "bottom"
        if "itxrest" in script:
            self.store_asks += 1
            return self.store
        return None  # no shadow roots, and scrollTo


@pytest.fixture
def chrome(monkeypatch: pytest.MonkeyPatch):
    def open_with(tab: _Tab):
        class _Browser:
            async def get(self, url: str) -> _Tab:
                return tab

        async def start(**kwargs):
            return _Browser()

        fake = types.SimpleNamespace(start=start, util=types.SimpleNamespace(get_registered_instances=lambda: set()))
        monkeypatch.setitem(sys.modules, "nodriver", fake)
        return asyncio.run(_browser_html(URL, 40))

    return open_with


def test_the_fullest_view_of_the_page_is_kept(chrome) -> None:
    tab = _Tab([PAGE, FULL, FULL, PAGE])
    html, page_url = chrome(tab)
    assert "data-shadow-host" in html
    assert page_url == URL
    assert tab.scrolls == 1


def test_a_tab_that_dies_mid_read_keeps_what_was_read(chrome) -> None:
    tab = _Tab([FULL], fail_after=2)
    html, _url = chrome(tab)
    assert "data-shadow-host" in html


def test_a_read_short_of_sizes_takes_the_store_data(chrome) -> None:
    tab = _Tab([FULL], store=json.dumps([STORE_API]))
    html, _url = chrome(tab)
    assert tab.store_asks == 1
    assert 'data-bot-source="store-api"' in html


def test_a_complete_read_does_not_ask_the_store(chrome) -> None:
    tab = _Tab([_append_to_body(FULL, STORE_BLOCK)])
    chrome(tab)
    assert tab.store_asks == 0


def test_chrome_reads_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    running: list[int] = []
    peak: list[int] = []

    async def read(url: str, timeout_seconds: float) -> tuple[str, str]:
        running.append(1)
        peak.append(len(running))
        await asyncio.sleep(0.05)
        running.pop()
        return "<html></html>", url

    monkeypatch.setattr(product_page, "_browser_html", read)
    monkeypatch.setattr(product_page, "_ensure_virtual_display", lambda: None)
    threads = [threading.Thread(target=_fetch_html_browser, args=(URL, 5)) for _ in range(3)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max(peak) == 1
    assert time.monotonic() - started >= 0.15


BASE = "https://static.pullandbear.net/assets/public/aa/bb/cc/dd"


def _photos(colour: str, product: str = "11308840") -> list[str]:
    return [f"{BASE}/{product}{colour}-{shot}/{product}{colour}-{shot}.jpg" for shot in ("M", "A1M", "A2M")]


def test_the_photos_are_of_the_colour_the_link_names() -> None:
    page = URL.replace("cS=002", "cS=800")
    # The page's first photo (the anchor) is the store's default colour 002.
    assert keep_single_product(_photos("002") + _photos("800"), page, anchor_url=_photos("002")[0]) == _photos("800")


def test_the_link_colour_never_brings_in_another_product() -> None:
    page = URL.replace("cS=002", "cS=800")
    urls = _photos("002") + _photos("800") + _photos("800", product="20125566")
    assert keep_single_product(urls, page, anchor_url=_photos("002")[0]) == _photos("800")


def test_without_a_colour_in_the_link_the_anchor_colour_stays() -> None:
    page = "https://www.pullandbear.com/de/retrosneaker-l11308840"
    assert keep_single_product(_photos("002") + _photos("800"), page, anchor_url=_photos("002")[0]) == _photos("002")


def test_zara_v1_is_a_product_not_a_colour() -> None:
    zara = [f"https://static.zara.net/assets/public/aa/05067177{c}-p/05067177{c}-p.jpg" for c in ("716", "730")]
    page = "https://www.zara.com/es/es/camiseta-pique-cuello-perkins-p05067177.html?v1=545436419"
    assert keep_single_product(zara, page, anchor_url=zara[0]) == zara[:1]


def test_the_product_ids_a_store_api_may_use() -> None:
    assert _product_ids(URL) == ["11308840", "753677463"]
    lefties = "https://www.lefties.com/es/en/woman/footwear/sneakers/retro-sneakers-c1030272270p754376580.html?colorId=001&parentId=754380551"
    # The category (c1030272270) would bring in other products' data.
    assert _product_ids(lefties) == ["754376580", "754380551"]
