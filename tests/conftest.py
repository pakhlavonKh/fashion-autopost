"""Shared test fixtures and test doubles (fakes) for unit and integration testing.

Per SDD §9.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import pytest

from adapters.base import RawProduct, SourceAdapter
from core.composer import ComposedPost
from llm.base import HighlightSelectionResult, LLMProvider, SelectionResult
from publishers.base import Publisher, PublishResult
from storage.models import (
    BrandSettingRecord,
    DuplicateApprovalRecord,
    ProductRecord,
    StoryJobRecord,
    SystemSettingRecord,
)
from storage.repository import ProductRepository


class FakeProductRepository:
    """In-memory fake implementation of ProductRepository protocol."""

    def __init__(self) -> None:
        self.products: dict[str, dict] = {}
        self.logs: list[dict] = []

    def get_published_ids(self) -> set[str]:
        return {
            ext_id
            for ext_id, data in self.products.items()
            if data.get("status") == "published"
        }

    def get_published_signatures(self) -> set[str]:
        from core.dedup import extract_duplicate_signatures
        signatures: set[str] = set()
        for ext_id, data in self.products.items():
            if data.get("status") == "published":
                signatures.update(
                    extract_duplicate_signatures(
                        source=data.get("source", ""),
                        external_id=ext_id,
                        product_url=data.get("product_url"),
                        title=data.get("title"),
                    )
                )
        return signatures

    def get_unposted_products(self, limit: int = 50) -> list[RawProduct]:
        candidates: list[RawProduct] = []
        for ext_id, data in self.products.items():
            if data.get("status") == "new" and not data.get("telegram_post_id"):
                candidates.append(
                    RawProduct(
                        external_id=ext_id,
                        source=data.get("source", "generic"),
                        title=data.get("title", ""),
                        price=data.get("price", Decimal("0.00")),
                        currency=data.get("currency", "USD"),
                        photo_url=data.get("photo_url", "https://example.com/photo.jpg"),
                        product_url=data.get("product_url", ""),
                        in_stock=True,
                    )
                )
                if len(candidates) >= limit:
                    break
        return candidates

    def get_published_count_today(self, timezone_str: str = "UTC") -> int:
        return len(self.get_published_ids())

    def upsert_new(self, product: RawProduct) -> None:
        if product.external_id not in self.products:
            self.products[product.external_id] = {
                "external_id": product.external_id,
                "source": product.source,
                "title": product.title,
                "price": product.price,
                "currency": product.currency,
                "product_url": product.product_url,
                "photo_url": product.photo_url,
                "status": "new",
                "description": None,
                "price_final": None,
            }

    def upsert_held(self, product: RawProduct, status: str = "manual") -> None:
        existing = self.products.get(product.external_id)
        if existing and existing.get("status") == "published":
            return
        self.products[product.external_id] = {
            "external_id": product.external_id,
            "source": product.source,
            "title": product.title,
            "price": product.price,
            "currency": product.currency,
            "product_url": product.product_url,
            "photo_url": product.photo_url,
            "status": status,
            "description": existing.get("description") if existing else None,
            "price_final": existing.get("price_final") if existing else None,
            "telegram_post_id": existing.get("telegram_post_id") if existing else None,
            "instagram_post_id": existing.get("instagram_post_id") if existing else None,
        }

    def mark_selected(self, external_id: str, description: str, price_final: Decimal, title: str | None = None) -> None:
        if external_id in self.products:
            self.products[external_id]["status"] = "selected"
            self.products[external_id]["description"] = description
            self.products[external_id]["price_final"] = price_final
            if title:
                self.products[external_id]["title"] = title

    def mark_published(self, external_id: str, telegram_id: str | None, instagram_id: str | None) -> None:
        if external_id in self.products:
            self.products[external_id]["status"] = "published"
            self.products[external_id]["telegram_post_id"] = telegram_id
            self.products[external_id]["instagram_post_id"] = instagram_id

    def update_platform_post_id(self, external_id: str, platform: str, post_id: str) -> None:
        if external_id in self.products:
            if platform.lower() == "telegram":
                self.products[external_id]["telegram_post_id"] = post_id
            elif platform.lower() == "instagram":
                self.products[external_id]["instagram_post_id"] = post_id

    def mark_failed(self, external_id: str, error: str) -> None:
        if external_id in self.products:
            self.products[external_id]["status"] = "failed"
            self.products[external_id]["error"] = error

    def mark_pending_review(self, external_id: str) -> None:
        if external_id in self.products:
            self.products[external_id]["status"] = "pending_review"

    def log_event(self, level: str, stage: str, message: str, external_id: str | None = None) -> None:
        self.logs.append({
            "level": level,
            "stage": stage,
            "message": message,
            "external_id": external_id,
        })

    def save_telegram_publication(self, external_id: str, message_id: str, message_url: str) -> None:
        if external_id in self.products:
            self.products[external_id]["telegram_message_id"] = message_id
            self.products[external_id]["telegram_message_url"] = message_url
            self.products[external_id]["telegram_post_id"] = message_id
            self.products[external_id]["telegram_published_at"] = datetime.now(timezone.utc)

    def get_by_external_id(self, external_id: str) -> Optional[ProductRecord]:
        data = self.products.get(external_id)
        if not data:
            return None
        rec = ProductRecord(
            id=data.get("id", 1),
            external_id=data["external_id"],
            source=data["source"],
            title=data["title"],
            price_original=data["price"],
            currency_original=data["currency"],
            price_final=data.get("price_final"),
            photo_url=data.get("photo_url", "https://example.com/photo.jpg"),
            description_gpt=data.get("description"),
            status=data["status"],
            telegram_post_id=data.get("telegram_post_id"),
            instagram_post_id=data.get("instagram_post_id"),
            telegram_message_id=data.get("telegram_message_id") or data.get("telegram_post_id"),
            telegram_message_url=data.get("telegram_message_url"),
            telegram_published_at=data.get("telegram_published_at"),
        )
        return rec

    def get_product(self, product_id: int) -> Optional[ProductRecord]:
        for data in self.products.values():
            if data.get("id", 1) == product_id:
                return self.get_by_external_id(data["external_id"])
        # Fallback to first if only one exists and matches default id
        if self.products and product_id == 1:
            first_ext = next(iter(self.products))
            return self.get_by_external_id(first_ext)
        return None

    def create_story_job(
        self, product_id: int, instagram_account_id: str, highlight_name: str | None = None
    ) -> StoryJobRecord:
        job = StoryJobRecord(
            id=len(getattr(self, "story_jobs", [])) + 1,
            product_id=product_id,
            instagram_account_id=instagram_account_id,
            highlight_name=highlight_name,
            status="pending",
        )
        if not hasattr(self, "story_jobs"):
            self.story_jobs = []
        self.story_jobs.append(job)
        return job

    def update_story_job(
        self,
        job_id: int,
        status: str,
        story_id: str | None = None,
        highlight_name: str | None = None,
        error: str | None = None,
    ) -> None:
        jobs = getattr(self, "story_jobs", [])
        for job in jobs:
            if job.id == job_id:
                job.status = status
                if story_id is not None:
                    job.story_id = story_id
                if highlight_name is not None:
                    job.highlight_name = highlight_name
                if error is not None:
                    job.error = error
                break

    def get_story_jobs(
        self, product_id: int | None = None, status: str | None = None
    ) -> list[StoryJobRecord]:
        jobs = getattr(self, "story_jobs", [])
        res = list(jobs)
        if product_id is not None:
            res = [j for j in res if j.product_id == product_id]
        if status is not None:
            res = [j for j in res if j.status == status]
        return res

    def get_brand_settings(self) -> list[dict]:
        brands = getattr(self, "_brands", {})
        return [
            {"id": idx + 1, "name": b, "display_name": b.capitalize(), "is_paused": paused, "status": "Paused" if paused else "Active"}
            for idx, (b, paused) in enumerate(brands.items())
        ]

    def set_brand_paused(self, name: str, is_paused: bool) -> None:
        if not hasattr(self, "_brands"):
            self._brands = {}
        self._brands[name.strip().lower()] = is_paused

    def is_brand_paused(self, name: str) -> bool:
        brands = getattr(self, "_brands", {})
        return bool(brands.get(name.strip().lower(), False))

    def get_paused_brands(self) -> set[str]:
        brands = getattr(self, "_brands", {})
        return {b for b, paused in brands.items() if paused}

    def get_system_setting(self, key: str, default: str | None = None) -> str | None:
        settings = getattr(self, "_settings", {})
        return settings.get(key, default)

    def set_system_setting(self, key: str, value: str, description: str | None = None) -> None:
        if not hasattr(self, "_settings"):
            self._settings = {}
        self._settings[key] = str(value)

    def get_all_system_settings(self) -> dict[str, str]:
        return dict(getattr(self, "_settings", {}))

    def create_duplicate_approval(
        self,
        external_id: str,
        title: str,
        source: str,
        price: str | None = None,
        original_published_at: datetime | None = None,
        telegram_url: str | None = None,
    ) -> DuplicateApprovalRecord:
        if not hasattr(self, "_approvals"):
            self._approvals = []
        rec = DuplicateApprovalRecord(
            id=len(self._approvals) + 1,
            external_id=external_id,
            title=title,
            source=source,
            price=price,
            original_published_at=original_published_at,
            telegram_url=telegram_url,
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        self._approvals.append(rec)
        return rec

    def get_duplicate_approval(self, approval_id: int) -> DuplicateApprovalRecord | None:
        approvals = getattr(self, "_approvals", [])
        return next((a for a in approvals if a.id == approval_id), None)

    def get_pending_duplicate_approval_by_external_id(self, external_id: str) -> DuplicateApprovalRecord | None:
        approvals = getattr(self, "_approvals", [])
        return next((a for a in approvals if a.external_id == external_id and a.status == "pending"), None)

    def resolve_duplicate_approval(self, approval_id: int, status: str) -> bool:
        rec = self.get_duplicate_approval(approval_id)
        if rec:
            rec.status = status
            rec.resolved_at = datetime.now(timezone.utc)
            return True
        return False

    def list_duplicate_approvals(self, status: str | None = None) -> list[DuplicateApprovalRecord]:
        approvals = getattr(self, "_approvals", [])
        if status:
            return [a for a in approvals if a.status == status]
        return list(approvals)

    def get_recent_published_products(self, limit: int = 50) -> list[ProductRecord]:
        res: list[ProductRecord] = []
        for ext_id, data in self.products.items():
            if data.get("status") == "published":
                rec = self.get_by_external_id(ext_id)
                if rec:
                    res.append(rec)
        return res[:limit]


class FakeSourceAdapter:
    """In-memory fake SourceAdapter."""

    def __init__(self, products: list[RawProduct] | None = None) -> None:
        self.products = products or []

    def fetch_products(self) -> list[RawProduct]:
        return list(self.products)


class FakeLLMProvider:
    """In-memory fake LLMProvider."""

    def __init__(self, select_count: int = 2) -> None:
        self.select_count = select_count

    def select_products(
        self,
        candidates: list[RawProduct],
        prompt: str,
        max_items: int,
    ) -> list[SelectionResult]:
        count = min(self.select_count, max_items, len(candidates))
        return [
            SelectionResult(
                external_id=c.external_id,
                description=f"Fake stylish copy for {c.title}",
            )
            for c in candidates[:count]
        ]

    def select_highlight(
        self, product: dict, existing_highlights: list[str]
    ) -> HighlightSelectionResult:
        name = str(product.get("name") or product.get("title") or "")
        cat = str(product.get("category") or "")
        for eh in existing_highlights:
            if (eh.lower() in name.lower()) or (cat and eh.lower() in cat.lower()):
                return HighlightSelectionResult(highlight=eh, confidence=0.96)
        return HighlightSelectionResult(
            highlight=None,
            create_highlight=True,
            suggested_name=cat or "New Arrivals",
            confidence=0.90,
        )


class FakePublisher:
    """In-memory fake Publisher tracking published posts."""

    def __init__(self, name: str = "fake_pub", should_fail: bool = False) -> None:
        self._name = name
        self.should_fail = should_fail
        self.published_posts: list[ComposedPost] = []

    @property
    def platform_name(self) -> str:
        return self._name

    def publish(self, post: ComposedPost) -> PublishResult:
        if self.should_fail:
            return PublishResult(success=False, error=f"{self._name} simulated failure")
        self.published_posts.append(post)
        return PublishResult(success=True, platform_post_id=f"{self._name}_{len(self.published_posts)}")


@pytest.fixture
def fake_repo() -> FakeProductRepository:
    return FakeProductRepository()


@pytest.fixture
def sample_products() -> list[RawProduct]:
    return [
        RawProduct(
            external_id="p-1",
            source="zara",
            title="Linen Blend Trousers",
            price=Decimal("49.95"),
            currency="EUR",
            photo_url="https://images.example.com/p1.jpg",
            product_url="https://zara.com/p1",
            in_stock=True,
        ),
        RawProduct(
            external_id="p-2",
            source="mango",
            title="Cotton Poplin Shirt",
            price=Decimal("39.99"),
            currency="USD",
            photo_url="https://images.example.com/p2.jpg",
            product_url="https://mango.com/p2",
            in_stock=True,
        ),
        RawProduct(
            external_id="p-3",
            source="zara",
            title="Suede Overshirt",
            price=Decimal("89.90"),
            currency="EUR",
            photo_url="https://images.example.com/p3.jpg",
            product_url="https://zara.com/p3",
            in_stock=True,
        ),
    ]
