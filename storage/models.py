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
    price_original: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency_original: Mapped[str] = mapped_column(String(16), nullable=False)
    price_final: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    photo_url: Mapped[str] = mapped_column(Text, nullable=False)
    description_gpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Status: 'new' | 'selected' | 'pending_review' | 'published' | 'failed'
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    
    telegram_post_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    instagram_post_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_products_status", "status"),
        Index("idx_products_published_at", "published_at"),
    )


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
