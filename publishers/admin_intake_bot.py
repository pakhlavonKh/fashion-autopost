"""Private Telegram bot that lets admins schedule a product link.

Only the configured admin user ids can talk to the bot. An admin sends a
product URL, the bot asks when to publish, and the post goes out at that time
through the same pipeline as the regular schedule.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import re
import threading
import zoneinfo
from typing import Any, Callable

import httpx

from core.publish_time import parse_publish_time
from publishers.telegram_discovery import TelegramChatDiscoveryService

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_JOB_PREFIX = "manual_post_"


class AdminIntakeBot:
    """Long-polling intake bot for admin-submitted product links."""

    def __init__(
        self,
        bot_token: str,
        admin_user_ids: list[int],
        repo: Any,
        runner: Any,
        timezone_name: str,
        scheduler: Any | None = None,
        sender: Callable[[str, str], None] | None = None,
        discovery: TelegramChatDiscoveryService | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.admin_user_ids = {int(user_id) for user_id in admin_user_ids}
        self.repo = repo
        self.runner = runner
        self.timezone_name = timezone_name or "UTC"
        self.scheduler = scheduler
        self._sender = sender
        self.discovery = discovery or TelegramChatDiscoveryService(
            bot_token=bot_token,
            repo=repo,
            allowed_private_user_ids=self.admin_user_ids,
        )
        self._offset: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owns_scheduler = False
        self._fire_lock = threading.Lock()

    def start(self) -> None:
        """Drop the old update backlog, restore scheduled posts, and poll for new messages."""
        if self.scheduler is None:
            from apscheduler.schedulers.background import BackgroundScheduler

            self.scheduler = BackgroundScheduler()
            self.scheduler.start()
            self._owns_scheduler = True
        try:
            self._delete_webhook()
            self._discard_backlog()
        except Exception as exc:
            logger.warning("Could not clear the Telegram update backlog: %s", exc)
        self.restore_jobs()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-admin-intake",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Admin intake bot is listening. Allowed user ids: %s. Timezone: %s",
            ", ".join(str(user_id) for user_id in sorted(self.admin_user_ids)),
            self.timezone_name,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._owns_scheduler and self.scheduler is not None:
            self.scheduler.shutdown(wait=False)

    def restore_jobs(self) -> None:
        """Re-arm posts that were scheduled before the process restarted."""
        now = datetime.now(timezone.utc)
        for post in self.repo.list_manual_posts(["scheduled", "publishing"]):
            if post.status == "publishing":
                self.repo.set_manual_post_status(post.id, "scheduled")
            run_at = post.publish_at or now
            if run_at <= now:
                run_at = now + timedelta(seconds=2)
            self._arm_job(post.id, run_at)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, dict) and (message.get("chat") or {}).get("type") == "private":
            self._handle_private_message(message)
            return
        self.discovery._process_single_update(update)

    def _handle_private_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        try:
            user_id = int(sender.get("id") or chat.get("id"))
        except (TypeError, ValueError):
            return
        chat_id = str(chat.get("id") or user_id)
        if user_id not in self.admin_user_ids:
            self._send(chat_id, "Доступ только для администраторов.")
            return
        self.discovery._process_single_update({"message": message})
        text = str(message.get("text") or message.get("caption") or "")
        reply = self._reply_to_admin(user_id, chat_id, text)
        if reply:
            self._send(chat_id, reply)

    def _reply_to_admin(self, user_id: int, chat_id: str, text: str) -> str:
        command = _command_name(text)
        if command in {"/start", "/help"}:
            return (
                "Пришлите ссылку на товар. Я спрошу, в какое время выложить пост, "
                "и опубликую его с теми же настройками, что и остальные: "
                "описание, наценка, Telegram и Instagram."
            )
        if command == "/cancel":
            cancelled = self.repo.cancel_awaiting_manual(str(user_id))
            if cancelled:
                return "Отменено. Пришлите новую ссылку, когда будете готовы."
            return "Сейчас нечего отменять. Пришлите ссылку на товар."

        product_url = extract_product_url(text)
        if product_url:
            return self._accept_link(user_id, chat_id, product_url)
        if not text.strip():
            return "Пришлите ссылку на товар."
        return self._accept_time(user_id, text)

    def _accept_link(self, user_id: int, chat_id: str, product_url: str) -> str:
        from core.dedup import extract_duplicate_signatures

        open_post = self.repo.find_open_manual_by_url(product_url)
        if open_post and open_post.status in {"scheduled", "publishing"} and open_post.publish_at:
            when = self._format_local(open_post.publish_at)
            return f"Эта ссылка уже стоит в очереди на {when} ({self.timezone_name})."
        signatures = extract_duplicate_signatures("", "", product_url, "")
        if signatures & self.repo.get_published_signatures():
            return "Этот товар уже публиковался. Пришлите другую ссылку."
        self.repo.create_manual_draft(str(user_id), chat_id, product_url)
        return (
            "Ссылку принял. В какое время выложить пост?\n"
            "Например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас».\n"
            f"Часовой пояс: {self.timezone_name}."
        )

    def _accept_time(self, user_id: int, text: str) -> str:
        draft = self.repo.get_awaiting_manual(str(user_id))
        if draft is None:
            return "Сначала пришлите ссылку на товар."
        try:
            parsed = parse_publish_time(text, datetime.now(timezone.utc), self.timezone_name)
        except ValueError as exc:
            return str(exc)

        scheduled = self.repo.schedule_manual_post(draft.id, parsed.when)
        if scheduled is None:
            return "Не удалось сохранить время. Пришлите ссылку ещё раз."
        run_at = parsed.when
        if parsed.note == "сейчас":
            run_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        self._arm_job(scheduled.id, run_at)

        if parsed.note == "сейчас":
            return (
                "Публикую сейчас. Пост соберётся так же, как остальные: "
                "описание, наценка и те же каналы. Напишу, когда выйдет."
            )
        extra = f"\n{parsed.note}" if parsed.note else ""
        return (
            f"Поставил пост на {self._format_local(parsed.when)} ({self.timezone_name})."
            f"{extra}\n"
            "Настройки те же, что у остальных постов: описание, наценка, Telegram и Instagram."
        )

    def _arm_job(self, post_id: int, run_at: datetime) -> None:
        from apscheduler.triggers.date import DateTrigger

        if self.scheduler is None:
            raise RuntimeError("Scheduler is not configured")
        when = run_at if run_at.tzinfo else run_at.replace(tzinfo=timezone.utc)
        self.scheduler.add_job(
            func=self._publish_scheduled,
            trigger=DateTrigger(run_date=when),
            id=f"{_JOB_PREFIX}{post_id}",
            name=f"Manual product post {post_id}",
            replace_existing=True,
            misfire_grace_time=86400,
            args=[post_id],
        )
        logger.info("Scheduled manual post %s at %s", post_id, when.isoformat())

    def _publish_scheduled(self, post_id: int) -> None:
        with self._fire_lock:
            post = self.repo.get_manual_post(post_id)
            if post is None or post.status != "scheduled":
                return
            self.repo.set_manual_post_status(post_id, "publishing")
            product_url = post.product_url
            chat_id = post.chat_id
        try:
            ok, message = self.runner.publish_manual_url(product_url)
        except Exception as exc:
            logger.error("Scheduled manual post %s failed: %s", post_id, exc, exc_info=True)
            ok, message = False, f"Не удалось опубликовать: {exc}"
        self.repo.set_manual_post_status(post_id, "published" if ok else "failed", None if ok else message)
        self._send(chat_id, message)

    def _format_local(self, when: datetime) -> str:
        try:
            tz = zoneinfo.ZoneInfo(self.timezone_name)
        except Exception:
            tz = timezone.utc
        moment = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        return moment.astimezone(tz).strftime("%d.%m.%Y %H:%M")

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                updates = self._get_updates(timeout=25)
            except Exception as exc:
                logger.warning("Admin intake poll failed: %s", exc)
                if self._stop.wait(3):
                    return
                continue
            for update in updates:
                self._offset = int(update["update_id"]) + 1
                try:
                    self.handle_update(update)
                except Exception:
                    logger.exception("Failed to handle Telegram update %s", update.get("update_id"))

    def _discard_backlog(self) -> None:
        while not self._stop.is_set():
            updates = self._get_updates(timeout=0)
            if not updates:
                return
            self._offset = int(updates[-1]["update_id"]) + 1
            if len(updates) < 100:
                return

    def _delete_webhook(self) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/deleteWebhook"
        try:
            with httpx.Client(timeout=15.0) as client:
                client.post(url, json={"drop_pending_updates": False})
        except Exception as exc:
            logger.warning("Could not delete Telegram webhook: %s", exc)

    def _get_updates(self, timeout: int) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member"]),
        }
        if self._offset is not None:
            params["offset"] = self._offset
        with httpx.Client(timeout=timeout + 10) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload))
        updates = payload.get("result") or []
        return list(updates)

    def _send(self, chat_id: str, text: str) -> None:
        if self._sender is not None:
            self._sender(str(chat_id), text)
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code != 200:
                    logger.warning("Telegram sendMessage failed (%s): %s", response.status_code, response.text)
        except Exception as exc:
            logger.warning("Telegram sendMessage failed: %s", exc)


def extract_product_url(text: str) -> str | None:
    match = _URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(").,]>\"'")


def _command_name(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    first = stripped.split()[0].lower()
    return first.split("@", 1)[0]
