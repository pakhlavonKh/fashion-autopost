"""Repository interface and SQLAlchemy implementation.

Per SDD §3.7 and SRS FR-6 (Anti-duplicate tracking).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable
import zoneinfo
from sqlalchemy import create_engine, delete, select, update, func
from sqlalchemy.orm import Session, sessionmaker

from adapters.base import RawProduct
from storage.models import (
    Base,
    BrandSettingRecord,
    DuplicateApprovalRecord,
    LogRecord,
    ManualPostRecord,
    ProductRecord,
    StoryJobRecord,
    SystemSettingRecord,
    TelegramChatRecord,
)


@dataclass
class ManualPost:
    """Detached view of an admin-scheduled product link."""

    id: int
    admin_user_id: str
    chat_id: str
    product_url: str
    publish_at: datetime | None
    status: str
    error: str | None
    original_product_url: str | None = None


@runtime_checkable
class ProductRepository(Protocol):
    """Abstract interface for product persistence and duplicate checking."""

    def get_published_ids(self) -> set[str]:
        """Return the set of all external_ids that have already been published."""
        ...

    def get_published_signatures(self) -> set[str]:
        """Return the set of all duplicate signatures (IDs, canonical URLs, SKUs) for published products."""
        ...

    def get_unposted_products(self, limit: int = 50) -> list[RawProduct]:
        """Return candidate products from the database that have not been published yet."""
        ...

    def get_published_count_today(self, timezone_str: str = "UTC") -> int:
        """Count products published today in the specified timezone (for daily cap check)."""
        ...

    def upsert_new(self, product: RawProduct) -> None:
        """Store newly ingested candidate product with status 'new' if not already present."""
        ...

    def mark_selected(self, external_id: str, description: str, price_final: Decimal, title: str | None = None) -> None:
        """Update candidate with GPT description, final price, and optional translated title, set status to 'selected'."""
        ...

    def mark_published(self, external_id: str, telegram_id: str | None, instagram_id: str | None) -> None:
        """Mark product as published with social media post IDs and current timestamp."""
        ...

    def update_platform_post_id(self, external_id: str, platform: str, post_id: str) -> None:
        """Update platform post ID (e.g. telegram_post_id or instagram_post_id) immediately upon success."""
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

    def get_product(self, product_id: int) -> Optional[ProductRecord]:
        """Retrieve single product record by primary key id."""
        ...

    def save_telegram_publication(self, external_id: str, message_id: str, message_url: str) -> None:
        """Store Telegram message ID, exact message URL, and publish timestamp against product."""
        ...

    def create_story_job(self, product_id: int, instagram_account_id: str, highlight_name: str | None = None) -> "StoryJobRecord":
        """Create a new story job referencing the product and highlight."""
        ...

    def update_story_job(
        self,
        job_id: int,
        status: str,
        story_id: str | None = None,
        highlight_name: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update story job status, story_id, highlight_name, or error."""
        ...

    def get_story_jobs(self, product_id: int | None = None, status: str | None = None) -> list["StoryJobRecord"]:
        """Retrieve story jobs by product or status."""
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

    def upsert_held(self, product: RawProduct, status: str = "manual") -> None:
        """Store a product outside the automatic queue so a scheduled post is not published early."""
        ...

    def get_brand_settings(self) -> list[dict[str, Any]]:
        """Return all configured brand active/paused states."""
        ...

    def set_brand_paused(self, name: str, is_paused: bool) -> None:
        """Pause or unpause a brand from automated publication."""
        ...

    def is_brand_paused(self, name: str) -> bool:
        """Check whether a brand is currently paused."""
        ...

    def get_paused_brands(self) -> set[str]:
        """Return the set of all currently paused brand names."""
        ...

    def get_system_setting(self, key: str, default: str | None = None) -> str | None:
        """Get a persistent system configuration setting by key."""
        ...

    def set_system_setting(self, key: str, value: str, description: str | None = None) -> None:
        """Set a persistent system configuration setting."""
        ...

    def get_all_system_settings(self) -> dict[str, str]:
        """Return all persistent system configuration settings as a key-value dictionary."""
        ...

    def create_duplicate_approval(
        self,
        external_id: str,
        title: str,
        source: str,
        price: str | None = None,
        original_published_at: datetime | None = None,
        telegram_url: str | None = None,
    ) -> "DuplicateApprovalRecord":
        """Record a duplicate publication requiring admin approval."""
        ...

    def get_duplicate_approval(self, approval_id: int) -> Optional["DuplicateApprovalRecord"]:
        """Retrieve a duplicate approval record by ID."""
        ...

    def get_pending_duplicate_approval_by_external_id(self, external_id: str) -> Optional["DuplicateApprovalRecord"]:
        """Check if an external_id has a pending duplicate approval."""
        ...

    def resolve_duplicate_approval(self, approval_id: int, status: str) -> bool:
        """Resolve a duplicate approval request ('approved' or 'rejected')."""
        ...

    def list_duplicate_approvals(self, status: str | None = None) -> list["DuplicateApprovalRecord"]:
        """List duplicate approval records."""
        ...

    def get_recent_published_products(self, limit: int = 50) -> list["ProductRecord"]:
        """Fetch recently published products from the channel for style/compatibility analysis."""
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
        self._ensure_columns()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _ensure_columns(self) -> None:
        """Add newly introduced columns to existing SQLite tables if not already present."""
        if not self.db_url.startswith("sqlite"):
            return
        try:
            with self.engine.connect() as conn:
                res = conn.exec_driver_sql("PRAGMA table_info(products)")
                cols = {row[1] for row in res.fetchall()}
                if "category" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN category VARCHAR(128)")
                if "original_product_url" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN original_product_url TEXT")
                if "heel_height" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN heel_height VARCHAR(32)")
                if "outfit_id" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN outfit_id VARCHAR(64)")
                if "outfit_position" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN outfit_position INTEGER")
                if "telegram_message_id" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN telegram_message_id VARCHAR(255)")
                if "telegram_message_url" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN telegram_message_url TEXT")
                if "telegram_published_at" not in cols:
                    conn.exec_driver_sql("ALTER TABLE products ADD COLUMN telegram_published_at DATETIME")

                mres = conn.exec_driver_sql("PRAGMA table_info(manual_posts)")
                mcols = {row[1] for row in mres.fetchall()}
                if "original_product_url" not in mcols:
                    conn.exec_driver_sql("ALTER TABLE manual_posts ADD COLUMN original_product_url TEXT")
                conn.commit()
        except Exception as exc:
            logger.warning("Auto-migration in _ensure_columns encountered: %s", exc)

    def _get_session(self) -> Session:
        return self.SessionLocal()

    def get_published_ids(self) -> set[str]:
        with self._get_session() as session:
            stmt = select(ProductRecord.external_id).where(ProductRecord.status == "published")
            results = session.scalars(stmt).all()
            return set(results)

    def get_published_signatures(self) -> set[str]:
        from core.dedup import extract_duplicate_signatures
        with self._get_session() as session:
            stmt = select(
                ProductRecord.external_id,
                ProductRecord.source,
                ProductRecord.product_url,
                ProductRecord.title,
            ).where(ProductRecord.status == "published")
            rows = session.execute(stmt).all()
            signatures: set[str] = set()
            for ext_id, source, prod_url, title in rows:
                signatures.update(
                    extract_duplicate_signatures(
                        source=source,
                        external_id=ext_id,
                        product_url=prod_url,
                        title=title,
                    )
                )
            return signatures

    def get_unposted_products(self, limit: int = 50) -> list[RawProduct]:
        with self._get_session() as session:
            stmt = (
                select(ProductRecord)
                .where(
                    (ProductRecord.status == "new")
                    & (ProductRecord.telegram_post_id.is_(None))
                )
                .order_by(ProductRecord.id.asc())
                .limit(limit)
            )
            records = session.scalars(stmt).all()
            products: list[RawProduct] = []
            for r in records:
                products.append(
                    RawProduct(
                        external_id=r.external_id,
                        source=r.source,
                        title=r.title,
                        price=r.price_original,
                        currency=r.currency_original,
                        photo_url=r.photo_url,
                        product_url=r.product_url or "",
                        in_stock=True,
                    )
                )
            return products

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
                    original_product_url=getattr(product, "original_product_url", None) or product.product_url,
                    heel_height=getattr(product, "heel_height", None),
                    status="new",
                )
                session.add(record)
                session.commit()

    def mark_selected(self, external_id: str, description: str, price_final: Decimal, title: str | None = None) -> None:
        with self._get_session() as session:
            values: dict[str, Any] = {
                "description_gpt": description,
                "price_final": price_final,
                "status": "selected",
            }
            if title:
                values["title"] = title
            stmt = (
                update(ProductRecord)
                .where(ProductRecord.external_id == external_id)
                .values(**values)
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

    def update_platform_post_id(self, external_id: str, platform: str, post_id: str) -> None:
        """Update platform post ID immediately upon platform publish success to prevent duplicates on retries."""
        with self._get_session() as session:
            values = {}
            if platform.lower() == "telegram":
                values["telegram_post_id"] = post_id
            elif platform.lower() == "instagram":
                values["instagram_post_id"] = post_id
            if values:
                stmt = (
                    update(ProductRecord)
                    .where(ProductRecord.external_id == external_id)
                    .values(**values)
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

    def delete_if_unpublished(self, external_id: str) -> str:
        """Remove a product that has not been published.

        Returns ``deleted``, ``missing``, or ``published``.
        A product is treated as published when its status is published or a
        platform post id is still stored.
        """
        with self._get_session() as session:
            record = session.scalar(
                select(ProductRecord).where(ProductRecord.external_id == external_id)
            )
            if record is None:
                return "missing"
            if record.status == "published" or record.telegram_post_id or record.instagram_post_id:
                return "published"
            session.execute(delete(LogRecord).where(LogRecord.external_id == external_id))
            session.delete(record)
            session.commit()
            return "deleted"

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

    def get_product(self, product_id: int) -> Optional[ProductRecord]:
        with self._get_session() as session:
            return session.scalar(
                select(ProductRecord).where(ProductRecord.id == product_id)
            )

    def save_telegram_publication(self, external_id: str, message_id: str, message_url: str) -> None:
        """Store Telegram message ID, exact message URL, and publish timestamp against product."""
        with self._get_session() as session:
            now = datetime.now(timezone.utc)
            stmt = (
                update(ProductRecord)
                .where(ProductRecord.external_id == external_id)
                .values(
                    telegram_message_id=message_id,
                    telegram_message_url=message_url,
                    telegram_published_at=now,
                    telegram_post_id=message_id,
                )
            )
            session.execute(stmt)
            session.commit()

    def create_story_job(
        self, product_id: int, instagram_account_id: str, highlight_name: str | None = None
    ) -> StoryJobRecord:
        with self._get_session() as session:
            job = StoryJobRecord(
                product_id=product_id,
                instagram_account_id=instagram_account_id,
                highlight_name=highlight_name,
                status="pending",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            session.expunge(job)
            return job

    def update_story_job(
        self,
        job_id: int,
        status: str,
        story_id: str | None = None,
        highlight_name: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._get_session() as session:
            values: dict[str, Any] = {"status": status}
            if story_id is not None:
                values["story_id"] = story_id
            if highlight_name is not None:
                values["highlight_name"] = highlight_name
            if error is not None:
                values["error"] = error
            stmt = (
                update(StoryJobRecord)
                .where(StoryJobRecord.id == job_id)
                .values(**values)
            )
            session.execute(stmt)
            session.commit()

    def get_story_jobs(
        self, product_id: int | None = None, status: str | None = None
    ) -> list[StoryJobRecord]:
        with self._get_session() as session:
            stmt = select(StoryJobRecord)
            if product_id is not None:
                stmt = stmt.where(StoryJobRecord.product_id == product_id)
            if status is not None:
                stmt = stmt.where(StoryJobRecord.status == status)
            records = list(session.scalars(stmt).all())
            for r in records:
                session.expunge(r)
            return records

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

    def upsert_held(self, product: RawProduct, status: str = "manual") -> None:
        """Store a product outside the automatic queue so a scheduled post is not published early."""
        with self._get_session() as session:
            existing = session.scalar(
                select(ProductRecord).where(ProductRecord.external_id == product.external_id)
            )
            if existing is None:
                session.add(
                    ProductRecord(
                        external_id=product.external_id,
                        source=product.source,
                        title=product.title,
                        price_original=product.price,
                        currency_original=product.currency,
                        photo_url=product.photo_url,
                        product_url=product.product_url,
                        status=status,
                    )
                )
            elif existing.status != "published":
                existing.source = product.source
                existing.title = product.title
                existing.price_original = product.price
                existing.currency_original = product.currency
                existing.photo_url = product.photo_url
                existing.product_url = product.product_url
                existing.status = status
            session.commit()

    def create_manual_draft(
        self,
        admin_user_id: str,
        chat_id: str,
        product_url: str,
        original_product_url: str | None = None,
    ) -> ManualPost:
        """Replace this admin's unanswered link with a new one waiting for a publish time."""
        with self._get_session() as session:
            waiting = session.scalars(
                select(ManualPostRecord).where(
                    ManualPostRecord.admin_user_id == str(admin_user_id),
                    ManualPostRecord.status == "awaiting_time",
                )
            ).all()
            for row in waiting:
                row.status = "cancelled"
                row.updated_at = datetime.now(timezone.utc)
            record = ManualPostRecord(
                admin_user_id=str(admin_user_id),
                chat_id=str(chat_id),
                product_url=product_url,
                original_product_url=original_product_url or product_url,
                status="awaiting_time",
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _manual_post_from_row(record)

    def get_awaiting_manual(self, admin_user_id: str) -> ManualPost | None:
        with self._get_session() as session:
            record = session.scalar(
                select(ManualPostRecord)
                .where(
                    ManualPostRecord.admin_user_id == str(admin_user_id),
                    ManualPostRecord.status == "awaiting_time",
                )
                .order_by(ManualPostRecord.id.desc())
            )
            return _manual_post_from_row(record) if record else None

    def cancel_awaiting_manual(self, admin_user_id: str) -> bool:
        with self._get_session() as session:
            rows = session.scalars(
                select(ManualPostRecord).where(
                    ManualPostRecord.admin_user_id == str(admin_user_id),
                    ManualPostRecord.status == "awaiting_time",
                )
            ).all()
            if not rows:
                return False
            now = datetime.now(timezone.utc)
            for row in rows:
                row.status = "cancelled"
                row.updated_at = now
            session.commit()
            return True

    def schedule_manual_post(self, post_id: int, publish_at: datetime) -> ManualPost | None:
        with self._get_session() as session:
            record = session.get(ManualPostRecord, post_id)
            if record is None:
                return None
            record.publish_at = publish_at
            record.status = "scheduled"
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
            return _manual_post_from_row(record)

    def get_manual_post(self, post_id: int) -> ManualPost | None:
        with self._get_session() as session:
            record = session.get(ManualPostRecord, post_id)
            return _manual_post_from_row(record) if record else None

    def set_manual_post_status(self, post_id: int, status: str, error: str | None = None) -> None:
        with self._get_session() as session:
            record = session.get(ManualPostRecord, post_id)
            if record is None:
                return
            record.status = status
            record.error = error
            record.updated_at = datetime.now(timezone.utc)
            session.commit()

    def list_manual_posts(self, statuses: list[str]) -> list[ManualPost]:
        with self._get_session() as session:
            rows = session.scalars(
                select(ManualPostRecord)
                .where(ManualPostRecord.status.in_(statuses))
                .order_by(ManualPostRecord.id.asc())
            ).all()
            return [_manual_post_from_row(row) for row in rows]

    def find_open_manual_by_url(self, product_url: str) -> ManualPost | None:
        from core.dedup import normalize_url

        target = normalize_url(product_url)
        if not target:
            return None
        with self._get_session() as session:
            rows = session.scalars(
                select(ManualPostRecord).where(
                    ManualPostRecord.status.in_(("awaiting_time", "scheduled", "publishing"))
                )
            ).all()
            for row in rows:
                if normalize_url(row.product_url) == target:
                    return _manual_post_from_row(row)
            return None

    def upsert_held(self, product: RawProduct, status: str = "manual") -> None:
        """Store a product outside the automatic queue so a scheduled post is not published early."""
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
                    original_product_url=getattr(product, "original_product_url", None) or product.product_url,
                    heel_height=getattr(product, "heel_height", None),
                    status=status,
                )
                session.add(record)
            else:
                existing.status = status
                if getattr(product, "original_product_url", None):
                    existing.original_product_url = product.original_product_url
                if getattr(product, "heel_height", None):
                    existing.heel_height = product.heel_height
            session.commit()

    def get_brand_settings(self) -> list[dict[str, Any]]:
        with self._get_session() as session:
            rows = session.scalars(select(BrandSettingRecord).order_by(BrandSettingRecord.name.asc())).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "display_name": r.display_name,
                    "is_paused": r.is_paused,
                    "status": "Paused" if r.is_paused else "Active",
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ]

    def set_brand_paused(self, name: str, is_paused: bool) -> None:
        norm = name.strip().lower()
        with self._get_session() as session:
            existing = session.scalar(select(BrandSettingRecord).where(BrandSettingRecord.name == norm))
            now = datetime.now(timezone.utc)
            if existing:
                existing.is_paused = is_paused
                existing.updated_at = now
            else:
                record = BrandSettingRecord(
                    name=norm,
                    display_name=norm.capitalize(),
                    is_paused=is_paused,
                    updated_at=now,
                )
                session.add(record)
            session.commit()

    def is_brand_paused(self, name: str) -> bool:
        norm = name.strip().lower()
        with self._get_session() as session:
            val = session.scalar(select(BrandSettingRecord.is_paused).where(BrandSettingRecord.name == norm))
            return bool(val)

    def get_paused_brands(self) -> set[str]:
        with self._get_session() as session:
            rows = session.scalars(
                select(BrandSettingRecord.name).where(BrandSettingRecord.is_paused == True)
            ).all()
            return {r.lower() for r in rows}

    def get_system_setting(self, key: str, default: str | None = None) -> str | None:
        with self._get_session() as session:
            val = session.scalar(select(SystemSettingRecord.value).where(SystemSettingRecord.key == key))
            return val if val is not None else default

    def set_system_setting(self, key: str, value: str, description: str | None = None) -> None:
        with self._get_session() as session:
            existing = session.get(SystemSettingRecord, key)
            now = datetime.now(timezone.utc)
            if existing:
                existing.value = str(value)
                if description:
                    existing.description = description
                existing.updated_at = now
            else:
                record = SystemSettingRecord(
                    key=key,
                    value=str(value),
                    description=description,
                    updated_at=now,
                )
                session.add(record)
            session.commit()

    def get_all_system_settings(self) -> dict[str, str]:
        with self._get_session() as session:
            rows = session.scalars(select(SystemSettingRecord)).all()
            return {r.key: r.value for r in rows}

    def create_duplicate_approval(
        self,
        external_id: str,
        title: str,
        source: str,
        price: str | None = None,
        original_published_at: datetime | None = None,
        telegram_url: str | None = None,
    ) -> DuplicateApprovalRecord:
        with self._get_session() as session:
            now = datetime.now(timezone.utc)
            record = DuplicateApprovalRecord(
                external_id=external_id,
                title=title,
                source=source,
                price=price,
                original_published_at=original_published_at,
                telegram_url=telegram_url,
                status="pending",
                created_at=now,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_duplicate_approval(self, approval_id: int) -> DuplicateApprovalRecord | None:
        with self._get_session() as session:
            return session.get(DuplicateApprovalRecord, approval_id)

    def get_pending_duplicate_approval_by_external_id(self, external_id: str) -> DuplicateApprovalRecord | None:
        with self._get_session() as session:
            return session.scalar(
                select(DuplicateApprovalRecord)
                .where(
                    DuplicateApprovalRecord.external_id == external_id,
                    DuplicateApprovalRecord.status == "pending",
                )
                .order_by(DuplicateApprovalRecord.id.desc())
            )

    def resolve_duplicate_approval(self, approval_id: int, status: str) -> bool:
        if status not in {"approved", "rejected"}:
            raise ValueError(f"Invalid approval status: {status}")
        with self._get_session() as session:
            record = session.get(DuplicateApprovalRecord, approval_id)
            if not record:
                return False
            record.status = status
            record.resolved_at = datetime.now(timezone.utc)
            session.commit()
            return True

    def list_duplicate_approvals(self, status: str | None = None) -> list[DuplicateApprovalRecord]:
        with self._get_session() as session:
            stmt = select(DuplicateApprovalRecord).order_by(DuplicateApprovalRecord.id.desc())
            if status:
                stmt = stmt.where(DuplicateApprovalRecord.status == status)
            return list(session.scalars(stmt).all())

    def get_recent_published_products(self, limit: int = 50) -> list[ProductRecord]:
        with self._get_session() as session:
            stmt = (
                select(ProductRecord)
                .where(
                    ProductRecord.status == "published",
                    ProductRecord.telegram_message_url.is_not(None),
                )
                .order_by(ProductRecord.published_at.desc())
                .limit(limit)
            )
            return list(session.scalars(stmt).all())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _manual_post_from_row(record: ManualPostRecord) -> ManualPost:
    return ManualPost(
        id=record.id,
        admin_user_id=record.admin_user_id,
        chat_id=record.chat_id,
        product_url=record.product_url,
        publish_at=_as_utc(record.publish_at),
        status=record.status,
        error=record.error,
        original_product_url=record.original_product_url or record.product_url,
    )
