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

from core.color_variants import link_names_color
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
        # Hints for the dialogue only. Lost on restart, the bot then simply
        # shows the stored text with its «use this description» button.
        self._own_caption: dict[int, bool] = {}
        self._photo_counts: dict[int, int] = {}
        self._size_counts: dict[int, int | None] = {}

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

        elif data.startswith("color:"):
            try:
                _, raw_id, choice = data.split(":", 2)
                post_id = int(raw_id)
            except ValueError:
                return
            reply = self._choose_color(chat_id, post_id, choice)
            if reply:
                self._send(chat_id, reply)

        elif data.startswith("repeat:"):
            try:
                _, answer, raw_id = data.split(":", 2)
                post_id = int(raw_id)
            except ValueError:
                return
            reply = self._answer_repeat(chat_id, post_id, answer == "yes")
            if reply:
                self._send(chat_id, reply)

        elif data.startswith("desc:auto:"):
            try:
                post_id = int(data.split(":")[2])
            except (ValueError, IndexError):
                return
            post = self.repo.get_manual_post(post_id)
            if post is None:
                self._send(chat_id, "Ошибка: черновик не найден.")
                return
            if len(post.publish_urls()) > 1:
                # Each colour gets its own sizes and colour line when it is published.
                self.repo.update_manual_post(post_id, status="awaiting_time", clear_description=True)
            else:
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
                    "Если у товара несколько цветов, я покажу их все и спрошу, какой публиковать (или все сразу). "
                    "Потом спрошу время и канал, покажу предпросмотр и запланирую отправку. "
                    "Контакты добавятся сами."
                )
            return (
                "Пришлите ссылку на товар. Можно сразу следом написать свою первую часть описания. "
                "Если у товара несколько цветов, я покажу их все и спрошу, какой публиковать (или все сразу). "
                "Потом спрошу время, покажу предпросмотр и запланирую отправку в Telegram. "
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

        if draft.status == "awaiting_color":
            choice = _parse_color_choice(text, len(draft.variants))
            if choice is None:
                self._send_color_menu(chat_id, draft.id)
                return ""
            return self._choose_color(chat_id, draft.id, choice)

        if draft.status == "awaiting_repeat":
            answer = text.strip().lower()
            if answer in {"да", "yes", "ок", "ok", "повтор", "повторно"}:
                return self._answer_repeat(chat_id, draft.id, True)
            if answer in {"нет", "no"}:
                return self._answer_repeat(chat_id, draft.id, False)
            return "Этот товар уже публиковался. Нажмите «Да, опубликовать повторно» или «Нет» выше."

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
        original_url = product_url
        queued = self._queued_reply(original_url)
        if queued:
            return queued

        # Reading a store page takes a few seconds; say so at once.
        self._send(chat_id, "⏳ Открываю страницу товара: смотрю цвета, фото и размеры…")
        draft_data = self._prepare_draft_data(original_url)
        processed_url = str(draft_data.get("processed_url") or original_url)
        if processed_url != original_url:
            queued = self._queued_reply(processed_url)
            if queued:
                return queued

        own_caption = caption.strip()
        colors = [dict(item) for item in draft_data.get("colors") or [] if item.get("url")]
        if len(colors) >= 2:
            for item in colors:
                if item.get("selected"):
                    # This colour's page is already read; picking it costs nothing.
                    item["description"] = (draft_data.get("description") or "").strip()
                    item["photo_url"] = draft_data.get("photo_url")
                    item["photo_count"] = draft_data.get("photo_count")
                    item["size_count"] = draft_data.get("size_count")
            draft = self.repo.create_manual_draft(
                str(user_id),
                chat_id,
                processed_url,
                original_product_url=original_url,
                custom_description=own_caption or None,
                photo_url=draft_data.get("photo_url"),
                variants=colors,
                status="awaiting_color",
            )
            self._send_color_menu(chat_id, draft.id)
            return ""

        draft = self.repo.create_manual_draft(
            str(user_id),
            chat_id,
            processed_url,
            original_product_url=original_url,
            custom_description=own_caption or None,
            photo_url=draft_data.get("photo_url"),
            status="awaiting_description",
        )
        return self._after_color_chosen(
            chat_id,
            draft.id,
            auto_desc=(draft_data.get("description") or "").strip(),
            photo_url=draft_data.get("photo_url"),
            photo_count=draft_data.get("photo_count"),
            size_count=draft_data.get("size_count"),
            also_check=[original_url],
        )

    def _queued_reply(self, url: str) -> str:
        open_post = self.repo.find_open_manual_by_url(url)
        if open_post and open_post.status in {"scheduled", "publishing"} and open_post.publish_at:
            when = self._format_local(open_post.publish_at)
            return f"Эта ссылка уже стоит в очереди на {when} ({self.timezone_name})."
        return ""

    def _prepare_draft_data(self, url: str) -> dict[str, Any]:
        if not hasattr(self.runner, "prepare_manual_draft_data"):
            return {}
        try:
            return self.runner.prepare_manual_draft_data(url) or {}
        except Exception as exc:
            logger.warning("prepare_manual_draft_data failed for %s: %s", url, exc)
            return {}

    def _is_published(self, *urls: str, published: set[str] | None = None) -> bool:
        from core.dedup import extract_duplicate_signatures

        signatures: set[str] = set()
        for url in urls:
            if url:
                signatures |= extract_duplicate_signatures("", "", url, "")
        if published is None:
            published = self.repo.get_published_signatures()
        return bool(signatures & published)

    def _color_states(self, post: Any) -> list[dict[str, Any]]:
        """Each colour of the draft with whether it is in the channel or in the queue already."""
        states: list[dict[str, Any]] = []
        bare_link = post.original_product_url or ""
        published = self.repo.get_published_signatures()
        for item in post.variants:
            url = str(item.get("url") or "")
            # A bare link opened this colour, and older posts were saved under the bare link.
            extra = bare_link if item.get("selected") and not link_names_color(bare_link) else ""
            queued = self.repo.find_open_manual_by_url(url)
            states.append({
                **item,
                "published": self._is_published(url, extra, published=published),
                "queued": bool(queued and queued.status in {"scheduled", "publishing"} and queued.id != post.id),
            })
        return states

    def _send_color_menu(self, chat_id: str, post_id: int) -> None:
        post = self.repo.get_manual_post(post_id)
        if post is None or not post.variants:
            self._send(chat_id, "Ошибка: черновик не найден. Пришлите ссылку ещё раз.")
            return
        states = self._color_states(post)
        lines = [f"🎨 <b>У товара {len(states)} {_plural_colors(len(states))}:</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(states):
            name = _color_label(item)
            marks: list[str] = []
            if item.get("selected"):
                marks.append("по вашей ссылке")
            if item.get("published"):
                marks.append("✅ уже публиковался")
            if item.get("queued"):
                marks.append("⏳ уже в очереди")
            suffix = f" — {', '.join(marks)}" if marks else ""
            lines.append(f"{index + 1}. {html.escape(name)}{suffix}")
            button = name
            if item.get("selected"):
                button = f"👉 {button}"
            if item.get("published"):
                button = f"{button} · был"
            buttons.append([{"text": button[:60], "callback_data": f"color:{post.id}:{index}"}])

        fresh = [item for item in states if not item.get("published")]
        if len(fresh) == len(states):
            buttons.append([{"text": f"🎨 Все цвета ({len(states)}) — отдельными постами", "callback_data": f"color:{post.id}:all"}])
        else:
            if len(fresh) >= 2:
                buttons.append([{"text": f"🎨 Все новые цвета ({len(fresh)} из {len(states)})", "callback_data": f"color:{post.id}:all"}])
            buttons.append([{"text": f"🔁 Все {len(states)}, включая опубликованные", "callback_data": f"color:{post.id}:allrep"}])
        buttons.append([{"text": "❌ Отмена", "callback_data": f"cancel:{post.id}"}])

        lines += ["", "Какой цвет опубликовать? Можно ответить номером или словом «все»."]
        if post.photo_url:
            self._send_photo(chat_id, post.photo_url)
        self._send(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": buttons})

    def _choose_color(self, chat_id: str, post_id: int, choice: str) -> str:
        post = self.repo.get_manual_post(post_id)
        if post is None or not post.variants:
            return "Ошибка: черновик не найден. Пришлите ссылку ещё раз."
        if post.status != "awaiting_color":
            return "Цвет для этого поста уже выбран. Чтобы начать заново, пришлите ссылку ещё раз."
        states = self._color_states(post)

        if choice in {"all", "allrep"}:
            repeat = choice == "allrep"
            chosen = states if repeat else [item for item in states if not item.get("published")]
            if not chosen:
                self._send_color_menu(chat_id, post_id)
                return "Все цвета уже публиковались. Выберите «Все, включая опубликованные» или один цвет."
            urls = [str(item["url"]) for item in chosen]
            first = chosen[0]
            self.repo.update_manual_post(
                post_id,
                product_url=urls[0],
                variant_urls=urls,
                allow_repeat=repeat,
                photo_url=first.get("photo_url") or post.photo_url,
            )
            names = ", ".join(_color_label(item) for item in chosen)
            self._send(chat_id, f"Беру {len(chosen)} {_plural_colors(len(chosen))}: {html.escape(names)}.\nКаждый цвет выйдет отдельным постом со своими фото.")
            return self._offer_description(chat_id, post_id, multi=True)

        try:
            index = int(choice)
            item = states[index]
        except (ValueError, IndexError):
            return "Не понял выбор цвета. Нажмите кнопку с цветом."
        url = str(item["url"])
        self.repo.update_manual_post(post_id, product_url=url, variant_urls=[])
        auto_desc = (item.get("description") or "").strip()
        photo_url = item.get("photo_url")
        photo_count = item.get("photo_count")
        size_count = item.get("size_count")
        if not auto_desc and not post.custom_description:
            self._send(chat_id, f"⏳ Готовлю описание для цвета «{html.escape(_color_label(item))}»…")
            data = self._prepare_draft_data(url)
            auto_desc = (data.get("description") or "").strip()
            photo_url = data.get("photo_url") or photo_url
            photo_count = data.get("photo_count") or photo_count
            size_count = data.get("size_count", size_count)
        extra = [post.original_product_url] if item.get("selected") and post.original_product_url else []
        return self._after_color_chosen(
            chat_id,
            post_id,
            auto_desc=auto_desc,
            photo_url=photo_url,
            photo_count=photo_count,
            size_count=size_count,
            also_check=[u for u in extra if not link_names_color(u)],
            color_name=_color_label(item),
        )

    def _after_color_chosen(
        self,
        chat_id: str,
        post_id: int,
        auto_desc: str = "",
        photo_url: str | None = None,
        photo_count: int | None = None,
        size_count: int | None = None,
        also_check: list[str] | None = None,
        color_name: str = "",
    ) -> str:
        """The colour is known: ask about a repeat if it is in the channel, then about the text."""
        post = self.repo.get_manual_post(post_id)
        if post is None:
            return "Ошибка: черновик не найден. Пришлите ссылку ещё раз."
        own_caption = bool(post.custom_description)
        changes: dict[str, Any] = {}
        if photo_url:
            changes["photo_url"] = photo_url
        if auto_desc and not own_caption:
            changes["custom_description"] = auto_desc
        if changes:
            self.repo.update_manual_post(post_id, **changes)
        self._photo_counts[post_id] = photo_count or 0
        self._size_counts[post_id] = size_count
        self._own_caption[post_id] = own_caption

        if self._is_published(post.product_url, *(also_check or [])):
            self.repo.update_manual_post(post_id, status="awaiting_repeat")
            label = f"цвет «{html.escape(color_name)}»" if color_name else "товар"
            self._send(
                chat_id,
                f"⚠️ Этот {label} уже публиковался в канале.\n"
                f"<code>{html.escape(post.product_url)}</code>\n\n"
                "Опубликовать его ещё раз?",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "✅ Да, опубликовать повторно", "callback_data": f"repeat:yes:{post_id}"}],
                        [{"text": "🎨 Выбрать другой цвет" if post.variants else "❌ Нет", "callback_data": f"repeat:no:{post_id}"}],
                    ]
                },
            )
            return "⚠️ Этот товар уже публиковался. Подтвердите повтор кнопкой выше."
        return self._offer_description(chat_id, post_id)

    def _answer_repeat(self, chat_id: str, post_id: int, approved: bool) -> str:
        post = self.repo.get_manual_post(post_id)
        if post is None or post.status != "awaiting_repeat":
            return "Этот вопрос уже закрыт. Пришлите ссылку ещё раз, если нужно."
        if approved:
            self.repo.update_manual_post(post_id, allow_repeat=True)
            self._send(chat_id, "🔁 Хорошо, публикуем повторно.")
            return self._offer_description(chat_id, post_id)
        if post.variants:
            own = self._own_caption.get(post_id, False)
            self.repo.update_manual_post(
                post_id,
                status="awaiting_color",
                product_url=str(post.variants[0].get("url") or post.product_url),
                clear_description=not own,
            )
            self._send_color_menu(chat_id, post_id)
            return ""
        self.repo.set_manual_post_status(post_id, "cancelled")
        return "❌ Повтор не публикую. Пришлите другую ссылку, когда будете готовы."

    def _offer_description(self, chat_id: str, post_id: int, multi: bool = False) -> str:
        post = self.repo.get_manual_post(post_id)
        if post is None:
            return "Ошибка: черновик не найден. Пришлите ссылку ещё раз."
        # Before the colour is settled the draft holds only the admin's own text.
        # Later it may hold the auto text as well; the dialogue remembers which.
        own_caption = bool(post.custom_description) if multi else self._own_caption.get(post_id, False)
        photo_count = self._photo_counts.get(post_id) or 0
        photo_line = f"📸 Фото на сайте: {photo_count}. В пост пойдут все.\n" if photo_count else ""
        photo_line += _read_warnings(photo_count, self._size_counts.get(post_id))
        photo_line += "\n" if photo_line else ""

        if own_caption:
            self.repo.update_manual_post(post_id, status="awaiting_time")
            colour_note = (
                "Для каждого цвета строка «Цвет: …» подставится своя.\n" if len(post.publish_urls()) > 1 else ""
            )
            return (
                f"{photo_line}Ссылку и описание принял.\n{colour_note}"
                "В какое время выложить пост?\n"
                "Например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас».\n"
                f"Часовой пояс: {self.timezone_name}.\n\n"
                "Контакты и ссылки добавятся автоматически во 2-й части."
            )

        self.repo.update_manual_post(post_id, status="awaiting_description")
        if len(post.publish_urls()) > 1:
            sample = next(
                (str(item.get("description") or "") for item in post.variants if item.get("description")),
                "",
            )
            sample_block = f"Пример для одного цвета:\n\n{html.escape(sample)}\n\n" if sample else ""
            text = (
                "📝 <b>Описание (Часть 1)</b> соберу автоматически для каждого цвета: название, цена, размеры и цвет.\n\n"
                f"{sample_block}"
                "Нажмите <b>«✅ Использовать автоописание»</b> или отправьте свой текст — он пойдёт во все посты, "
                "а строка «Цвет: …» подставится своя.\n\n"
                "<i>(Контакты и ссылки добавятся автоматически во 2-й части)</i>"
            )
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "✅ Использовать автоописание", "callback_data": f"desc:auto:{post_id}"}],
                    [{"text": "❌ Отмена", "callback_data": f"cancel:{post_id}"}],
                ]
            }
            self._send(chat_id, text, reply_markup=reply_markup)
            return ""

        auto_desc = (post.custom_description or "").strip()
        if auto_desc:
            text = (
                f"{photo_line}Ссылку принял и обработал.\n\n"
                "📝 <b>Краткое описание товара (Часть 1):</b>\n\n"
                f"{html.escape(auto_desc)}\n\n"
                "Нажмите <b>«✅ Использовать это описание»</b> или отправьте свой вариант текста в ответном сообщении.\n\n"
                "<i>(Контакты и ссылки добавятся автоматически во 2-й части)</i>"
            )
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "✅ Использовать это описание", "callback_data": f"desc:auto:{post_id}"}],
                    [{"text": "❌ Отмена", "callback_data": f"cancel:{post_id}"}],
                ]
            }
            if post.photo_url:
                self._send_photo(chat_id, post.photo_url)
            self._send(chat_id, text, reply_markup=reply_markup)
            return ""

        text = (
            f"{photo_line}Ссылку принял. Введите краткое описание товара (Часть 1):\n\n"
            "Например:\n"
            "Слингбэки с вышивкой-98$\n"
            "Размеры с 35 по 42.\n"
            "Высота каблука 4,5 см.\n"
            "Цвет: черный.\n\n"
            "<i>(Контакты и ссылки добавятся автоматически во 2-й части)</i>"
        )
        self._send(chat_id, text)
        return ""

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
        urls = post.publish_urls()
        colour_line = ""
        if len(urls) > 1:
            names = [_color_label(item) for item in post.variants if str(item.get("url")) in urls]
            colour_line = f"🎨 Цвета: <b>{html.escape(', '.join(names))}</b> — {len(urls)} отдельных поста\n"
            if not part1:
                part1 = "(описание соберётся автоматически для каждого цвета)"
        elif post.variants:
            chosen = next((item for item in post.variants if str(item.get("url")) == post.product_url), None)
            if chosen:
                colour_line = f"🎨 Цвет: <b>{html.escape(_color_label(chosen))}</b>\n"
        if post.allow_repeat:
            colour_line += "🔁 Повторная публикация\n"

        part1_esc = html.escape(part1)
        part2_esc = html.escape(part2)
        dest_esc = html.escape(dest_label)
        time_esc = html.escape(time_label)

        preview_caption = f"{part1_esc}\n\n{part2_esc}" if part1_esc else part2_esc

        text = (
            f"🔍 <b>Предпросмотр поста перед публикацией</b>\n\n"
            f"📍 Канал: <b>{dest_esc}</b>\n"
            f"⏰ Время: <b>{time_esc}</b> ({self.timezone_name})\n"
            f"{colour_line}\n"
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
            chat_id = post.chat_id
            target_channel = getattr(post, "target_channel", "both") or "both"
            custom_desc = getattr(post, "custom_description", None)

        publishers_filter = None if target_channel in ("both", "all") else target_channel
        urls = post.publish_urls()
        names = {str(item.get("url")): _color_label(item) for item in post.variants}
        failures: list[str] = []
        for url in urls:
            color = names.get(url, "") if len(urls) > 1 else ""
            prefix = f"🎨 {color}: " if color else ""
            description = custom_desc
            if len(urls) > 1:
                if custom_desc:
                    russian = next((str(item.get("name_ru") or "") for item in post.variants if str(item.get("url")) == url), "")
                    description = swap_color_line(custom_desc, russian or color)
                else:
                    # The same text the single-colour preview offers, read from this colour's page.
                    description = (self._prepare_draft_data(url).get("description") or "").strip() or None
            try:
                ok, message = self.runner.publish_manual_url(
                    url,
                    on_platform=lambda platform, success, detail, prefix=prefix: self._send(
                        chat_id,
                        prefix + _platform_notice(platform, success, detail),
                    ),
                    publishers_filter=publishers_filter,
                    custom_description=description,
                    bypass_duplicate_gate=bool(post.allow_repeat),
                )
            except Exception as exc:
                logger.error("Scheduled manual post %s (%s) failed: %s", post_id, url, exc, exc_info=True)
                ok, message = False, f"Не удалось опубликовать: {exc}"
            if not ok:
                failures.append(f"{color or url}: {message}" if message else (color or url))
            if message:
                self._send(chat_id, prefix + message)

        if failures:
            self.repo.set_manual_post_status(post_id, "failed", "; ".join(failures)[:2000])
        else:
            self.repo.set_manual_post_status(post_id, "published")
        if len(urls) > 1:
            done = len(urls) - len(failures)
            self._send(chat_id, f"Готово: опубликовано {done} из {len(urls)} цветов.")

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


_COLOR_LINE = re.compile(r"^\s*цвет\s*:.*$", re.IGNORECASE | re.MULTILINE)


def _read_warnings(photo_count: int, size_count: int | None) -> str:
    """What the page read missed, for the admin to see before the post goes out."""
    lines = ""
    if photo_count == 1:
        lines += (
            "⚠️ На странице нашлось только 1 фото. Если на сайте их больше, "
            "пришлите ссылку ещё раз или напишите разработчику.\n"
        )
    if size_count == 0:
        lines += "ℹ️ Размеры на странице не найдены: строки с размерами в описании не будет.\n"
    return lines


def swap_color_line(text: str, color: str) -> str:
    """The admin's text for one colour of many: its «Цвет:» line names that colour."""
    if not color:
        return text
    line = f"Цвет: {color.strip().rstrip('.')}."
    if _COLOR_LINE.search(text):
        return _COLOR_LINE.sub(line, text, count=1)
    return f"{text.rstrip()}\n{line}"


def _color_label(item: dict[str, Any]) -> str:
    name = str(item.get("name_ru") or item.get("name") or "").strip()
    return name[:1].upper() + name[1:] if name else "Без названия"


def _plural_colors(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "цвет"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "цвета"
    return "цветов"


def _parse_color_choice(text: str, count: int) -> str | None:
    answer = (text or "").strip().lower()
    if answer in {"все", "всё", "all", "все цвета", "всё цвета"}:
        return "all"
    if answer.isdigit() and 1 <= int(answer) <= count:
        return str(int(answer) - 1)
    return None
