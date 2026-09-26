"""Shared test fixtures and test doubles (fakes) for unit and integration testing.

Per SDD §9.
"""

from decimal import Decimal
from typing import Optional
import pytest

from adapters.base import RawProduct, SourceAdapter
from core.composer import ComposedPost
from llm.base import LLMProvider, SelectionResult
from publishers.base import Publisher, PublishResult
from storage.models import ProductRecord
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
                "status": "new",
                "description": None,
                "price_final": None,
            }

    def mark_selected(self, external_id: str, description: str, price_final: Decimal) -> None:
        if external_id in self.products:
            self.products[external_id]["status"] = "selected"
            self.products[external_id]["description"] = description
            self.products[external_id]["price_final"] = price_final

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

    def get_by_external_id(self, external_id: str) -> Optional[ProductRecord]:
        data = self.products.get(external_id)
        if not data:
            return None
        rec = ProductRecord(
            id=1,
            external_id=data["external_id"],
            source=data["source"],
            title=data["title"],
            price_original=data["price"],
            currency_original=data["currency"],
            price_final=data.get("price_final"),
            photo_url="https://example.com/photo.jpg",
            description_gpt=data.get("description"),
            status=data["status"],
            telegram_post_id=data.get("telegram_post_id"),
            instagram_post_id=data.get("instagram_post_id"),
        )
        return rec


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
