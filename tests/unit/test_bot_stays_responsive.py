"""The admin bot keeps answering while one message takes long or never finishes.

The poll loop used to do every job itself. Reading a store page runs Chrome, and nodriver
waits for Chrome's answers without a time limit, so one frozen tab left every admin without
an answer until the service was restarted. Now each chat has its own worker (one chat's
messages still run in order), and a Chrome read is cut off after a time limit.
"""

from decimal import Decimal
from pathlib import Path
import asyncio
import sys
import threading
import time
import types

import httpx
import pytest

import adapters.product_page as product_page
import publishers.admin_intake_bot as intake
from config.app_config import AppConfig
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from publishers.admin_intake_bot import AdminIntakeBot
from storage.repository import SqlAlchemyProductRepository
from tests.conftest import FakeLLMProvider
from tests.unit.test_admin_intake import ADMIN_ID, _message, _Runner, _Scheduler

OTHER_ADMIN = 333588697
LINK = "https://www.zara.com/es/es/wool-coat-p12345678.html"


class _SlowRunner(_Runner):
    """A page read that does not come back until the test lets it."""

    def __init__(self) -> None:
        super().__init__()
        self.reading = threading.Event()
        self.release = threading.Event()

    def prepare_manual_draft_data(self, product_url: str) -> dict:
        self.reading.set()
        self.release.wait(10)
        return super().prepare_manual_draft_data(product_url)


class _Telegram:
    """getUpdates: the first batch at once, the rest once the gate opens, then an idle long poll."""

    def __init__(self, batches: list[list[dict]], gate: threading.Event | None = None) -> None:
        self.batches = list(batches)
        self.gate = gate
        self.served = 0

    def __call__(self, timeout: int) -> list[dict]:
        if self.batches and (self.served == 0 or self.gate is None or self.gate.is_set()):
            self.served += 1
            return self.batches.pop(0)
        time.sleep(0.02)
        return []


def _polling_bot(tmp_path: Path, runner, batches, gate=None) -> tuple[AdminIntakeBot, list[tuple[str, str]]]:
    sent: list[tuple[str, str]] = []
    lock = threading.Lock()

    def record(chat_id: str, text: str) -> None:
        with lock:
            sent.append((chat_id, text))

    bot = AdminIntakeBot(
        bot_token="test-token",
        admin_user_ids=[ADMIN_ID, OTHER_ADMIN],
        repo=SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'bot.db'}"),
        runner=runner,
        timezone_name="Asia/Tashkent",
        scheduler=_Scheduler(),
        sender=record,
    )
    bot._get_updates = _Telegram(batches, gate)
    threading.Thread(target=bot._poll_loop, daemon=True).start()
    return bot, sent


def _wait_for(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _texts(sent, chat: int) -> list[str]:
    return [text for chat_id, text in list(sent) if chat_id == str(chat)]


def test_a_link_that_takes_long_does_not_silence_the_other_admins(tmp_path: Path) -> None:
    runner = _SlowRunner()
    bot, sent = _polling_bot(
        tmp_path,
        runner,
        [[_message(ADMIN_ID, LINK, 1)], [_message(OTHER_ADMIN, "/start", 2)]],
        gate=runner.reading,
    )
    try:
        assert runner.reading.wait(5)
        assert _wait_for(lambda: _texts(sent, OTHER_ADMIN)), "the other admin got no answer"
        assert "Пришлите ссылку" in _texts(sent, OTHER_ADMIN)[0]
        assert not runner.release.is_set()  # answered while the first link was still being read
        runner.release.set()
        assert _wait_for(lambda: any("краткое описание" in t.lower() for t in _texts(sent, ADMIN_ID)))
    finally:
        runner.release.set()
        bot._stop.set()


def test_one_admins_messages_are_answered_in_the_order_they_came(tmp_path: Path) -> None:
    runner = _SlowRunner()
    bot, sent = _polling_bot(
        tmp_path,
        runner,
        [[_message(ADMIN_ID, LINK, 1)], [_message(ADMIN_ID, "/cancel", 2)]],
        gate=runner.reading,
    )
    try:
        assert runner.reading.wait(5)
        time.sleep(0.2)
        assert not any("отмен" in t.lower() for t in _texts(sent, ADMIN_ID))  # /cancel waits its turn
        runner.release.set()
        assert _wait_for(lambda: any(t.startswith("Отменено") for t in _texts(sent, ADMIN_ID)))
        texts = _texts(sent, ADMIN_ID)
        asked = next(i for i, t in enumerate(texts) if "краткое описание" in t.lower())
        cancelled = next(i for i, t in enumerate(texts) if t.startswith("Отменено"))
        assert asked < cancelled
        assert bot.repo.get_awaiting_manual(str(ADMIN_ID)) is None
    finally:
        runner.release.set()
        bot._stop.set()


def test_an_admin_who_writes_during_a_long_read_hears_the_bot_is_busy_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "_BUSY_NOTE_AFTER_SECONDS", 0.0)
    runner = _SlowRunner()
    bot, sent = _polling_bot(
        tmp_path,
        runner,
        [[_message(ADMIN_ID, LINK, 1)], [_message(ADMIN_ID, "привет", 2)], [_message(ADMIN_ID, "ау", 3)]],
        gate=runner.reading,
    )
    try:
        assert runner.reading.wait(5)
        assert _wait_for(lambda: intake._BUSY_NOTE in _texts(sent, ADMIN_ID))
        time.sleep(0.3)
        assert _texts(sent, ADMIN_ID).count(intake._BUSY_NOTE) == 1
    finally:
        runner.release.set()
        bot._stop.set()


def test_an_idle_chat_worker_ends_and_the_next_message_starts_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "_CHAT_WORKER_IDLE_SECONDS", 0.1)
    bot, sent = _polling_bot(tmp_path, _Runner(), [[_message(ADMIN_ID, "/start", 1)]])
    try:
        assert _wait_for(lambda: len(_texts(sent, ADMIN_ID)) == 1)
        assert _wait_for(lambda: str(ADMIN_ID) not in bot._chat_queues)
        bot._dispatch(_message(ADMIN_ID, "/start", 2))
        assert _wait_for(lambda: len(_texts(sent, ADMIN_ID)) == 2)
    finally:
        bot._stop.set()


def test_a_post_that_cannot_be_rearmed_does_not_stop_the_bot_from_starting() -> None:
    posts = [
        types.SimpleNamespace(id=1, status="scheduled", publish_at=None),
        types.SimpleNamespace(id=2, status="scheduled", publish_at=None),
    ]
    repo = types.SimpleNamespace(
        list_manual_posts=lambda statuses: posts,
        set_manual_post_status=lambda *a, **k: None,
    )
    armed: list[str] = []

    class _BrokenScheduler:
        def add_job(self, func, trigger=None, id=None, **kwargs) -> None:
            if id == "manual_post_1":
                raise ValueError("bad job")
            armed.append(id)

    bot = AdminIntakeBot("test-token", [ADMIN_ID], repo, _Runner(), "Asia/Tashkent", scheduler=_BrokenScheduler())
    bot.restore_jobs()
    assert armed == ["manual_post_2"]


def test_a_poll_error_says_what_it_means() -> None:
    request = httpx.Request("GET", "https://api.telegram.org/bot123:x/getUpdates")

    def error(status: int) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError(str(status), request=request, response=httpx.Response(status, request=request))

    assert "another getUpdates call" in intake._poll_failure_reason(error(409))
    assert "TELEGRAM_BOT_TOKEN" in intake._poll_failure_reason(error(401))
    assert intake._poll_failure_reason(RuntimeError("network down")) == "network down"


def test_a_frozen_chrome_neither_freezes_the_bot_nor_keeps_the_chrome_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, as it happened: the store sends a bot wall, Chrome starts and never answers."""

    async def chrome_that_never_answers(**kwargs):
        await asyncio.Event().wait()  # nodriver awaiting a reply Chrome never sends

    fake_nodriver = types.SimpleNamespace(
        start=chrome_that_never_answers,
        util=types.SimpleNamespace(get_registered_instances=lambda: set()),
    )
    monkeypatch.setitem(sys.modules, "nodriver", fake_nodriver)
    monkeypatch.setattr("core.pipeline.resolve_product_url", lambda url, *a, **k: (url, ""))
    monkeypatch.setattr(product_page, "_fetch_html_fast", lambda url, timeout: (url, ""))
    monkeypatch.setattr(product_page, "_ensure_virtual_display", lambda: None)
    monkeypatch.setattr(product_page, "BROWSER_READ_LIMIT_SECONDS", 0.5)
    monkeypatch.setattr(product_page, "BROWSER_ATTEMPTS", 1)

    class _Prompt:
        def load_prompt(self) -> str:
            return "write copy"

    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=SqlAlchemyProductRepository(f"sqlite:///{tmp_path / 'runner.db'}"),
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(fixed_rate=Decimal("1.08")),
        publishers=[],
        config=AppConfig(),
        prompt_loader=_Prompt(),
    )
    bot, sent = _polling_bot(
        tmp_path,
        runner,
        [[_message(ADMIN_ID, LINK, 1)], [_message(OTHER_ADMIN, "/start", 2)]],
    )
    try:
        assert _wait_for(lambda: _texts(sent, OTHER_ADMIN), 3), "the bot froze on the first link"
        # The stuck read is cut off and the admin who sent the link is answered too.
        assert _wait_for(lambda: len(_texts(sent, ADMIN_ID)) >= 2, 10)
        assert "Открываю страницу" in _texts(sent, ADMIN_ID)[0]
        assert _wait_for(lambda: not product_page._CHROME_LOCK.locked(), 3)
    finally:
        bot._stop.set()
