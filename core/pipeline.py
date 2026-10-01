"""Pipeline runner orchestrating the end-to-end publishing cycle.

Per SDD §2.2, §5, §6 and SRS FR-1 through FR-9.
Guarantees per-product error isolation, idempotency, hot config reloading,
and comprehensive audit logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
import logging
import re
import threading
from typing import Any, Callable, Protocol, runtime_checkable, TYPE_CHECKING

from adapters.base import RawProduct, SourceAdapter
from adapters.playwright_url_processor import process_product_url_with_playwright
from adapters.product_page import ProductPageError, fetch_product_page
from config.app_config import AppConfig
from core.composer import compose_post
from core.dedup import extract_duplicate_signatures, filter_unseen
from core.image_downloader import ImageDownloader
from core.moderation import ConfigurableModerationGate, ModerationGate
from core.outfits import OutfitCoordinator
from core.pricing import FxConverter, calculate_final_price, source_price_usd
from core.product_facts import (
    attach_site_facts,
    is_heeled_footwear,
    order_sizes,
    site_description,
)
from core.similarity import rank_products_by_channel_similarity
from llm.base import LLMProvider, PromptLoader, SelectionResult
from publishers.base import Publisher

if TYPE_CHECKING:
    from storage.repository import ProductRepository

logger = logging.getLogger(__name__)

# How many candidates a cycle may walk through looking for one whose store
# page still answers with its size grid.
SITE_FACTS_ATTEMPTS = 5


class SiteFactsUnavailable(Exception):
    """The store page did not give up the size grid the caption is built from.

    Raised before anything is published. A post whose caption is only the
    title and the price is worse than no post, so the cycle moves on to the
    next candidate instead.
    """

    def __init__(self, external_id: str, product_url: str | None) -> None:
        super().__init__(
            f"{external_id}: the store page did not return a size grid "
            f"({product_url or 'no product URL'})"
        )
        self.external_id = external_id
        self.product_url = product_url


class AlreadyPublished(Exception):
    """The channel has carried this product before.

    Raised before anything is published. The slot is not lost: the caller
    moves on to the next candidate in the queue and tells the admin which
    product was skipped.
    """

    def __init__(self, external_id: str, title: str, product_url: str | None) -> None:
        super().__init__(f"{external_id}: already published ({product_url or 'no product URL'})")
        self.external_id = external_id
        self.title = title
        self.product_url = product_url


def _carries_size_line(description: str) -> bool:
    """True when the copy already holds the «Размер…» line taken from the store page."""
    return any(line.strip().casefold().startswith("размер") for line in description.splitlines())


_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def _reads_in_russian(title: str) -> bool:
    """A store title has been translated once it carries Cyrillic."""
    return bool(_CYRILLIC.search(title))


def _telegram_link_for(record: Any) -> str | None:
    """Public t.me link saved for the product, or one rebuilt from the channel post id."""
    if record is None:
        return None
    saved = getattr(record, "telegram_message_url", None) or getattr(record, "telegramMessageUrl", None)
    if saved and str(saved).strip():
        return str(saved).strip()
    post_id = getattr(record, "telegram_post_id", None) or getattr(record, "telegram_message_id", None) or ""
    for part in str(post_id).split(","):
        part = part.strip()
        if part.startswith("@") and ":" in part:
            name, mid = part[1:].split(":", 1)
            if name and mid.isdigit():
                return f"https://t.me/{name}/{mid}"
    return None


@runtime_checkable
class AdminNotifierProtocol(Protocol):
    """Protocol for sending critical alert notifications."""
    def notify_critical(self, stage: str, message: str, external_id: str | None = None) -> None:
        ...


@dataclass
class CycleSummary:
    """Aggregated outcome of a single pipeline cycle execution."""
    fetched: int = 0
    unseen: int = 0
    selected: int = 0
    published: int = 0
    failed: int = 0
    pending_review: int = 0
    skipped_daily_cap: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class CollectionSummary:
    """Outcome of filling the queue from the brand sites, without publishing."""
    fetched: int = 0
    over_price: int = 0
    duplicates: int = 0
    stored: int = 0
    by_brand: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class PipelineRunner:
    """Orchestrator for the autonomous clothing post publishing pipeline."""

    def __init__(
        self,
        source: SourceAdapter,
        repo: ProductRepository,
        llm: LLMProvider,
        fx: FxConverter,
        publishers: list[Publisher],
        config: AppConfig,
        prompt_loader: PromptLoader,
        moderation_gate: ModerationGate | None = None,
        notifier: AdminNotifierProtocol | None = None,
    ) -> None:
        self.source = source
        self.repo = repo
        self.llm = llm
        self.fx = fx
        self.publishers = publishers
        self.config = config
        self.prompt_loader = prompt_loader
        self.moderation_gate = moderation_gate or ConfigurableModerationGate(
            enabled=config.moderation.enabled,
            auto_approve=config.moderation.auto_approve,
        )
        self.notifier = notifier
        self.image_downloader = ImageDownloader()
        self._cycle_lock = threading.Lock()

    def run_cycle(self) -> CycleSummary:
        """Execute one complete publishing cycle."""
        with self._cycle_lock:
            return self._run_cycle_locked()

    def run_telegram_cycle(self) -> CycleSummary:
        """Execute one complete publishing cycle for Telegram channel schedule."""
        with self._cycle_lock:
            return self._run_cycle_locked(publishers_filter="telegram")

    def run_instagram_cycle(self) -> CycleSummary:
        """Publish the next Telegram post that does not yet have an Instagram post."""
        with self._cycle_lock:
            return self._run_instagram_backlog_locked()

    def _run_instagram_backlog_locked(self) -> CycleSummary:
        """Instagram follows Telegram: same product, story link, then the carousel."""
        summary = CycleSummary()
        logger.info("=== Starting Instagram Cycle ===")
        try:
            self.config.reload_hot_fields()
        except Exception as exc:
            logger.warning("Failed to reload hot config fields: %s", exc)

        pending: list[RawProduct] = []
        getter = getattr(self.repo, "get_products_pending_instagram", None)
        if callable(getter):
            try:
                pending = list(getter(limit=1))
            except Exception as exc:
                logger.warning("Could not load products waiting for Instagram: %s", exc)
        if not pending:
            logger.info("No Telegram posts are waiting for Instagram. Instagram cycle complete.")
            return summary

        product = pending[0]
        description = ""
        record = None
        if hasattr(self.repo, "get_by_external_id"):
            try:
                record = self.repo.get_by_external_id(product.external_id)
            except Exception as exc:
                logger.warning("Could not load %s for Instagram: %s", product.external_id, exc)
        if record is not None:
            description = getattr(record, "description_gpt", None) or ""
        link = _telegram_link_for(record)
        logger.info(
            "Instagram cycle posting %s already in Telegram (%s)",
            product.external_id,
            link or "no public link yet",
        )
        summary.fetched = 1
        summary.unseen = 1
        summary.selected = 1
        try:
            self._process_single_product(
                product,
                description,
                summary,
                publishers_filter="instagram",
                bypass_duplicate_gate=True,
            )
        except SiteFactsUnavailable as exc:
            # The product keeps its place in the Instagram queue: the store
            # page may well answer on the next run.
            err_msg = f"Instagram post held back, {exc}"
            logger.warning(err_msg)
            summary.errors.append(err_msg)
        except Exception as exc:
            summary.failed += 1
            err_msg = f"Unhandled error posting {product.external_id} to Instagram: {exc}"
            logger.error(err_msg, exc_info=True)
            summary.errors.append(err_msg)
            self._notify_error("publish_instagram", err_msg, product.external_id)
        return summary

    def publish_next_eligible_product(self, publishers_filter: str | None = None) -> tuple[bool, str]:
        """Publish next eligible product when previous was rejected or skipped."""
        with self._cycle_lock:
            return self._publish_next_eligible_product_locked(publishers_filter=publishers_filter)

    def collect_new_products(self) -> CollectionSummary:
        """Read the brand sites and fill the queue. Nothing is published here."""
        with self._cycle_lock:
            return self._collect_new_products_locked()

    def _collect_new_products_locked(self) -> CollectionSummary:
        summary = CollectionSummary()
        logger.info("=== Collecting new products from the brand sites ===")
        try:
            self.config.reload_hot_fields()
        except Exception as exc:
            logger.warning("Failed to reload hot config fields before collecting: %s", exc)

        try:
            scraped = self.source.fetch_products()
        except Exception as exc:
            err_msg = f"Failed to fetch products from source adapter: {exc}"
            logger.error(err_msg, exc_info=True)
            summary.errors.append(err_msg)
            self._notify_error("ingestion", err_msg)
            return summary
        summary.fetched = len(scraped)

        paused_brands = self.repo.get_paused_brands() if hasattr(self.repo, "get_paused_brands") else set()
        if paused_brands:
            scraped = [p for p in scraped if p.source.lower() not in paused_brands]

        affordable, rejected = self._split_by_source_price(scraped)
        summary.over_price = len(rejected)

        unseen = filter_unseen(affordable, self.repo)
        summary.duplicates = len(affordable) - len(unseen)

        # Store in style order: the queue is served by insertion order, so the
        # products closest to what the channel publishes are posted first.
        ordered = unseen
        try:
            ranked = rank_products_by_channel_similarity(
                unseen,
                self.repo,
                max_price_usd=self.get_effective_max_source_price(),
                channel=self.config.telegram.channel_id,
                markup=self.config.markup,
            )
            if ranked:
                ordered = [product for product, _ in ranked]
        except Exception as exc:
            logger.warning("Channel similarity ranking skipped while collecting: %s", exc)

        for product in ordered:
            try:
                self.repo.upsert_new(product)
            except Exception as exc:
                logger.warning("Failed to store collected product %s: %s", product.external_id, exc)
                continue
            summary.stored += 1
            brand = product.source.lower()
            summary.by_brand[brand] = summary.by_brand.get(brand, 0) + 1

        logger.info(
            "=== Collected %d new products into the queue (%d scraped, %d over the price limit, %d already seen): %s ===",
            summary.stored,
            summary.fetched,
            summary.over_price,
            summary.duplicates,
            summary.by_brand or "nothing",
        )
        return summary

    def get_effective_max_source_price(self) -> Decimal:
        """Retrieve dynamic maximum source price from DB or fallback to config."""
        if hasattr(self.repo, "get_system_setting"):
            val = self.repo.get_system_setting("max_source_price_usd")
            if val:
                try:
                    return Decimal(str(val))
                except Exception:
                    pass
        return self.config.max_source_price_usd

    def publish_manual_url(
        self,
        product_url: str,
        on_platform: Callable[[str, bool, str], None] | None = None,
        bypass_duplicate_gate: bool = False,
    ) -> tuple[bool, str]:
        """Publish one admin-submitted product with the same pricing, copy, and channels as scheduled posts."""
        with self._cycle_lock:
            return self._publish_manual_url_locked(product_url, on_platform, bypass_duplicate_gate=bypass_duplicate_gate)

    def _publish_manual_url_locked(
        self,
        product_url: str,
        on_platform: Callable[[str, bool, str], None] | None = None,
        bypass_duplicate_gate: bool = False,
    ) -> tuple[bool, str]:
        try:
            self.config.reload_hot_fields()
        except Exception as exc:
            logger.warning("Failed to reload hot config fields before manual publish: %s", exc)

        # Feature 1: Pass URL through Playwright worker
        try:
            processed_url, _ = process_product_url_with_playwright(
                product_url,
                headless=self.config.scraper.headless,
            )
        except Exception as exc:
            logger.warning("Playwright URL processing fell back to input URL: %s", exc)
            processed_url = product_url

        logger.info(
            "Playwright URL processing for manual post: original='%s' -> processed='%s'",
            product_url,
            processed_url,
        )

        try:
            product = fetch_product_page(processed_url, headless=self.config.scraper.headless)
        except ProductPageError as exc:
            return False, str(exc)
        except Exception as exc:
            logger.error("Manual product fetch failed for %s: %s", processed_url, exc, exc_info=True)
            return False, f"Не удалось открыть ссылку: {exc}"

        # Attach original URL for traceability
        if getattr(product, "original_product_url", None) is None:
            try:
                object.__setattr__(product, "original_product_url", product_url)
            except Exception:
                pass

        # Feature 10: Duplicate check with Admin Approval
        signatures = extract_duplicate_signatures(
            product.source,
            product.external_id,
            product.product_url,
            product.title,
        )
        already_published = product.external_id in self.repo.get_published_ids() or bool(
            signatures & self.repo.get_published_signatures()
        )
        if already_published and not bypass_duplicate_gate:
            has_approval = False
            if hasattr(self.repo, "get_pending_duplicate_approval_by_external_id"):
                appr = self.repo.get_pending_duplicate_approval_by_external_id(product.external_id)
                if appr and appr.status == "approved":
                    has_approval = True
            if not has_approval:
                if hasattr(self.repo, "create_duplicate_approval"):
                    appr_rec = self.repo.create_duplicate_approval(
                        external_id=product.external_id,
                        title=product.title,
                        source=product.source,
                        price=f"{product.price} {product.currency}",
                        original_published_at=datetime.now(timezone.utc),
                        telegram_url=product.product_url,
                    )
                    if appr_rec:
                        self._notify_duplicate_warning(
                            approval_id=appr_rec.id,
                            product=product,
                            previous_published_at=datetime.now(timezone.utc),
                            previous_telegram_url=product.product_url,
                        )
                return False, "Этот товар уже публиковался. Запрос на подтверждение повторной публикации отправлен администратору."

        held = getattr(self.repo, "upsert_held", None)
        if held is not None:
            held(product, status="manual")
        else:
            self.repo.upsert_new(product)

        try:
            selections = self.llm.select_products(
                candidates=[product],
                prompt=self.prompt_loader.load_prompt(),
                max_items=1,
            )
        except Exception as exc:
            logger.warning("LLM copy failed for manual product %s: %s", product.external_id, exc)
            selections = []

        if not selections:
            selections = [
                SelectionResult(
                    external_id=product.external_id,
                    title=product.title,
                    description="",
                )
            ]

        selection = selections[0]
        summary = CycleSummary()
        try:
            self._process_single_product(
                product,
                selection.description,
                summary,
                title_override=selection.title,
                on_platform=on_platform,
                bypass_duplicate_gate=bypass_duplicate_gate,
            )
        except SiteFactsUnavailable as exc:
            logger.warning("Manual publish held back, %s", exc)
            return False, (
                "Магазин не отдал размерную сетку для этого товара, "
                "поэтому пост не опубликован. Попробуйте ссылку ещё раз."
            )
        except Exception as exc:
            err_msg = f"Не удалось опубликовать: {exc}"
            logger.error("Manual publish failed for %s: %s", product.external_id, exc, exc_info=True)
            try:
                self.repo.mark_failed(product.external_id, str(exc))
            except Exception:
                pass
            return False, err_msg

        if summary.published:
            if on_platform is not None:
                return True, ""
            record = None
            try:
                record = self.repo.get_by_external_id(product.external_id)
            except Exception as exc:
                logger.warning("Could not reload published manual product %s: %s", product.external_id, exc)
            title = getattr(record, "title", None) or selection.title or product.title
            price_final = getattr(record, "price_final", None)
            price_line = ""
            if price_final is not None:
                price_line = f"\nЦена: {price_final} {self.config.target_currency}"
            return True, f"Пост опубликован: {title}{price_line}"
        if summary.pending_review:
            return True, "Пост подготовлен и ожидает модерации."
        if on_platform is not None and summary.errors:
            return False, ""
        err = "; ".join(summary.errors) or "публикация не выполнена"
        return False, f"Не удалось опубликовать: {err}"

    def _run_cycle_locked(self, publishers_filter: str | None = None) -> CycleSummary:
        summary = CycleSummary()
        logger.info("=== Starting Pipeline Cycle (dry_run=%s, filter=%s) ===", self.config.dry_run, publishers_filter)

        # 1. Hot-reload operational parameters (FR-2.3, FR-3.2, FR-8.2)
        try:
            self.config.reload_hot_fields()
        except Exception as exc:
            logger.warning("Failed to reload hot config fields: %s", exc)

        # 2. Check daily publication cap (SRS §10.4)
        if self.config.daily_publish_cap is not None:
            published_today = self.repo.get_published_count_today(self.config.schedule.timezone)
            if published_today >= self.config.daily_publish_cap:
                logger.info(
                    "Daily publish cap reached (%d/%d today). Skipping cycle.",
                    published_today,
                    self.config.daily_publish_cap,
                )
                summary.skipped_daily_cap = True
                return summary
            remaining_today = self.config.daily_publish_cap - published_today
            max_to_select = min(self.config.max_products_per_run, remaining_today)
        else:
            max_to_select = self.config.max_products_per_run

        # 3. Check for unposted products in database queue first (FR-1.4 / DB queue mode)
        db_candidates: list[RawProduct] = []
        if hasattr(self.repo, "get_unposted_products"):
            try:
                db_candidates = self.repo.get_unposted_products(limit=50)
            except Exception as exc:
                logger.warning("Could not fetch unposted products from repository: %s", exc)

        # Feature 2: Filter out products belonging to paused brands
        paused_brands = self.repo.get_paused_brands() if hasattr(self.repo, "get_paused_brands") else set()
        if paused_brands and db_candidates:
            active_db = [p for p in db_candidates if p.source.lower() not in paused_brands]
            if len(active_db) < len(db_candidates):
                logger.info("Filtered out %d unposted DB candidates from paused brands", len(db_candidates) - len(active_db))
            db_candidates = active_db

        if db_candidates:
            affordable, rejected = self._split_by_source_price(db_candidates)
            for product in rejected:
                self._reject_over_source_price(product, already_stored=True)
            db_candidates = affordable

        if db_candidates:
            logger.info("Found %d unposted products in database queue. Publishing from DB.", len(db_candidates))
            raw_products = db_candidates
            summary.fetched = len(raw_products)
        else:
            logger.info("No unposted products remaining in database. Scraping catalog to replenish queue...")
            try:
                raw_products = self.source.fetch_products()
            except Exception as exc:
                err_msg = f"Failed to fetch products from source adapter: {exc}"
                logger.error(err_msg, exc_info=True)
                summary.errors.append(err_msg)
                self._notify_error("ingestion", err_msg)
                return summary

            # Feature 2: Filter scraped products by paused brands
            if paused_brands and raw_products:
                active_scraped = [p for p in raw_products if p.source.lower() not in paused_brands]
                if len(active_scraped) < len(raw_products):
                    logger.info("Filtered out %d scraped products from paused brands", len(raw_products) - len(active_scraped))
                raw_products = active_scraped

            affordable, rejected = self._split_by_source_price(raw_products)
            for product in rejected:
                self._reject_over_source_price(product, already_stored=False)
            raw_products = affordable
            summary.fetched = len(raw_products)

        if not raw_products:
            logger.info("No in-stock products returned by source adapter or database queue. Cycle complete.")
            return summary

        # 4. Deduplication against repository (FR-1.4, FR-6.2)
        unseen_products = filter_unseen(raw_products, self.repo)
        summary.unseen = len(unseen_products)
        if not unseen_products:
            logger.info("All fetched products have already been published. Cycle complete.")
            return summary

        # Feature 8: Rank unseen products based on similarity to Telegram channel history
        try:
            ranked_scored = rank_products_by_channel_similarity(
                unseen_products,
                self.repo,
                max_price_usd=self.get_effective_max_source_price(),
                channel=self.config.telegram.channel_id,
                markup=self.config.markup,
            )
            ranked_candidates = [p for p, _ in ranked_scored] if ranked_scored else unseen_products
        except Exception as exc:
            logger.warning("Channel similarity ranking skipped due to error: %s", exc)
            ranked_candidates = unseen_products

        # Feature 9: Check if outfit mode is enabled to publish 2-3 consecutive posts as a coordinated look
        outfit_mode = False
        if hasattr(self.repo, "get_system_setting"):
            outfit_mode = (self.repo.get_system_setting("outfit_mode_enabled", "false") or "").lower() == "true"

        if outfit_mode and len(ranked_candidates) >= 2:
            outfit = OutfitCoordinator.find_coordinated_outfit(ranked_candidates)
            if outfit:
                logger.info(
                    "Assembled coordinated look '%s' with %d items: %s",
                    outfit.outfit_id,
                    len(outfit.items),
                    outfit.theme,
                )
                for idx, item in enumerate(outfit.items):
                    pos_name = outfit.positions[idx] if idx < len(outfit.positions) else f"Деталь {idx+1}"
                    outfit_info = f"«{outfit.theme}» — {pos_name} ({idx+1}/{len(outfit.items)})"
                    try:
                        self._process_single_product(
                            item,
                            f"Стильный образ: {item.title}",
                            summary,
                            outfit_info=outfit_info,
                            publishers_filter=publishers_filter,
                        )
                    except AlreadyPublished as exc:
                        self._skip_already_published(exc)
                    except SiteFactsUnavailable as exc:
                        self._shelve_without_site_facts(exc)
                return summary

        # 5. Persist newly discovered products in status 'new'
        product_map: dict[str, RawProduct] = {}
        for p in ranked_candidates:
            product_map[p.external_id] = p
            try:
                self.repo.upsert_new(p)
            except Exception as exc:
                logger.warning("Failed to upsert new product %s: %s", p.external_id, exc)

        # 6. GPT selection and description generation (FR-2)
        prompt_text = self.prompt_loader.load_prompt()
        try:
            selections = self.llm.select_products(
                candidates=ranked_candidates,
                prompt=prompt_text,
                max_items=max_to_select,
            )
            summary.selected = len(selections)
        except Exception as exc:
            err_msg = f"LLM selection encountered unexpected error: {exc}"
            logger.error(err_msg, exc_info=True)
            summary.errors.append(err_msg)
            self._notify_error("selection", err_msg)
            return summary

        if not selections:
            if ranked_candidates:
                logger.info(
                    "No products chosen by LLM filter; selecting first candidate %s to maintain publishing schedule.",
                    ranked_candidates[0].external_id,
                )
                from llm.base import SelectionResult
                first = ranked_candidates[0]
                selections = [
                    SelectionResult(
                        external_id=first.external_id,
                        title=first.title,
                        description="",
                    )
                ]
                summary.selected = 1
            else:
                logger.info("No products were selected by LLM and no unseen candidates remain. Cycle complete.")
                return summary

        # 7. Process each selected product with per-product error isolation (SDD §6, NFR-3)
        for selection in selections:
            external_id = selection.external_id
            product = product_map.get(external_id)
            if not product:
                logger.warning("Selected product %s not found in candidates, skipping.", external_id)
                continue

            self._publish_first_with_site_facts(
                product,
                selection.title,
                ranked_candidates,
                summary,
                publishers_filter=publishers_filter,
            )

        # 8. Log cycle metrics summary
        summary_msg = (
            f"Cycle finished: fetched={summary.fetched}, unseen={summary.unseen}, "
            f"selected={summary.selected}, published={summary.published}, "
            f"failed={summary.failed}, pending_review={summary.pending_review}"
        )
        logger.info("=== %s ===", summary_msg)
        try:
            self.repo.log_event("INFO", "cycle_summary", summary_msg)
        except Exception as exc:
            logger.warning("Could not persist cycle summary to DB: %s", exc)

        return summary

    def _split_by_source_price(
        self,
        products: list[RawProduct],
    ) -> tuple[list[RawProduct], list[RawProduct]]:
        """Keep products whose store price, converted to USD before markup, is within the limit."""
        limit = self.get_effective_max_source_price()
        kept: list[RawProduct] = []
        rejected: list[RawProduct] = []
        for product in products:
            usd_price = source_price_usd(product.price, product.currency, self.fx)
            if usd_price > limit:
                logger.info(
                    "Skipping %s: store price %s %s is %s USD, above the %s USD limit before markup",
                    product.external_id,
                    product.price,
                    product.currency,
                    usd_price,
                    limit,
                )
                rejected.append(product)
            else:
                kept.append(product)
        if rejected:
            logger.info(
                "Dropped %d products above the %s USD source-price limit (markup is not included).",
                len(rejected),
                limit,
            )
        return kept, rejected

    def _reject_over_source_price(self, product: RawProduct, already_stored: bool) -> None:
        """Drop an over-limit product. Queued rows are marked failed so they do not block the queue."""
        if not already_stored:
            return
        try:
            usd_price = source_price_usd(product.price, product.currency, self.fx)
            self.repo.mark_failed(
                product.external_id,
                f"Store price {usd_price} USD exceeds {self.config.max_source_price_usd} USD before markup",
            )
        except Exception as exc:
            logger.warning("Could not mark over-limit product %s as failed: %s", product.external_id, exc)

    def _stored_site_description(self, external_id: str) -> str:
        """Size and color lines kept from an earlier, working read of the store page."""
        getter = getattr(self.repo, "get_by_external_id", None)
        if not callable(getter):
            return ""
        try:
            record = getter(external_id)
        except Exception as exc:
            logger.warning("Could not reload stored facts for %s: %s", external_id, exc)
            return ""
        stored = (getattr(record, "description_gpt", None) or "").strip()
        return stored if _carries_size_line(stored) else ""

    def _publish_first_with_site_facts(
        self,
        chosen: RawProduct,
        chosen_title: str,
        fallbacks: list[RawProduct],
        summary: CycleSummary,
        publishers_filter: str | None = None,
    ) -> None:
        """Publish the chosen product, or the next candidate whose page still answers.

        When a store serves a bot wall the caption would carry nothing but the
        title and the price, so that product is shelved and the slot goes to
        the next candidate rather than to a half-empty post.
        """
        queue = [(chosen, chosen_title)]
        queue.extend(
            (item, item.title)
            for item in fallbacks
            if item.external_id != chosen.external_id
        )

        for product, title in queue[:SITE_FACTS_ATTEMPTS]:
            try:
                self._process_single_product(
                    product,
                    "",
                    summary,
                    title_override=title,
                    publishers_filter=publishers_filter,
                )
                return
            except AlreadyPublished as exc:
                self._skip_already_published(exc)
            except SiteFactsUnavailable as exc:
                self._shelve_without_site_facts(exc)
            except Exception as exc:
                summary.failed += 1
                err_msg = f"Unhandled error processing product {product.external_id}: {exc}"
                logger.error(err_msg, exc_info=True)
                summary.errors.append(err_msg)
                try:
                    self.repo.mark_failed(product.external_id, err_msg)
                except Exception:
                    pass
                self._notify_error("processing", err_msg, product.external_id)
                return

        err_msg = (
            f"No candidate out of {len(queue[:SITE_FACTS_ATTEMPTS])} returned its size grid; "
            "nothing was published rather than posting a caption without sizes."
        )
        logger.error(err_msg)
        summary.errors.append(err_msg)
        self._notify_error("site_facts", err_msg)

    def _skip_already_published(self, exc: AlreadyPublished) -> None:
        """Hand the slot to the next candidate and tell the admin which product was skipped."""
        logger.info("Not publishing %s, the channel has carried it before", exc.external_id)
        if self.notifier and hasattr(self.notifier, "notify_duplicate_skipped"):
            try:
                self.notifier.notify_duplicate_skipped(title=exc.title, product_url=exc.product_url)
            except Exception as notify_exc:
                logger.warning("Failed to send the duplicate-skipped notice: %s", notify_exc)

    def _shelve_without_site_facts(self, exc: SiteFactsUnavailable) -> None:
        """Take a product the store would not describe out of the publishing queue."""
        logger.warning("Not publishing %s", exc)
        summary_msg = f"Skipped, the store page gave no size grid: {exc.product_url or ''}".strip()
        try:
            self.repo.mark_failed(exc.external_id, summary_msg)
        except Exception as mark_exc:
            logger.warning("Could not shelve %s: %s", exc.external_id, mark_exc)

    def _russian_title(self, product: RawProduct, title_override: str | None) -> str:
        """The caption names the garment in Russian, whichever store language the page used.

        Most paths arrive with copy the model already wrote. The ones that
        publish straight from the queue (admin «опубликовать сейчас», a repeat
        post, a coordinated look) carry only the store title, and the channel
        reads Russian, so that title is translated here rather than in each
        caller.
        """
        chosen = (title_override or "").strip() or product.title
        if _reads_in_russian(chosen):
            return chosen

        translated = self._translated_title(product)
        if translated:
            return translated

        logger.warning(
            "Posting %s under its store title '%s': no Russian name came back",
            product.external_id,
            chosen,
        )
        return chosen

    def _translated_title(self, product: RawProduct) -> str:
        """Ask the model for this one product's Russian name, or "" if it cannot."""
        try:
            selections = self.llm.select_products(
                candidates=[product],
                prompt=self.prompt_loader.load_prompt(),
                max_items=1,
            )
        except Exception as exc:
            logger.warning("Could not translate the title of %s: %s", product.external_id, exc)
            return ""

        for selection in selections:
            candidate = (selection.title or "").strip()
            if _reads_in_russian(candidate):
                return candidate
        return ""

    def _process_single_product(
        self,
        product: RawProduct,
        description: str,
        summary: CycleSummary,
        title_override: str | None = None,
        on_platform: Callable[[str, bool, str], None] | None = None,
        publishers_filter: str | None = None,
        outfit_info: str | None = None,
        bypass_duplicate_gate: bool = False,
    ) -> bool:
        """Process price calculation, moderation, composition, and publishing for one item."""
        external_id = product.external_id
        product = attach_site_facts(product)
        product_title = self._russian_title(product, title_override)
        # Size, color, and heel height are copied from the product page. The model must not fill them in.
        heel = product.heel_height if is_heeled_footwear(product.title, product.product_url) else None
        description = site_description(product.color, product.sizes, heel_height=heel)
        if not order_sizes(product.sizes):
            # A bot wall answers with a script shell that carries no size grid.
            # Instagram reposts what Telegram already carries, so it can fall
            # back to the lines read on that earlier, working visit.
            description = self._stored_site_description(external_id)
            if not description:
                raise SiteFactsUnavailable(external_id, product.product_url)

        # 7a. Calculate final price (FR-3.1, FR-3.2, FR-3.3)
        final_price = calculate_final_price(
            original_price=product.price,
            currency=product.currency,
            markup=self.config.markup,
            target_currency=self.config.target_currency,
            fx=self.fx,
        )

        # 7b. Update status to 'selected'
        self.repo.mark_selected(external_id, description, final_price, title=product_title)

        # 7c. Moderation gate check (SRS §10.2)
        if not self.moderation_gate.should_publish(external_id):
            self.repo.mark_pending_review(external_id)
            summary.pending_review += 1
            logger.info("Product %s held in 'pending_review' per ModerationGate.", external_id)
            return False

        # Collect all gallery photos for the product card. Related products on the
        # same page (complete the look, other colourways) are not part of this post.
        from core.gallery import arrange_carousel, extract_gallery_photos, keep_single_product

        photo_urls = list(product.photo_urls) if getattr(product, "photo_urls", None) else []
        if len(photo_urls) <= 1 and product.product_url:
            try:
                gallery = extract_gallery_photos(product.product_url, brand=product.source, max_photos=10)
                if gallery:
                    photo_urls = gallery
            except Exception as exc:
                logger.debug("Failed to extract gallery photos from %s: %s", product.product_url, exc)

        if not photo_urls and product.photo_url:
            photo_urls = [product.photo_url]
        photo_urls = keep_single_product(photo_urls, product.product_url or "")
        photo_urls = arrange_carousel(photo_urls, max_photos=10)

        # Download all photos locally for binary posting & multi-photo albums
        downloaded_paths = []
        try:
            downloaded_paths = self.image_downloader.download_all(photo_urls, external_id=external_id)
        except Exception as exc:
            logger.warning("Failed to download gallery photos for %s: %s", external_id, exc)

        photo_to_use = str(downloaded_paths[0]) if downloaded_paths else product.photo_url
        downloaded_strings = [str(p) for p in downloaded_paths] if downloaded_paths else photo_urls

        # 7d. Compose platform-agnostic post (FR-4)
        composed = compose_post(
            product=RawProduct(
                external_id=product.external_id,
                source=product.source,
                title=product_title,
                price=product.price,
                currency=product.currency,
                photo_url=photo_to_use,
                product_url=product.product_url,
                in_stock=product.in_stock,
                photo_urls=downloaded_strings,
                original_product_url=getattr(product, "original_product_url", None),
                heel_height=getattr(product, "heel_height", None),
                color=product.color,
                sizes=product.sizes,
            ),
            description=description,
            price=final_price,
            target_currency=self.config.target_currency,
            include_link=self.config.include_product_link,
            outfit_info=outfit_info,
        )

        # Feature 10: Duplicate publication check and Admin approval gate
        sigs = extract_duplicate_signatures(product.source, external_id, product.product_url, product.title)
        published_sigs = getattr(self.repo, "get_published_signatures", lambda: set())()
        is_already_in_channel = (external_id in self.repo.get_published_ids()) or bool(sigs & published_sigs)

        if is_already_in_channel and not bypass_duplicate_gate:
            approved = False
            if hasattr(self.repo, "get_pending_duplicate_approval_by_external_id"):
                appr = self.repo.get_pending_duplicate_approval_by_external_id(external_id)
                if appr and appr.status == "approved":
                    approved = True

            if not approved:
                prev_record = self.repo.get_by_external_id(external_id) if hasattr(self.repo, "get_by_external_id") else None
                prev_date = getattr(prev_record, "published_at", None) or getattr(prev_record, "telegram_published_at", None)
                prev_url = getattr(prev_record, "telegram_message_url", None) or getattr(prev_record, "product_url", None)

                if hasattr(self.repo, "create_duplicate_approval"):
                    appr_rec = self.repo.create_duplicate_approval(
                        external_id=external_id,
                        title=product_title,
                        source=product.source,
                        price=f"{product.price} {product.currency}",
                        original_published_at=prev_date,
                        telegram_url=prev_url,
                    )
                    if appr_rec:
                        self._notify_duplicate_warning(
                            approval_id=appr_rec.id,
                            product=product,
                            previous_published_at=prev_date,
                            previous_telegram_url=prev_url,
                        )

                raise AlreadyPublished(external_id, product_title, product.product_url)

        # Filter to only currently enabled publishers, taking publishers_filter into account
        active_publishers = [
            p for p in self.publishers
            if (p.platform_name.lower() == "telegram" and self.config.telegram.enabled)
            or (p.platform_name.lower() == "instagram" and self.config.instagram.enabled)
            or (p.platform_name.lower() not in ("telegram", "instagram"))
        ]

        if publishers_filter:
            active_publishers = [p for p in active_publishers if p.platform_name.lower() == publishers_filter.lower()]

        if not active_publishers:
            err_msg = f"No active publishers enabled to publish product {external_id} (filter={publishers_filter})."
            logger.warning(err_msg)
            self.repo.mark_failed(external_id, err_msg)
            summary.failed += 1
            return False

        # Check existing publication records to prevent re-posting on partial retries
        existing_record = None
        try:
            existing_record = self.repo.get_by_external_id(external_id)
        except Exception as exc:
            logger.warning("Could not fetch existing record for %s: %s", external_id, exc)

        is_instagram_followup = bool(publishers_filter and publishers_filter.lower() == "instagram")
        ignore_existing_posts = bypass_duplicate_gate and not is_instagram_followup
        existing_post_ids = {
            "telegram": getattr(existing_record, "telegram_post_id", None) if (existing_record and not ignore_existing_posts) else None,
            "instagram": getattr(existing_record, "instagram_post_id", None) if (existing_record and not ignore_existing_posts) else None,
        }

        # Instagram Story links to the Telegram message of this same product.
        telegram_url = _telegram_link_for(existing_record)
        if telegram_url:
            composed = replace(composed, telegram_links=(telegram_url,))

        # 7e. Publish to each active configured channel (FR-5)
        telegram_post_id: str | None = existing_post_ids.get("telegram")
        instagram_post_id: str | None = existing_post_ids.get("instagram")
        all_succeeded = True
        publish_errors: list[str] = []

        # Enforce publication order: Telegram MUST publish first so its message URL can link the Instagram Story
        ordered_publishers = sorted(
            active_publishers,
            key=lambda p: 0 if p.platform_name.lower() == "telegram" else 1,
        )

        for publisher in ordered_publishers:
            pub_name = publisher.platform_name.lower()
            already_id = existing_post_ids.get(pub_name)

            if already_id and not ignore_existing_posts:
                logger.info(
                    "Product %s was already published to %s (post_id=%s). Skipping duplicate publish.",
                    external_id,
                    pub_name,
                    already_id,
                )
                continue

            # Ensure publisher has access to repo and llm for AI highlight selection & story worker
            if hasattr(publisher, "repo") and getattr(publisher, "repo", None) is None:
                setattr(publisher, "repo", self.repo)
            if hasattr(publisher, "llm") and getattr(publisher, "llm", None) is None:
                setattr(publisher, "llm", self.llm)
            if pub_name == "instagram" and hasattr(publisher, "story_worker") and getattr(publisher, "story_worker", None) is None:
                try:
                    from publishers.playwright_story_worker import PlaywrightStoryWorker
                    publisher.story_worker = PlaywrightStoryWorker(
                        repo=self.repo,
                        llm=self.llm,
                        account_id=getattr(publisher, "account_id", "instagram"),
                        private_story=getattr(publisher, "private_story", None),
                    )
                except Exception as exc:
                    logger.debug("Could not initialize story_worker on Instagram publisher: %s", exc)

            res = publisher.publish(composed)

            if res.success:
                if pub_name == "telegram":
                    telegram_post_id = res.platform_post_id
                    telegram_url = res.links[0] if (res.links and len(res.links) > 0) else None
                    if hasattr(self.repo, "save_telegram_publication") and telegram_url:
                        try:
                            self.repo.save_telegram_publication(external_id, telegram_post_id, telegram_url)
                        except Exception as exc:
                            logger.warning("Could not save Telegram publication for %s: %s", external_id, exc)
                    if res.links:
                        composed = replace(composed, telegram_links=tuple(res.links))
                elif pub_name == "instagram":
                    instagram_post_id = res.platform_post_id
                logger.info(
                    "Published %s to %s (post_id=%s)",
                    external_id,
                    pub_name,
                    res.platform_post_id,
                )
                # Persist platform post ID immediately so subsequent retries never duplicate
                try:
                    self.repo.update_platform_post_id(external_id, pub_name, res.platform_post_id)
                except Exception as exc:
                    logger.warning("Could not save platform post ID for %s (%s): %s", external_id, pub_name, exc)
                if on_platform is not None and pub_name in {"telegram", "instagram"}:
                    on_platform(pub_name, True, "")
            else:
                all_succeeded = False
                err_msg = f"{pub_name} publish failed for {external_id}: {res.error}"
                logger.error(err_msg)
                publish_errors.append(err_msg)
                self._notify_error(f"publish_{pub_name}", err_msg, external_id)
                if on_platform is not None and pub_name in {"telegram", "instagram"}:
                    on_platform(pub_name, False, str(res.error or "неизвестная ошибка"))

                # Failure handling requirement:
                # If Telegram publication fails: STOP -> Do not create Instagram Story!
                if pub_name == "telegram":
                    logger.warning("Telegram publish failed for %s. Aborting workflow before Instagram Story.", external_id)
                    self.repo.mark_failed(external_id, err_msg)
                    summary.failed += 1
                    summary.errors.append(err_msg)
                    return

        # 7f. Persist publication status in repository (FR-6.1)
        if all_succeeded:
            self.repo.mark_published(external_id, telegram_post_id, instagram_post_id)
            summary.published += 1
            logger.info("Product %s marked published in database.", external_id)
        else:
            joined_errors = "; ".join(publish_errors)
            self.repo.mark_failed(external_id, joined_errors)
            summary.failed += 1
            summary.errors.extend(publish_errors)

    def _notify_error(self, stage: str, message: str, external_id: str | None = None) -> None:
        """Send critical failure notification if notifier is configured."""
        if self.notifier:
            try:
                self.notifier.notify_critical(stage, message, external_id)
            except Exception as exc:
                logger.warning("Failed to send admin notification: %s", exc)

    def _notify_duplicate_warning(
        self,
        approval_id: int,
        product: RawProduct,
        previous_published_at: datetime | None,
        previous_telegram_url: str | None,
    ) -> None:
        """Send duplicate publication warning and approval request to admin bot."""
        if self.notifier and hasattr(self.notifier, "notify_duplicate_warning"):
            try:
                prev_date_str = (
                    previous_published_at.strftime("%Y-%m-%d %H:%M UTC")
                    if previous_published_at
                    else "Ранее"
                )
                self.notifier.notify_duplicate_warning(
                    approval_id=approval_id,
                    title=product.title,
                    source=product.source,
                    price=f"{product.price} {product.currency}",
                    previous_date=prev_date_str,
                    previous_url=previous_telegram_url,
                    proposed_date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                )
            except Exception as exc:
                logger.warning("Failed to send duplicate warning alert: %s", exc)

    def publish_approved_duplicate(self, approval_id: int) -> tuple[bool, str]:
        """Admin approved a repeat publication: resolve approval and publish with duplicate bypass."""
        with self._cycle_lock:
            return self._publish_approved_duplicate_locked(approval_id)

    def _publish_approved_duplicate_locked(self, approval_id: int) -> tuple[bool, str]:
        if hasattr(self.repo, "resolve_duplicate_approval"):
            self.repo.resolve_duplicate_approval(approval_id, "approved")
        rec = None
        if hasattr(self.repo, "get_duplicate_approval"):
            rec = self.repo.get_duplicate_approval(approval_id)
        if not rec:
            return False, f"Duplicate approval #{approval_id} not found"

        product = None
        existing = self.repo.get_by_external_id(rec.external_id) if hasattr(self.repo, "get_by_external_id") else None
        if existing:
            product = RawProduct(
                external_id=existing.external_id,
                source=existing.source,
                title=existing.title,
                price=existing.price_original,
                currency=existing.currency_original,
                photo_url=existing.photo_url,
                product_url=existing.product_url,
                in_stock=True,
                original_product_url=getattr(existing, "original_product_url", None),
                heel_height=getattr(existing, "heel_height", None),
                sizes=getattr(existing, "sizes", ()),
                color=getattr(existing, "color", None),
            )
        elif getattr(rec, "telegram_url", None):
            return self._publish_manual_url_locked(rec.telegram_url, bypass_duplicate_gate=True)

        if not product:
            return False, f"Товар {rec.external_id} не найден в базе для повторной публикации"

        summary = CycleSummary()
        try:
            self._process_single_product(
                product,
                description=getattr(existing, "description_gpt", None) or f"Повторный показ: {product.title}",
                summary=summary,
                bypass_duplicate_gate=True,
            )
            if summary.published:
                return True, f"Повторный пост опубликован: {product.title}"
            err = "; ".join(summary.errors) or "публикация не выполнена"
            return False, f"Не удалось опубликовать: {err}"
        except Exception as exc:
            logger.error("Failed to publish approved duplicate %s: %s", rec.external_id, exc)
            return False, str(exc)

    def _publish_next_eligible_product_locked(self, publishers_filter: str | None = None) -> tuple[bool, str]:
        """Iterate unposted products in database queue, verifying brand pause, max price,
        and duplicate check, and publish the first eligible product.
        If a duplicate is encountered, triggers duplicate approval and continues."""
        if not hasattr(self.repo, "get_unposted_products"):
            return False, "База данных не поддерживает очередь товаров"

        candidates = self.repo.get_unposted_products(limit=50)
        paused_brands = self.repo.get_paused_brands() if hasattr(self.repo, "get_paused_brands") else set()
        max_price = self.get_effective_max_source_price()
        published_ids = self.repo.get_published_ids()
        published_sigs = getattr(self.repo, "get_published_signatures", lambda: set())()

        for candidate in candidates:
            # 1. Brand pause check
            if candidate.source.lower() in paused_brands:
                continue

            # 2. Max price check
            usd_price = source_price_usd(candidate.price, candidate.currency, self.fx)
            if usd_price > max_price:
                self._reject_over_source_price(candidate, already_stored=True)
                continue

            # 3. Duplicate check
            sigs = extract_duplicate_signatures(candidate.source, candidate.external_id, candidate.product_url, candidate.title)
            if (candidate.external_id in published_ids) or bool(sigs & published_sigs):
                pending = getattr(self.repo, "get_pending_duplicate_approval_by_external_id", lambda x: None)(candidate.external_id)
                if not pending:
                    prev_rec = self.repo.get_by_external_id(candidate.external_id) if hasattr(self.repo, "get_by_external_id") else None
                    prev_date = getattr(prev_rec, "published_at", None) or getattr(prev_rec, "telegram_published_at", None)
                    prev_url = getattr(prev_rec, "telegram_message_url", None) or getattr(prev_rec, "product_url", None)
                    if hasattr(self.repo, "create_duplicate_approval"):
                        appr_rec = self.repo.create_duplicate_approval(
                            external_id=candidate.external_id,
                            title=candidate.title,
                            source=candidate.source,
                            price=f"{candidate.price} {candidate.currency}",
                            original_published_at=prev_date,
                            telegram_url=prev_url,
                        )
                        if appr_rec:
                            self._notify_duplicate_warning(
                                approval_id=appr_rec.id,
                                product=candidate,
                                previous_published_at=prev_date,
                                previous_telegram_url=prev_url,
                            )
                self._skip_already_published(
                    AlreadyPublished(candidate.external_id, candidate.title, candidate.product_url)
                )
                continue

            # Eligible product found! Publish it
            summary = CycleSummary()
            try:
                self._process_single_product(
                    candidate,
                    description="",
                    summary=summary,
                    publishers_filter=publishers_filter,
                )
                if summary.published:
                    return True, f"Опубликован следующий подходящий товар: {candidate.title}"
            except AlreadyPublished as exc:
                self._skip_already_published(exc)
                continue
            except SiteFactsUnavailable as exc:
                self._shelve_without_site_facts(exc)
                continue
            except Exception as exc:
                logger.error("Failed to publish candidate %s: %s", candidate.external_id, exc)
                continue

        return False, "В базе нет подходящих товаров для публикации."

