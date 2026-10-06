"""Private Telegram bot that lets admins schedule a product link.

Only the configured admin user ids can talk to the bot. An admin sends a
product URL, the bot asks when to publish, and the post goes out at that time
through the same pipeline as the regular schedule.
"""

from datetime import datetime, timedelta, timezone
import html
import json
import logging
import re
import threading
import zoneinfo
from typing import Any, Callable

import httpx

from core.publish_time import parse_publish_time
from publishers.telegram_discovery import TelegramChatDiscoveryService
from publishers.telegram_publisher import DEFAULT_BIO_FOOTER

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
        instagram_enabled: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.admin_user_ids = {int(user_id) for user_id in admin_user_ids}
        self.repo = repo
        self.runner = runner
        self.timezone_name = timezone_name or "UTC"
        self.scheduler = scheduler
        self._sender = sender
        self.instagram_enabled = instagram_enabled
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

    def _instagram_on(self) -> bool:
        """Live flag. The admin panel can turn Instagram back on without a code change."""
        config = getattr(self.runner, "config", None)
        instagram = getattr(config, "instagram", None)
        enabled = getattr(instagram, "enabled", None)
        if enabled is not None:
            return bool(enabled)
        return self.instagram_enabled

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
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            self._handle_callback_query(callback_query)
            return
        message = update.get("message")
        if isinstance(message, dict) and (message.get("chat") or {}).get("type") == "private":
            self._handle_private_message(message)
            return
        self.discovery._process_single_update(update)

    def _handle_callback_query(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        from_user = query.get("from") or {}
        user_id = int(from_user.get("id") or 0)
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or user_id)
        data = str(query.get("data") or "")

        if user_id not in self.admin_user_ids:
            self._answer_callback(query_id, "Доступ только для администраторов.")
            return

        self._answer_callback(query_id, "Обрабатываю...")

        if data.startswith("dup_approve:"):
            try:
                approval_id = int(data.split(":")[1])
            except (ValueError, IndexError):
                return
            self._send(chat_id, f"✅ Одобрена повторная публикация #{approval_id}. Начинаю публикацию...")
            try:
                ok, msg = self.runner.publish_approved_duplicate(approval_id)
            except Exception as exc:
                ok, msg = False, f"Ошибка публикации: {exc}"
            self._send(chat_id, msg or ("Товар успешно опубликован!" if ok else "Ошибка публикации."))

        elif data.startswith("dup_reject:"):
            try:
                approval_id = int(data.split(":")[1])
            except (ValueError, IndexError):
                return
            if hasattr(self.repo, "resolve_duplicate_approval"):
                self.repo.resolve_duplicate_approval(approval_id, "rejected")
            self._send(chat_id, f"❌ Повторная публикация #{approval_id} отклонена. Выбираю следующий подходящий товар из базы...")
            try:
                ok, msg = self.runner.publish_next_eligible_product()
            except Exception as exc:
                ok, msg = False, f"Ошибка при выборе следующего товара: {exc}"
            self._send(chat_id, msg or ("Следующий товар опубликован!" if ok else "Нет доступных товаров."))

        elif data.startswith("desc:auto:"):
            try:
                post_id = int(data.split(":")[2])
            except (ValueError, IndexError):
                return
            post = self.repo.get_manual_post(post_id)
            if post is None:
                self._send(chat_id, "Ошибка: черновик не найден.")
                return
            self.repo.set_manual_post_status(post_id, "awaiting_time")
            self._send(
                chat_id,
                f"Описание принято!\n"
                f"В какое время выложить пост?\n"
                f"Например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас».\n"
                f"Часовой пояс: {self.timezone_name}."
            )

        elif data.startswith("dest:"):
            try:
                parts = data.split(":")
                destination = parts[1]
                post_id = int(parts[2])
            except (ValueError, IndexError):
                return
            if not self._instagram_on():
                destination = "telegram"
            self.repo.set_manual_post_destination(post_id, destination, next_status="awaiting_approval")
            self._send_prerender(chat_id, post_id)

        elif data.startswith("approve:"):
            try:
                post_id = int(data.split(":")[1])
            except (ValueError, IndexError):
                return
            reply = self._finalize_schedule(post_id)
            if reply:
                self._send(chat_id, reply)

        elif data.startswith("edit:desc:"):
            try:
                post_id = int(data.split(":")[2])
            except (ValueError, IndexError):
                return
            self.repo.set_manual_post_status(post_id, "awaiting_description")
            self._send(chat_id, "Пришлите новое краткое описание (Часть 1) для этого поста:")

        elif data.startswith("edit:time:"):
            try:
                post_id = int(data.split(":")[2])
            except (ValueError, IndexError):
                return
            self.repo.set_manual_post_status(post_id, "awaiting_time")
            self._send(
                chat_id,
                f"В какое время выложить пост?\n"
                f"Например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас».\n"
                f"Часовой пояс: {self.timezone_name}."
            )

        elif data.startswith("edit:dest:"):
            try:
                post_id = int(data.split(":")[2])
            except (ValueError, IndexError):
                return
            self.repo.set_manual_post_status(post_id, "awaiting_destination")
            self._send(chat_id, "Куда опубликовать пост?", reply_markup=_destination_keyboard(post_id))

        elif data.startswith("cancel:"):
            try:
                post_id = int(data.split(":")[1])
            except (ValueError, IndexError):
                return
            self.repo.set_manual_post_status(post_id, "cancelled")
            self._send(chat_id, "❌ Публикация отменена. Пришлите новую ссылку, когда будете готовы.")

    def _answer_callback(self, query_id: str, text: str = "") -> None:
        if not query_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(url, json={"callback_query_id": query_id, "text": text})
        except Exception as exc:
            logger.debug("Failed to answer callback query %s: %s", query_id, exc)

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
            if self._instagram_on():
                return (
                    "Пришлите ссылку на товар. Можно сразу следом написать свою первую часть описания. "
                    "Я спрошу время и канал, покажу предпросмотр и запланирую отправку. "
                    "Контакты добавятся сами."
                )
            return (
                "Пришлите ссылку на товар. Можно сразу следом написать свою первую часть описания. "
                "Я спрошу время, покажу предпросмотр и запланирую отправку в Telegram. "
                "Контакты добавятся сами. Instagram сейчас выключен."
            )
        if command == "/cancel":
            cancelled = self.repo.cancel_awaiting_manual(str(user_id))
            if cancelled:
                return "Отменено. Пришлите новую ссылку, когда будете готовы."
            return "Сейчас нечего отменять. Пришлите ссылку на товар."

        product_url = extract_product_url(text)
        if product_url:
            return self._accept_link(
                user_id,
                chat_id,
                product_url,
                caption=caption_after_url(text, product_url),
            )
        if not text.strip():
            return "Пришлите ссылку на товар."

        draft = self.repo.get_awaiting_manual(str(user_id))
        if draft is None:
            return "Сначала пришлите ссылку на товар."

        if draft.status == "awaiting_description":
            is_time = False
            if draft.custom_description:
                try:
                    parse_publish_time(text, datetime.now(timezone.utc), self.timezone_name)
                    is_time = True
                except Exception:
                    pass
            if is_time:
                return self._accept_time(user_id, chat_id, text)

            self.repo.set_manual_post_description(draft.id, text.strip(), next_status="awaiting_time")
            return (
                "Описание сохранено!\n"
                "В какое время выложить пост?\n"
                "Например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас».\n"
                f"Часовой пояс: {self.timezone_name}."
            )

        if draft.status == "awaiting_time":
            return self._accept_time(user_id, chat_id, text)

        if draft.status == "awaiting_destination":
            dest = "telegram" if not self._instagram_on() else _parse_destination(text)
            if dest:
                self.repo.set_manual_post_destination(draft.id, dest, next_status="awaiting_approval")
                self._send_prerender(chat_id, draft.id)
                return ""
            self._send(
                chat_id,
                "Куда опубликовать пост?\nВыберите Telegram, Instagram или Везде:",
                reply_markup=_destination_keyboard(draft.id),
            )
            return ""

        if draft.status == "awaiting_approval":
            return (
                "Пост ожидает подтверждения. Нажмите «✅ Подтвердить и запланировать» выше или выберите, что нужно изменить."
            )

        return self._accept_time(user_id, chat_id, text)

    def _accept_link(self, user_id: int, chat_id: str, product_url: str, caption: str = "") -> str:
        from core.dedup import extract_duplicate_signatures
        from adapters.playwright_url_processor import process_product_url_with_playwright

        original_url = product_url
        try:
            processed_url, _ = process_product_url_with_playwright(original_url)
        except Exception as exc:
            logger.warning("Playwright URL processing fell back to input: %s", exc)
            processed_url = original_url

        open_post = self.repo.find_open_manual_by_url(processed_url) or self.repo.find_open_manual_by_url(original_url)
        if open_post and open_post.status in {"scheduled", "publishing"} and open_post.publish_at:
            when = self._format_local(open_post.publish_at)
            return f"Эта ссылка уже стоит в очереди на {when} ({self.timezone_name})."

        signatures = extract_duplicate_signatures("", "", processed_url, "") | extract_duplicate_signatures("", "", original_url, "")
        if signatures & self.repo.get_published_signatures():
            appr_rec = None
            if hasattr(self.repo, "create_duplicate_approval"):
                appr_rec = self.repo.create_duplicate_approval(
                    external_id=f"manual_{int(datetime.now(timezone.utc).timestamp())}",
                    title=f"Товар по ссылке {processed_url[:60]}...",
                    source="manual",
                    telegram_url=processed_url,
                )
            if appr_rec:
                self._send_duplicate_warning(
                    chat_id=chat_id,
                    approval_id=appr_rec.id,
                    url=processed_url,
                )
            return (
                "⚠️ Этот товар уже публиковался в Telegram-канале!\n"
                "Выше отправлен запрос с кнопками [Опубликовать повторно] и [Отклонить]."
            )

        draft_data = None
        if hasattr(self.runner, "prepare_manual_draft_data"):
            try:
                draft_data = self.runner.prepare_manual_draft_data(processed_url)
            except Exception as exc:
                logger.warning("prepare_manual_draft_data failed: %s", exc)

        auto_desc = (draft_data.get("description") or "").strip() if draft_data else ""
        photo_url = draft_data.get("photo_url") if draft_data else None
        own_caption = caption.strip()
        if own_caption:
            auto_desc = ""

        draft = self.repo.create_manual_draft(
            str(user_id),
            chat_id,
            processed_url,
            original_product_url=original_url,
            custom_description=auto_desc if auto_desc else None,
            photo_url=photo_url,
        )

        if own_caption:
            self.repo.set_manual_post_description(draft.id, own_caption, next_status="awaiting_time")
            return (
                "Ссылку и описание принял.\n"
                "В какое время выложить пост?\n"
                "Например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас».\n"
                f"Часовой пояс: {self.timezone_name}.\n\n"
                "Контакты и ссылки добавятся автоматически во 2-й части."
            )

        if auto_desc:
            text = (
                "Ссылку принял и обработал.\n\n"
                "📝 <b>Краткое описание товара (Часть 1):</b>\n\n"
                f"{auto_desc}\n\n"
                "Нажмите <b>«✅ Использовать это описание»</b> или отправьте свой вариант текста в ответном сообщении.\n\n"
                "<i>(Контакты и ссылки добавятся автоматически во 2-й части)</i>"
            )
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "✅ Использовать это описание", "callback_data": f"desc:auto:{draft.id}"}],
                    [{"text": "❌ Отмена", "callback_data": f"cancel:{draft.id}"}],
                ]
            }
            if photo_url:
                self._send_photo(chat_id, photo_url)
            self._send(chat_id, text, reply_markup=reply_markup)
            return ""

        text = (
            "Ссылку принял. Введите краткое описание товара (Часть 1):\n\n"
            "Например:\n"
            "Слингбэки с вышивкой-98$\n"
            "Размеры с 35 по 42.\n"
            "Высота каблука 4,5 см.\n"
            "Цвет: черный.\n\n"
            "<i>(Контакты и ссылки добавятся автоматически во 2-й части)</i>"
        )
        self._send(chat_id, text)
        return ""

    def _send_duplicate_warning(self, chat_id: str, approval_id: int, url: str) -> None:
        text = (
            f"⚠️ <b>ВНИМАНИЕ: ПОВТОРНАЯ ПУБЛИКАЦИЯ</b>\n\n"
            f"Ссылка на товар уже встречалась среди опубликованных постов:\n"
            f"<code>{url}</code>\n\n"
            f"Выберите действие:"
        )
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Опубликовать повторно", "callback_data": f"dup_approve:{approval_id}"},
                    {"text": "❌ Отклонить", "callback_data": f"dup_reject:{approval_id}"},
                ]
            ]
        }
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=15.0) as client:
                client.post(
                    api_url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup,
                        "disable_web_page_preview": True,
                    },
                )
        except Exception as exc:
            logger.warning("Failed to send duplicate warning inline message: %s", exc)

    def _send_prerender(self, chat_id: str, post_id: int) -> None:
        post = self.repo.get_manual_post(post_id)
        if post is None:
            self._send(chat_id, "Ошибка: черновик не найден.")
            return

        channel_labels = {
            "telegram": "Telegram",
            "instagram": "Instagram",
            "both": "Telegram и Instagram",
        }
        dest_label = channel_labels.get(post.target_channel, post.target_channel)

        now = datetime.now(timezone.utc)
        run_at = post.publish_at or now
        is_immediate = (run_at <= now + timedelta(seconds=5))
        time_label = "сейчас" if is_immediate else self._format_local(run_at)

        part1 = (post.custom_description or "").strip()
        part2 = DEFAULT_BIO_FOOTER.strip()

        part1_esc = html.escape(part1)
        part2_esc = html.escape(part2)
        dest_esc = html.escape(dest_label)
        time_esc = html.escape(time_label)

        preview_caption = f"{part1_esc}\n\n{part2_esc}" if part1_esc else part2_esc

        text = (
            f"🔍 <b>Предпросмотр поста перед публикацией</b>\n\n"
            f"📍 Канал: <b>{dest_esc}</b>\n"
            f"⏰ Время: <b>{time_esc}</b> ({self.timezone_name})\n\n"
            f"👇 <b>Текст поста:</b>\n"
            f"----------------------------------------\n"
            f"{preview_caption}\n"
            f"----------------------------------------\n\n"
            f"Проверьте правильность и подтвердите публикацию:"
        )

        row_edit = [
            {"text": "✏️ Изменить описание", "callback_data": f"edit:desc:{post.id}"},
            {"text": "⏰ Изменить время", "callback_data": f"edit:time:{post.id}"},
        ]
        row_last = [{"text": "❌ Отмена", "callback_data": f"cancel:{post.id}"}]
        if self._instagram_on():
            row_last.insert(0, {"text": "🌐 Изменить канал", "callback_data": f"edit:dest:{post.id}"})
        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ Подтвердить и запланировать", "callback_data": f"approve:{post.id}"}],
                row_edit,
                row_last,
            ]
        }
        if getattr(post, "photo_url", None):
            self._send_photo(chat_id, post.photo_url)
        self._send(chat_id, text, reply_markup=keyboard)

    def _finalize_schedule(self, post_id: int, destination: str | None = None) -> str:
        post = self.repo.get_manual_post(post_id)
        if post is None:
            return "Не удалось найти пост. Пришлите ссылку ещё раз."

        dest = destination or getattr(post, "target_channel", "both") or "both"
        scheduled = self.repo.schedule_manual_post(
            post_id,
            target_channel=dest,
            custom_description=getattr(post, "custom_description", None),
        )
        if scheduled is None:
            return "Не удалось сохранить настройки. Пришлите ссылку ещё раз."

        run_at = scheduled.publish_at or datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        is_immediate = (run_at <= now + timedelta(seconds=5))
        if is_immediate:
            run_at = now + timedelta(seconds=1)

        self._arm_job(scheduled.id, run_at)

        channel_labels = {
            "telegram": "Telegram",
            "instagram": "Instagram",
            "both": "Telegram и Instagram",
        }
        dest_label = channel_labels.get(dest, dest)

        if is_immediate:
            return f"Публикую сейчас в {dest_label}"
        return f"Поставил пост на {self._format_local(scheduled.publish_at)} в {dest_label}"

    def _accept_time(self, user_id: int, chat_id: str, text: str | None = None) -> str:
        if text is None:
            text = chat_id
            chat_id = str(user_id)

        draft = self.repo.get_awaiting_manual(str(user_id))
        if draft is None:
            return "Сначала пришлите ссылку на товар."

        if draft.status == "awaiting_destination":
            dest = "telegram" if not self._instagram_on() else _parse_destination(text)
            if dest:
                self.repo.set_manual_post_destination(draft.id, dest, next_status="awaiting_approval")
                self._send_prerender(chat_id, draft.id)
                return ""

        words = text.strip().split()
        dest_override = None
        time_text = text
        if len(words) >= 2:
            last_word = words[-1]
            dest_candidate = _parse_destination(last_word)
            if dest_candidate:
                time_candidate = " ".join(words[:-1])
                try:
                    parse_publish_time(time_candidate, datetime.now(timezone.utc), self.timezone_name)
                    dest_override = dest_candidate
                    time_text = time_candidate
                except Exception:
                    pass

        try:
            parsed = parse_publish_time(time_text, datetime.now(timezone.utc), self.timezone_name)
        except ValueError as exc:
            if draft.status == "awaiting_destination":
                self._send(
                    chat_id,
                    "Куда опубликовать пост?\nВыберите Telegram, Instagram или Везде:",
                    reply_markup=_destination_keyboard(draft.id),
                )
                return ""
            return str(exc)

        time_val = parsed.when
        if parsed.note == "сейчас":
            time_val = datetime.now(timezone.utc)

        if not self._instagram_on():
            dest_override = "telegram"

        if dest_override:
            self.repo.set_manual_post_time(draft.id, time_val, next_status="awaiting_approval")
            self.repo.set_manual_post_destination(draft.id, dest_override, next_status="awaiting_approval")
            self._send_prerender(chat_id, draft.id)
            return ""

        updated = self.repo.set_manual_post_time(draft.id, time_val, next_status="awaiting_destination")
        if updated is None:
            return "Не удалось сохранить время. Пришлите ссылку ещё раз."

        time_label = "сейчас" if parsed.note == "сейчас" else self._format_local(parsed.when)
        prompt_text = (
            f"Время: {time_label} ({self.timezone_name}).\n"
            f"Куда опубликовать пост?"
        )
        self._send(chat_id, prompt_text, reply_markup=_destination_keyboard(draft.id))
        return ""

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

    def _send_photo(self, chat_id: str, photo: str, caption: str = "", reply_markup: dict[str, Any] | None = None) -> bool:
        if self._sender is not None:
            return True
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "photo": photo,
        }
        if caption:
            payload["caption"] = caption
            payload["parse_mode"] = "HTML"
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as exc:
            logger.debug("Failed to send preview photo %s: %s", photo, exc)
            return False

    def _publish_scheduled(self, post_id: int) -> None:
        with self._fire_lock:
            post = self.repo.get_manual_post(post_id)
            if post is None or post.status != "scheduled":
                return
            self.repo.set_manual_post_status(post_id, "publishing")
            product_url = post.product_url
            chat_id = post.chat_id
            target_channel = getattr(post, "target_channel", "both") or "both"
            custom_desc = getattr(post, "custom_description", None)

        publishers_filter = None if target_channel in ("both", "all") else target_channel
        try:
            ok, message = self.runner.publish_manual_url(
                product_url,
                on_platform=lambda platform, success, detail: self._send(
                    chat_id,
                    _platform_notice(platform, success, detail),
                ),
                publishers_filter=publishers_filter,
                custom_description=custom_desc,
            )
        except Exception as exc:
            logger.error("Scheduled manual post %s failed: %s", post_id, exc, exc_info=True)
            ok, message = False, f"Не удалось опубликовать: {exc}"
        self.repo.set_manual_post_status(post_id, "published" if ok else "failed", None if ok else message)
        if message:
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
            "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member", "callback_query"]),
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

    def _send(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        if self._sender is not None:
            self._sender(str(chat_id), text)
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(url, json=payload)
                if response.status_code == 400 and parse_mode:
                    del payload["parse_mode"]
                    response = client.post(url, json=payload)
                if response.status_code != 200:
                    logger.warning("Telegram sendMessage failed (%s): %s", response.status_code, response.text)
        except Exception as exc:
            logger.warning("Telegram sendMessage failed: %s", exc)


def _parse_destination(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"тг", "tg", "telegram", "телеграм", "телеграмм", "в тг", "в телеграм"}:
        return "telegram"
    if t in {"инста", "инст", "ig", "instagram", "инстаграм", "в инсту", "в инстаграм"}:
        return "instagram"
    if t in {"оба", "обе", "везде", "все", "всё", "both", "all", "в оба", "в обе"}:
        return "both"
    return None


def _destination_keyboard(post_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✈️ Telegram", "callback_data": f"dest:telegram:{post_id}"},
                {"text": "📸 Instagram", "callback_data": f"dest:instagram:{post_id}"},
            ],
            [
                {"text": "🌐 Везде (TG + IG)", "callback_data": f"dest:both:{post_id}"},
            ],
        ]
    }


def _platform_notice(platform: str, success: bool, detail: str) -> str:
    label = "ТГ" if platform == "telegram" else "Инсте"
    if success:
        return f"Опубликован в {label}"
    reason = (detail or "неизвестная ошибка").strip()
    return f"Не удалось опубликовать в {label}: {reason}"


def caption_after_url(text: str, url: str) -> str:
    """The admin's own part 1, written in the same message after the product link."""
    if not text or not url:
        return ""
    idx = text.lower().find(url.lower())
    if idx < 0:
        return ""
    rest = text[idx + len(url):]
    return rest.strip().lstrip(").,]>\"'").strip()


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
