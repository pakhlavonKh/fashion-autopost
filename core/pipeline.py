"""Pipeline runner orchestrating the end-to-end publishing cycle.

Per SDD §2.2, §5, §6 and SRS FR-1 through FR-9.
Guarantees per-product error isolation, idempotency, hot config reloading,
and comprehensive audit logging.
"""

from dataclasses import dataclass, field
import logging
from typing import Any, Protocol, runtime_checkable

from adapters.base import RawProduct, SourceAdapter
from config.app_config import AppConfig
from core.composer import compose_post
from core.dedup import filter_unseen
from core.image_downloader import ImageDownloader
from core.moderation import ConfigurableModerationGate, ModerationGate
from core.pricing import FxConverter, calculate_final_price
from llm.base import LLMProvider, PromptLoader
from publishers.base import Publisher
from storage.repository import ProductRepository

logger = logging.getLogger(__name__)


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

    def run_cycle(self) -> CycleSummary:
        """Execute one complete publishing cycle."""
        summary = CycleSummary()
        logger.info("=== Starting Pipeline Cycle (dry_run=%s) ===", self.config.dry_run)

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

        # 3. Ingestion from aggregator source (FR-1.1, FR-1.2)
        try:
            raw_products = self.source.fetch_products()
            summary.fetched = len(raw_products)
        except Exception as exc:
            err_msg = f"Failed to fetch products from source adapter: {exc}"
            logger.error(err_msg, exc_info=True)
            summary.errors.append(err_msg)
            self._notify_error("ingestion", err_msg)
            return summary

        if not raw_products:
            logger.info("No in-stock products returned by source adapter. Cycle complete.")
            return summary

        # 4. Deduplication against repository (FR-1.4, FR-6.2)
        unseen_products = filter_unseen(raw_products, self.repo)
        summary.unseen = len(unseen_products)
        if not unseen_products:
            logger.info("All fetched products have already been published. Cycle complete.")
            return summary

        # 5. Persist newly discovered products in status 'new'
        product_map: dict[str, RawProduct] = {}
        for p in unseen_products:
            product_map[p.external_id] = p
            try:
                self.repo.upsert_new(p)
            except Exception as exc:
                logger.warning("Failed to upsert new product %s: %s", p.external_id, exc)

        # 6. GPT selection and description generation (FR-2)
        prompt_text = self.prompt_loader.load_prompt()
        try:
            selections = self.llm.select_products(
                candidates=unseen_products,
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
            logger.info("No products were selected by LLM. Cycle complete.")
            return summary

        # 7. Process each selected product with per-product error isolation (SDD §6, NFR-3)
        for selection in selections:
            external_id = selection.external_id
            product = product_map.get(external_id)
            if not product:
                logger.warning("Selected product %s not found in candidates, skipping.", external_id)
                continue

            try:
                self._process_single_product(product, selection.description, summary)
            except Exception as exc:
                summary.failed += 1
                err_msg = f"Unhandled error processing product {external_id}: {exc}"
                logger.error(err_msg, exc_info=True)
                summary.errors.append(err_msg)
                try:
                    self.repo.mark_failed(external_id, err_msg)
                except Exception:
                    pass
                self._notify_error("processing", err_msg, external_id)

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

    def _process_single_product(
        self,
        product: RawProduct,
        description: str,
        summary: CycleSummary,
    ) -> None:
        """Process price calculation, moderation, composition, and publishing for one item."""
        external_id = product.external_id

        # 7a. Calculate final price (FR-3.1, FR-3.2, FR-3.3)
        final_price = calculate_final_price(
            original_price=product.price,
            currency=product.currency,
            markup=self.config.markup,
            target_currency=self.config.target_currency,
            fx=self.fx,
        )

        # 7b. Update status to 'selected'
        self.repo.mark_selected(external_id, description, final_price)

        # 7c. Moderation gate check (SRS §10.2)
        if not self.moderation_gate.should_publish(external_id):
            self.repo.mark_pending_review(external_id)
            summary.pending_review += 1
            logger.info("Product %s held in 'pending_review' per ModerationGate.", external_id)
            return

        # Pre-download photo locally for binary posting & local caching
        local_path = None
        try:
            local_path = self.image_downloader.download(product.photo_url, external_id=external_id)
        except Exception as exc:
            logger.warning("Failed to pre-download photo for %s: %s", external_id, exc)

        photo_to_use = str(local_path) if (local_path and local_path.is_file()) else product.photo_url

        # 7d. Compose platform-agnostic post (FR-4)
        composed = compose_post(
            product=RawProduct(
                external_id=product.external_id,
                source=product.source,
                title=product.title,
                price=product.price,
                currency=product.currency,
                photo_url=photo_to_use,
                product_url=product.product_url,
                in_stock=product.in_stock,
            ),
            description=description,
            price=final_price,
            target_currency=self.config.target_currency,
            include_link=self.config.include_product_link,
        )

        # Double check idempotency right before publishing (FR-6.2)
        if external_id in self.repo.get_published_ids():
            logger.warning("Product %s was published concurrently! Skipping duplicate.", external_id)
            return

        # 7e. Publish to each configured channel (FR-5)
        telegram_post_id: str | None = None
        instagram_post_id: str | None = None
        all_succeeded = True
        publish_errors: list[str] = []

        for publisher in self.publishers:
            pub_name = publisher.platform_name.lower()
            res = publisher.publish(composed)

            if res.success:
                if pub_name == "telegram":
                    telegram_post_id = res.platform_post_id
                elif pub_name == "instagram":
                    instagram_post_id = res.platform_post_id
                logger.info(
                    "Published %s to %s (post_id=%s)",
                    external_id,
                    pub_name,
                    res.platform_post_id,
                )
            else:
                all_succeeded = False
                err_msg = f"{pub_name} publish failed for {external_id}: {res.error}"
                logger.error(err_msg)
                publish_errors.append(err_msg)
                self._notify_error(f"publish_{pub_name}", err_msg, external_id)

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
