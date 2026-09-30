"""SQLAlchemy models for fashion-autopost.

Matches SRS §6.1 / SDD §4.1 (`products` table) and SDD §4.2 (`logs` table).
Supports both SQLite and PostgreSQL without code modification.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProductRecord(Base):
    """Database entity tracking all ingested, selected, and published products."""
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    price_original: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency_original: Mapped[str] = mapped_column(String(16), nullable=False)
    price_final: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    photo_url: Mapped[str] = mapped_column(Text, nullable=False)
    description_gpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_product_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    heel_height: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    outfit_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    outfit_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Status: 'new' | 'selected' | 'pending_review' | 'published' | 'failed'
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    
    telegram_post_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    instagram_post_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    telegram_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_message_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_products_status", "status"),
        Index("idx_products_published_at", "published_at"),
        Index("idx_products_outfit", "outfit_id"),
    )

    @property
    def name(self) -> str:
        return self.title

    @property
    def description(self) -> str:
        return self.description_gpt or ""

    @property
    def image(self) -> str:
        return self.photo_url

    @property
    def telegramMessageId(self) -> Optional[str]:
        return self.telegram_message_id or self.telegram_post_id

    @property
    def telegramMessageUrl(self) -> Optional[str]:
        return self.telegram_message_url

    @property
    def telegramPublishedAt(self) -> Optional[datetime]:
        return self.telegram_published_at or self.published_at


class StoryJobRecord(Base):
    """Tracks Instagram Story publishing linked to Telegram message."""
    __tablename__ = "story_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    instagram_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    highlight_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    story_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # status: 'pending' | 'story_published' | 'highlight_failed' | 'completed' | 'failed'
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_story_jobs_status", "status"),
        Index("idx_story_jobs_product", "product_id"),
    )

    @property
    def productId(self) -> int:
        return self.product_id

    @property
    def instagramAccountId(self) -> str:
        return self.instagram_account_id

    @property
    def highlightName(self) -> Optional[str]:
        return self.highlight_name

    @property
    def storyId(self) -> Optional[str]:
        return self.story_id


class LogRecord(Base):
    """Database sink for structured pipeline activity logs (SDD §4.2)."""
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)  # INFO, WARNING, ERROR, CRITICAL
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class TelegramChatRecord(Base):
    """Database entity tracking dynamically discovered Telegram channels, groups, and admin chats."""
    __tablename__ = "telegram_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 'channel', 'supergroup', 'group', 'private'
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="publish_target")  # 'publish_target', 'admin_alert'
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_telegram_chats_role_active", "role", "is_active"),
    )


class ManualPostRecord(Base):
    """Admin-submitted product link waiting for a publish time or already scheduled."""

    __tablename__ = "manual_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_product_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    publish_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # awaiting_time | scheduled | publishing | published | failed | cancelled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="awaiting_time", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class BrandSettingRecord(Base):
    """Stores brand active/paused states persistently in database."""
    __tablename__ = "brand_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_paused: Mapped[bool] = mapped_column(nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SystemSettingRecord(Base):
    """General key-value persistent configuration settings in database."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DuplicateApprovalRecord(Base):
    """Tracks duplicate products requiring Admin approval prior to repeat publication."""
    __tablename__ = "duplicate_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    original_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # status: 'pending' | 'approved' | 'rejected'
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

