"""Repository interface and SQLAlchemy implementation.

Per SDD §3.7 and SRS FR-6 (Anti-duplicate tracking).
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable
import zoneinfo
from sqlalchemy import create_engine, select, update, func
from sqlalchemy.orm import Session, sessionmaker

from adapters.base import RawProduct
from storage.models import Base, LogRecord, ProductRecord, TelegramChatRecord


@runtime_checkable
class ProductRepository(Protocol):
    """Abstract interface for product persistence and duplicate checking."""

    def get_published_ids(self) -> set[str]:
        """Return the set of all external_ids that have already been published."""
        ...

    def get_published_count_today(self, timezone_str: str = "UTC") -> int:
        """Count products published today in the specified timezone (for daily cap check)."""
        ...

    def upsert_new(self, product: RawProduct) -> None:
        """Store newly ingested candidate product with status 'new' if not already present."""
        ...

    def mark_selected(self, external_id: str, description: str, price_final: Decimal) -> None:
        """Update candidate with GPT description and final price, set status to 'selected'."""
        ...

    def mark_published(self, external_id: str, telegram_id: str | None, instagram_id: str | None) -> None:
        """Mark product as published with social media post IDs and current timestamp."""
        ...

    def mark_failed(self, external_id: str, error: str) -> None:
        """Mark product publication as failed, recording error details."""
        ...

    def mark_pending_review(self, external_id: str) -> None:
        """Mark product as held in moderation queue."""
        ...

    def log_event(self, level: str, stage: str, message: str, external_id: str | None = None) -> None:
        """Record structured pipeline event in the logs table."""
        ...

    def get_by_external_id(self, external_id: str) -> Optional[ProductRecord]:
        """Retrieve single product record by external_id."""
        ...

    def get_active_telegram_targets(self) -> list["TelegramChatRecord"]:
        """Return all active Telegram channels/groups registered for product publishing."""
        ...

    def get_active_admin_chats(self) -> list["TelegramChatRecord"]:
        """Return all active Telegram chat IDs registered to receive critical failure alerts."""
        ...

    def get_all_telegram_chats(self) -> list["TelegramChatRecord"]:
        """Return all discovered or configured Telegram chats."""
        ...

    def upsert_telegram_chat(
        self,
        chat_id: str,
        title: str,
        chat_type: str,
        role: str = "publish_target",
        username: str | None = None,
        is_active: bool = True,
    ) -> "TelegramChatRecord":
        """Add or update a Telegram chat registration."""
        ...

    def deactivate_telegram_chat(self, chat_id: str) -> None:
        """Deactivate a Telegram chat target."""
        ...


class SqlAlchemyProductRepository:
    """Production implementation of ProductRepository backed by SQLAlchemy."""

    def __init__(self, db_url: str):
        self.db_url = db_url

        # Ensure sqlite parent directory exists if using local sqlite file
        if db_url.startswith("sqlite:///"):
            raw_path = db_url.replace("sqlite:///", "")
            if not raw_path.startswith(":memory:"):
                db_file = Path(raw_path)
                db_file.parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(db_url, echo=False, future=True)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _get_session(self) -> Session:
        return self.SessionLocal()

    def get_published_ids(self) -> set[str]:
        with self._get_session() as session:
            stmt = select(ProductRecord.external_id).where(ProductRecord.status == "published")
            results = session.scalars(stmt).all()
            return set(results)

    def get_published_count_today(self, timezone_str: str = "UTC") -> int:
        try:
            tz = zoneinfo.ZoneInfo(timezone_str)
        except Exception:
            tz = timezone.utc

        now_in_tz = datetime.now(tz)
        start_of_day = now_in_tz.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_day_utc = start_of_day.astimezone(timezone.utc)

        with self._get_session() as session:
            stmt = select(func.count(ProductRecord.id)).where(
                ProductRecord.status == "published",
                ProductRecord.published_at >= start_of_day_utc,
            )
            count = session.scalar(stmt)
            return count or 0

    def upsert_new(self, product: RawProduct) -> None:
        with self._get_session() as session:
            existing = session.scalar(
                select(ProductRecord).where(ProductRecord.external_id == product.external_id)
            )
            if existing is None:
                record = ProductRecord(
                    external_id=product.external_id,
                    source=product.source,
                    title=product.title,
                    price_original=product.price,
                    currency_original=product.currency,
                    photo_url=product.photo_url,
                    product_url=product.product_url,
                    status="new",
                )
                session.add(record)
                session.commit()

    def mark_selected(self, external_id: str, description: str, price_final: Decimal) -> None:
        with self._get_session() as session:
            stmt = (
                update(ProductRecord)
                .where(ProductRecord.external_id == external_id)
                .values(
                    description_gpt=description,
                    price_final=price_final,
                    status="selected",
                )
            )
            session.execute(stmt)
            session.commit()

    def mark_published(self, external_id: str, telegram_id: str | None, instagram_id: str | None) -> None:
        with self._get_session() as session:
            stmt = (
                update(ProductRecord)
                .where(ProductRecord.external_id == external_id)
                .values(
                    telegram_post_id=telegram_id,
                    instagram_post_id=instagram_id,
                    status="published",
                    published_at=datetime.now(timezone.utc),
                )
            )
            session.execute(stmt)
            session.commit()

    def mark_failed(self, external_id: str, error: str) -> None:
        with self._get_session() as session:
            stmt = (
                update(ProductRecord)
                .where(ProductRecord.external_id == external_id)
                .values(status="failed")
            )
            session.execute(stmt)
            # Log failure in logs table as well
            log_record = LogRecord(
                level="ERROR",
                stage="publish",
                external_id=external_id,
                message=f"Publication failed: {error}",
            )
            session.add(log_record)
            session.commit()

    def mark_pending_review(self, external_id: str) -> None:
        with self._get_session() as session:
            stmt = (
                update(ProductRecord)
                .where(ProductRecord.external_id == external_id)
                .values(status="pending_review")
            )
            session.execute(stmt)
            session.commit()

    def log_event(self, level: str, stage: str, message: str, external_id: str | None = None) -> None:
        with self._get_session() as session:
            log_record = LogRecord(
                level=level.upper(),
                stage=stage,
                external_id=external_id,
                message=message,
            )
            session.add(log_record)
            session.commit()

    def get_by_external_id(self, external_id: str) -> Optional[ProductRecord]:
        with self._get_session() as session:
            return session.scalar(
                select(ProductRecord).where(ProductRecord.external_id == external_id)
            )

    def get_active_telegram_targets(self) -> list[TelegramChatRecord]:
        with self._get_session() as session:
            stmt = select(TelegramChatRecord).where(
                TelegramChatRecord.role == "publish_target",
                TelegramChatRecord.is_active == True,
            )
            return list(session.scalars(stmt).all())

    def get_active_admin_chats(self) -> list[TelegramChatRecord]:
        with self._get_session() as session:
            stmt = select(TelegramChatRecord).where(
                TelegramChatRecord.role == "admin_alert",
                TelegramChatRecord.is_active == True,
            )
            return list(session.scalars(stmt).all())

    def get_all_telegram_chats(self) -> list[TelegramChatRecord]:
        with self._get_session() as session:
            stmt = select(TelegramChatRecord).order_by(TelegramChatRecord.created_at.desc())
            return list(session.scalars(stmt).all())

    def upsert_telegram_chat(
        self,
        chat_id: str,
        title: str,
        chat_type: str,
        role: str = "publish_target",
        username: str | None = None,
        is_active: bool = True,
    ) -> TelegramChatRecord:
        with self._get_session() as session:
            existing = session.scalar(
                select(TelegramChatRecord).where(TelegramChatRecord.chat_id == str(chat_id))
            )
            if existing:
                existing.title = title
                existing.chat_type = chat_type
                existing.role = role
                if username is not None:
                    existing.username = username
                existing.is_active = is_active
                existing.updated_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(existing)
                return existing
            else:
                new_chat = TelegramChatRecord(
                    chat_id=str(chat_id),
                    title=title,
                    chat_type=chat_type,
                    role=role,
                    username=username,
                    is_active=is_active,
                )
                session.add(new_chat)
                session.commit()
                session.refresh(new_chat)
                return new_chat

    def deactivate_telegram_chat(self, chat_id: str) -> None:
        with self._get_session() as session:
            stmt = (
                update(TelegramChatRecord)
                .where(TelegramChatRecord.chat_id == str(chat_id))
                .values(is_active=False, updated_at=datetime.now(timezone.utc))
            )
            session.execute(stmt)
            session.commit()
